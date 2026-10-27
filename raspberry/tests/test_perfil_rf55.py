"""RF-55.2: perfil de hardware que habilita o deshabilita las reglas compuestas."""

import logging

import pytest
from conftest import Harness, make_settings

from edge_agent import main
from edge_agent.agent import Agent
from edge_agent.config_store import Umbral
from edge_agent.reglas import perfil
from edge_agent.reglas.perfil import Habilitacion, Perfil, habilitar, verificar
from edge_agent.reglas.store import Regla
from edge_agent.sources import FakeLoraSource

SUFICIENTE = Perfil(cpu_mhz=1500.0, ram_libre_kb=512_000.0, disco_libre_mb=10_000.0)


def test_perfil_suficiente_habilita_rf55_2():
    assert habilitar(SUFICIENTE) == Habilitacion(True, ())


@pytest.mark.parametrize(
    ("campo", "valor", "recurso"),
    [
        ("cpu_mhz", 120.0, "CPU"),
        ("ram_libre_kb", 200.0, "RAM disponible"),
        ("disco_libre_mb", 0.5, "almacenamiento libre"),
    ],
)
def test_cada_recurso_bajo_el_minimo_deshabilita_y_lo_nombra(
    monkeypatch, tmp_path, caplog, campo, valor, recurso
):
    from dataclasses import replace

    monkeypatch.setattr(perfil, "medir", lambda _: replace(SUFICIENTE, **{campo: valor}))
    with caplog.at_level(logging.WARNING):
        resultado = verificar(tmp_path)

    assert resultado.rf55_2 is False
    assert len(resultado.motivos) == 1 and resultado.motivos[0].startswith(recurso)
    assert any(r.levelno == logging.WARNING and recurso in r.getMessage() for r in caplog.records)


def test_justo_en_el_minimo_alcanza():
    assert habilitar(Perfil(160.0, 256.0, 1.0)).rf55_2 is True


def test_recurso_que_no_se_puede_medir_no_deshabilita():
    assert habilitar(Perfil(None, None, None)).rf55_2 is True


def test_forzar_deshabilitado():
    resultado = habilitar(SUFICIENTE, forzar_deshabilitado=True)
    assert resultado.rf55_2 is False
    assert "EDGE_RF55_2_DESHABILITAR" in resultado.motivos[0]


def test_una_falla_al_medir_no_aborta(monkeypatch, tmp_path):
    def explota(_):
        raise RuntimeError("sin /proc")

    monkeypatch.setattr(perfil, "medir", explota)
    assert verificar(tmp_path).rf55_2 is True


def test_medir_lee_cpufreq_meminfo_y_disco(monkeypatch, tmp_path):
    cpufreq = tmp_path / "cpuinfo_max_freq"
    cpufreq.write_text("1800000\n", encoding="ascii")  # kHz
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 8000000 kB\nMemAvailable:  300 kB\n", encoding="ascii")
    monkeypatch.setattr(perfil, "_CPUFREQ", cpufreq)
    monkeypatch.setattr(perfil, "_MEMINFO", meminfo)

    medido = perfil.medir(tmp_path / "todavia-no-existe")
    assert medido.cpu_mhz == 1800.0
    assert medido.ram_libre_kb == 300.0
    assert medido.disco_libre_mb > 0


def test_medir_sin_proc_devuelve_none(monkeypatch, tmp_path):
    monkeypatch.setattr(perfil, "_CPUFREQ", tmp_path / "no")
    monkeypatch.setattr(perfil, "_CPUINFO", tmp_path / "no")
    monkeypatch.setattr(perfil, "_MEMINFO", tmp_path / "no")
    medido = perfil.medir(tmp_path)
    assert (medido.cpu_mhz, medido.ram_libre_kb) == (None, None)


# --- En el agente ---------------------------------------------------------------


def agente(h: Harness, rf55: Habilitacion) -> Agent:
    return Agent(
        h.settings,
        h.link,
        h.store,
        h.buffer,
        FakeLoraSource(h.settings.fake_variables),
        umbrales=h.umbrales,
        reglas=h.reglas,
        rf55=rf55,
        reloj_sincronizado=lambda: True,
        clock=lambda: h.now,
    )


@pytest.mark.parametrize(
    ("activo", "esperada"), [(True, "DESVIACION_COMPUESTA"), (False, "DESVIACION_SIMPLE")]
)
def test_deshabilitado_rf55_1_sigue_y_las_compuestas_no_corren(tmp_path, activo, esperada):
    h = Harness(tmp_path)
    h.agent = agente(h, Habilitacion(activo, ()))
    # La fuente falsa da 20-30 °C: fuera del umbral simple y de la regla Tipo A.
    niveles = ({"nivel": "normal", "limite_inferior": 0.0, "limite_superior": 10.0},)
    h.umbrales.set(Umbral(7, None, "temperatura_ambiente", "C", 0.0, 10.0, niveles))
    regla = {
        "nombre": "Temperatura alta",
        "tipo_sensor": "TEMPERATURA",
        "categoria_variable": "AMBIENTAL",
        "umbral_max": "1",
        "prevalece_sobre_regla_simple": True,
    }
    h.reglas.set(Regla(id_regla_alerta=1, version_regla=1, regla=regla))

    h.advance(0)

    (evento,) = h.link.on("evento")
    assert evento["clasificacion_rf55"] == esperada
    h.buffer.close()


@pytest.mark.parametrize("activo", [True, False])
def test_heartbeat_reporta_rf55_2_en_sensores_operativos(tmp_path, activo):
    h = Harness(tmp_path)
    h.agent = agente(h, Habilitacion(activo, ()))
    h.advance(0)
    (hb,) = h.link.on("heartbeat")
    assert {"sensor": "rf55_2", "operativo": activo} in hb["sensores_operativos"]
    h.buffer.close()


def test_el_arranque_nunca_falla_por_el_perfil(monkeypatch, tmp_path):
    monkeypatch.setattr(perfil, "medir", lambda _: Perfil(1.0, 1.0, 0.0))
    settings = make_settings(tmp_path)
    runtime = main.build(settings)
    try:
        assert runtime.agent._compuestas is None  # RF-55.2 apagado, el agente vivo
    finally:
        runtime.buffer.close()


def test_env_fuerza_la_deshabilitacion(monkeypatch, tmp_path):
    from dataclasses import replace

    monkeypatch.setattr(perfil, "medir", lambda _: SUFICIENTE)
    runtime = main.build(replace(make_settings(tmp_path), rf55_2_deshabilitar=True))
    try:
        assert runtime.agent._compuestas is None
    finally:
        runtime.buffer.close()
