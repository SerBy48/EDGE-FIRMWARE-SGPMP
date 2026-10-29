"""TC-M03-081: entradas nulas o malformadas no producen clasificaciones válidas falsas."""

import json
import math

import pytest
from conftest import Harness
from test_agent import con_umbral
from test_lora_gateway import Env
from test_reglas_compuestas import UMBRAL_T, VENTANA_A, evaluar, lectura, regla

from edge_agent.lora import protocol as p
from edge_agent.reglas.evaluador import lecturas_invalidas

THI = {"nombre": "THI", "prevalece_sobre_regla_simple": True}
HIDRICA = {"nombre": "Calidad hidrica"}
MALOS = [None, "abc", "31.0", True, math.nan, math.inf, -math.inf]


def clasificaciones(eventos):
    return [(e.clasificacion, e.severidad) for e in eventos]


@pytest.mark.parametrize("valor", MALOS)
def test_umbral_simple_y_tipo_a_ignoran_valores_invalidos(valor):
    lecturas = [lectura("temperatura_ambiente", valor)]
    assert evaluar(lecturas, [regla(VENTANA_A)], [UMBRAL_T]) == []


@pytest.mark.parametrize("valor", MALOS)
@pytest.mark.parametrize("posicion", ["temperatura", "humedad"])
def test_thi_no_clasifica_con_una_variable_invalida(valor, posicion):
    t, hr = (valor, 78.0) if posicion == "temperatura" else (32.5, valor)
    lecturas = [lectura("temperatura_ambiente", t), lectura("humedad_relativa", hr, "%")]
    # Ni DESVIACION falsa ni ERROR_CONFIGURACION: el problema es el dato, no la regla.
    assert evaluar(lecturas, [regla(THI)]) == []


@pytest.mark.parametrize("valor", MALOS)
@pytest.mark.parametrize("posicion", ["ph", "oxigeno"])
def test_hidrica_no_clasifica_con_una_variable_invalida(valor, posicion):
    ph, od = (valor, 3.9) if posicion == "ph" else (5.8, valor)
    lecturas = [lectura("ph", ph, "pH"), lectura("oxigeno_disuelto", od, "mg/L")]
    assert evaluar(lecturas, [regla(HIDRICA)]) == []


@pytest.mark.parametrize(
    ("variable", "valor", "unidad", "cuerpo"),
    [
        ("humedad_relativa", 150.0, "%", THI),
        ("humedad_relativa", -20.0, "%", THI),
        ("ph", -3.0, "pH", HIDRICA),
        ("ph", 20.0, "pH", HIDRICA),
        ("oxigeno_disuelto", -1.0, "mg/L", HIDRICA),
    ],
)
def test_fuera_de_rango_fisico_no_clasifica(variable, valor, unidad, cuerpo):
    otras = {
        "humedad_relativa": lectura("temperatura_ambiente", 34.0),
        "ph": lectura("oxigeno_disuelto", 3.9, "mg/L"),
        "oxigeno_disuelto": lectura("ph", 5.0, "pH"),
    }
    lecturas = [lectura(variable, valor, unidad), otras[variable]]
    assert evaluar(lecturas, [regla(cuerpo)]) == []


@pytest.mark.parametrize(
    ("variable", "valor"),
    [("humedad_relativa", 0.0), ("humedad_relativa", 100.0), ("ph", 0.0), ("ph", 14.0)],
)
def test_bordes_del_rango_fisico_son_validos(variable, valor):
    assert lecturas_invalidas([lectura(variable, valor)], {}) == []


def test_la_lectura_valida_de_la_captura_se_sigue_clasificando():
    lecturas = [lectura("temperatura_ambiente", 30.0), lectura("humedad_relativa", None, "%")]
    # Sin HR válida no hay THI, pero el umbral simple de T (crítico) sigue vigente.
    assert clasificaciones(evaluar(lecturas, [regla(THI)], [UMBRAL_T])) == [
        ("DESVIACION_SIMPLE", "CRITICO")
    ]


def test_un_dato_invalido_no_entra_a_la_ventana_de_correlacion():
    estado_lecturas = [lectura("humedad_relativa", math.inf, "%")]
    from edge_agent.reglas.ventana import EstadoCompuestas

    estado = EstadoCompuestas()
    evaluar(estado_lecturas, [regla(THI)], estado=estado)
    # Llega T 5 s después: no debe correlacionar con la HR infinita de antes.
    siguiente = [lectura("temperatura_ambiente", 32.5, segundos=5)]
    assert evaluar(siguiente, [regla(THI)], estado=estado) == []


def test_mapa_de_variables_aplica_el_rango_fisico():
    lecturas = [lectura("hr_aire", 150.0, "%")]
    (invalida,) = lecturas_invalidas(lecturas, {"HUMEDAD_RELATIVA": "hr_aire"})
    assert "fuera del rango físico" in invalida[1]


# --- entrada LoRa ---------------------------------------------------------------


@pytest.mark.parametrize("valor", [math.nan, math.inf, -math.inf])
def test_gateway_descarta_mediciones_no_finitas(valor, caplog):
    env = Env()
    env.rx(1, 5, p.Telemetria(0, (p.Measurement(1, valor), p.Measurement(2, 60.0))))

    readings = env.gw.read("IOT-A", env.dt)
    assert [(r.variable, r.valor_crudo) for r in readings] == [("humedad", 60.0)]
    assert "valor no finito" in caplog.text


# --- agente: telemetría e historial ---------------------------------------------


class FuenteFija:
    def __init__(self, lecturas):
        self.lecturas = lecturas

    def read(self, serial, now):
        lecturas, self.lecturas = self.lecturas, []
        return lecturas

    def is_alive(self, serial, now, max_silence_s):
        return True

    def status(self, serial):
        return {}

    def apply_config(self, serial, config):
        pass


def test_agente_no_publica_ni_encola_valores_invalidos(tmp_path, caplog):
    h = Harness(tmp_path, historial=True)
    con_umbral(h)
    h.agent._source = FuenteFija(
        [
            lectura("temperatura_ambiente", math.nan),
            lectura("temperatura_ambiente", None),
            lectura("humedad_relativa", 150.0, "%"),
        ]
    )
    h.advance(0)
    h.advance(15 * 60)  # ventana de transmisión de la telemetría

    assert h.link.on("evento") == []
    # NaN y None no son telemetría; HR 150 % sí (dato crudo), pero no se clasifica.
    assert [t["valor_crudo"] for t in h.link.on("telemetry")] == [150.0]
    for _, payload in h.link.published:
        json.loads(json.dumps(payload, allow_nan=False))  # todo lo publicado es JSON válido

    filas = h.historial.recientes()
    assert [f["clasificacion"] for f in filas] == ["LECTURA_INVALIDA"] * 3
    assert [f["variables"][0]["valor"] for f in filas] == ["nan", "None", 150.0]
    assert all(f["ruta"] == "LOCAL" and f["detalle"]["motivo"] for f in filas)
    assert caplog.text.count("descartada para RF-55") == 3
    h.buffer.close()
