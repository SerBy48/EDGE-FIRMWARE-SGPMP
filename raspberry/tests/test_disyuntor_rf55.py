"""Disyuntor de RF-55.2 ante fallas de evaluación (TC-M03-088, TC-M03-097)."""

import logging

import pytest
from conftest import Harness
from test_agent import ack_all, con_umbral

from edge_agent.config import ConfigError, FakeVariable, Settings
from edge_agent.config_store import Umbral
from edge_agent.reglas import disyuntor as dis
from edge_agent.reglas import evaluador
from edge_agent.reglas.disyuntor import Disyuntor
from edge_agent.reglas.perfil import Habilitacion
from edge_agent.reglas.store import Regla

FALLA = RuntimeError("falla")
THI = Regla(12, 3, {"nombre": "THI", "prevalece_sobre_regla_simple": True})
# T fuera de todas las bandas del umbral (simple CRITICO) y THI ≥ 84 (compuesta).
AMBIENTE = (
    FakeVariable("temperatura_ambiente", "C", 32, 33),
    FakeVariable("humedad_relativa", "%", 75, 76),
)


# --- Disyuntor ---------------------------------------------------------------------


def test_fallas_transitorias_no_deshabilitan():
    d = Disyuntor(fallos_max=3, reintento_s=600)
    assert d.registrar(FALLA, 0) == dis.TRANSITORIO
    assert d.registrar(FALLA, 60) == dis.TRANSITORIO
    assert d.registrar(None, 120) is None  # una captura sana reinicia la cuenta
    assert (d.estado, d.fallos_consecutivos) == (dis.ACTIVO, 0)
    assert d.registrar(FALLA, 180) == dis.TRANSITORIO


def test_la_tercera_falla_seguida_deshabilita_y_agenda_el_reintento():
    d = Disyuntor(fallos_max=3, reintento_s=600)
    d.registrar(FALLA, 0)
    d.registrar(FALLA, 60)
    assert d.registrar(FALLA, 120) == dis.DESHABILITA
    assert (d.estado, d.reintento_en) == (dis.DESHABILITADO, 720)
    assert not d.permite(719)
    assert d.permite(720)


def test_reintento_sano_reactiva_y_fallido_reprograma():
    d = Disyuntor(fallos_max=1, reintento_s=600)
    d.registrar(FALLA, 0)
    assert d.registrar(FALLA, 600) == dis.REINTENTO_FALLIDO
    assert (d.estado, d.reintento_en) == (dis.DESHABILITADO, 1200)
    assert d.registrar(None, 1200) == dis.REACTIVA
    assert (d.estado, d.reintento_en, d.fallos_consecutivos) == (dis.ACTIVO, None, 0)


# --- Agente ---------------------------------------------------------------------------


@pytest.fixture
def falla_rf55_2(monkeypatch):
    def falla(*args, **kwargs):
        raise RuntimeError("falla de RF-55.2 en la prueba")

    activa = {"si": False}
    original = evaluador._evaluar_rf55_2

    def quizas(*args, **kwargs):
        return falla(*args, **kwargs) if activa["si"] else original(*args, **kwargs)

    monkeypatch.setattr(evaluador, "_evaluar_rf55_2", quizas)
    return activa


def nuevo(tmp_path, **kwargs):
    h = Harness(tmp_path, fake_variables=AMBIENTE, default_frecuencia_captura_min=1, **kwargs)
    con_umbral(h)
    # HR 75-76 % en su banda normal: que no dé ERROR_CONFIGURACION por falta de umbral.
    niveles = (
        {"nivel": "normal", "limite_inferior": 0.0, "limite_superior": 80.0},
        {"nivel": "precaucion", "limite_inferior": 80.0, "limite_superior": 90.0},
        {"nivel": "critico", "limite_inferior": 90.0, "limite_superior": 100.0},
    )
    h.umbrales.set(Umbral(9, None, "humedad_relativa", "%", 0.0, 100.0, niveles))
    h.reglas.set(THI)
    return h


def capturar(h, n=1):
    clasificaciones = []
    for _ in range(n):
        h.link.take()
        h.advance(60)
        ack_all(h)
        clasificaciones.append([e["clasificacion_rf55"] for e in h.link.on("evento")])
    return clasificaciones


def rf55_2_en_heartbeat(h):
    (sensor,) = [
        s for s in h.link.on("heartbeat")[-1]["sensores_operativos"] if s["sensor"] == "rf55_2"
    ]
    return sensor["operativo"]


def test_tc_088_tres_fallas_deshabilitan_y_rf55_1_sigue(tmp_path, falla_rf55_2, caplog):
    h = nuevo(tmp_path)
    assert capturar(h) == [["DESVIACION_COMPUESTA"]]

    falla_rf55_2["si"] = True
    caplog.set_level(logging.WARNING)
    assert capturar(h, 3) == [["DESVIACION_SIMPLE"]] * 3
    assert h.agent.estado_rf55_2["estado"] == dis.DESHABILITADO
    assert caplog.text.count("se clasifica con RF-55.1") == 2
    assert "RF-55.2 DESHABILITADO tras 3 fallas seguidas" in caplog.text
    assert rf55_2_en_heartbeat(h) is False  # el heartbeat sale al deshabilitar

    # Deshabilitado no se evalúa (ni siquiera falla): RF-55.1 sigue solo.
    falla_rf55_2["si"] = False
    assert capturar(h) == [["DESVIACION_SIMPLE"]]
    h.buffer.close()


def test_tc_097_falla_transitoria_usa_rf55_1_un_ciclo(tmp_path, falla_rf55_2):
    h = nuevo(tmp_path)
    falla_rf55_2["si"] = True
    assert capturar(h) == [["DESVIACION_SIMPLE"]]
    falla_rf55_2["si"] = False
    assert capturar(h) == [["DESVIACION_COMPUESTA"]]
    estado = h.agent.estado_rf55_2
    assert (estado["estado"], estado["fallos_consecutivos"], estado["operativo"]) == (
        dis.ACTIVO,
        0,
        True,
    )
    h.buffer.close()


def test_tc_097_reactivacion_automatica(tmp_path, falla_rf55_2, caplog):
    h = nuevo(tmp_path, rf55_2_reintento_s=300)
    falla_rf55_2["si"] = True
    capturar(h, 3)
    falla_rf55_2["si"] = False
    caplog.set_level(logging.WARNING)

    # Hasta los 300 s no se reintenta; a la siguiente captura después, sí.
    resultados = capturar(h, 5)
    assert resultados[:4] == [["DESVIACION_SIMPLE"]] * 4
    assert resultados[4] == ["DESVIACION_COMPUESTA"]
    assert "RF-55.2 REACTIVADO" in caplog.text
    assert rf55_2_en_heartbeat(h) is True
    h.buffer.close()


def test_deshabilitado_por_hardware_no_se_reactiva_solo(tmp_path):
    h = nuevo(tmp_path)
    h.agent._rf55 = Habilitacion(False, ("RAM disponible 100 KB < 256 KB",))
    h.agent._compuestas = None
    assert capturar(h, 3) == [["DESVIACION_SIMPLE"]] * 3
    estado = h.agent.estado_rf55_2
    assert estado["operativo"] is False and estado["habilitado_por_hardware"] is False
    assert estado["estado"] == dis.ACTIVO  # el disyuntor no participa
    h.buffer.close()


def test_regla_mal_configurada_no_cuenta_como_falla(tmp_path):
    h = nuevo(tmp_path)
    h.reglas.set(Regla(13, 1, {"nombre": "Tipo A sin umbrales"}))  # ERROR_CONFIGURACION
    resultados = capturar(h, 4)
    assert "ERROR_CONFIGURACION" in resultados[0]  # se reporta como configuración
    assert h.agent.estado_rf55_2["fallos_consecutivos"] == 0  # y no abre el disyuntor
    h.buffer.close()


def test_configuracion_por_entorno():
    base = {
        "EDGE_SERIALS": "S",
        "EDGE_MQTT_HOST": "h",
        "EDGE_MQTT_USERNAME": "u",
        "EDGE_MQTT_PASSWORD": "p",
        "EDGE_HEARTBEAT_INTERVAL_S": "300",
        "EDGE_FRECUENCIA_CAPTURA_MIN": "1",
        "EDGE_INTERVALO_TRANSMISION_MIN": "1",
    }
    s = Settings.from_env(base)
    assert (s.rf55_2_fallos_max, s.rf55_2_reintento_s) == (3, 600.0)
    s = Settings.from_env({**base, "EDGE_RF55_2_FALLOS_MAX": "5", "EDGE_RF55_2_REINTENTO_S": "120"})
    assert (s.rf55_2_fallos_max, s.rf55_2_reintento_s) == (5, 120.0)
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "EDGE_RF55_2_FALLOS_MAX": "0"})
