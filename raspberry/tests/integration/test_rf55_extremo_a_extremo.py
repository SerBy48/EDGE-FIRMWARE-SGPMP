"""RF-55 de extremo a extremo con lecturas sintéticas: comando → evaluación → evento.

A diferencia del resto de `tests/integration`, no necesita broker ni API:
corre el agente real con el inyector de QA (`scripts/inyectar_lecturas.py`)
sobre los casos de `scripts/casos_rf55.json`, que son la tabla de
`docs/CONTRATO_EVENTO_EDGE.md`. Por eso no lleva la marca `integration` y
corre con el `pytest` de siempre.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
CASOS = json.loads((SCRIPTS / "casos_rf55.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location(
    "inyectar_lecturas", SCRIPTS / "inyectar_lecturas.py"
)
inyector = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = inyector  # @dataclass busca su módulo en sys.modules
_spec.loader.exec_module(inyector)

CAMPOS = {
    "clasificacion_rf55",
    "severidad",
    "variables_involucradas",
    "timestamp_captura",
    "timestamp_procesamiento_edge",
    "origen",
    "estado_conectividad",
    "metadata_edge",
}
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
CLASIFICACIONES = {"NORMAL", "DESVIACION_SIMPLE", "DESVIACION_COMPUESTA", "ERROR_CONFIGURACION"}


@pytest.fixture(scope="module")
def resultados():
    return {r.nombre: r for r in inyector.ejecutar(CASOS)}


@pytest.mark.parametrize("nombre", [c["nombre"] for c in CASOS["casos"]])
def test_cada_caso_de_la_tabla(resultados, nombre):
    resultado = resultados[nombre]
    assert resultado.ok, resultado.fallas


def test_todo_evento_cumple_el_contrato(resultados):
    eventos = [e for r in resultados.values() for e in r.eventos]
    assert eventos
    for evento in eventos:
        assert set(evento) <= CAMPOS and CAMPOS - set(evento) <= {"severidad"}
        assert evento["clasificacion_rf55"] in CLASIFICACIONES
        assert evento.get("severidad", "LEVE") in {"LEVE", "MODERADO", "CRITICO"}
        assert evento["origen"] in {"TIEMPO_REAL", "BUFFER_LOCAL"}
        assert ISO_Z.match(evento["timestamp_captura"])
        assert ISO_Z.match(evento["timestamp_procesamiento_edge"])
        for variable in evento["variables_involucradas"]:
            assert ISO_Z.match(variable["timestamp_captura"])
            assert {"tipo_variable", "valor", "unidad", "serial"} <= set(variable)
        meta = evento["metadata_edge"]
        assert meta["id_evento"].startswith("edge-evt-")
        assert {"regla_aplicada", "tiempo_procesamiento_ms", "version_firmware"} <= set(meta)
        if evento["clasificacion_rf55"] == "DESVIACION_COMPUESTA":
            assert {"nombre_regla", "version_regla", "id_regla_alerta"} <= set(meta)


# --- TC-M03-199: trazabilidad de la regla, tal como la publica el Edge ---------

_REGLAS_TRAZA = [
    (
        {"nombre": "Estrés térmico por THI"},
        [("temperatura_ambiente", 32.5, "C"), ("humedad_relativa", 78.0, "%")],
        "ESTRES_TERMICO",
    ),
    (
        {"nombre": "Calidad hídrica"},
        [("ph", 5.8, "pH"), ("oxigeno_disuelto", 3.9, "mg/L")],
        "CALIDAD_HIDRICA",
    ),
    (
        {"nombre": "Calidad del aire"},
        [("nh3", 30.0, "ppm"), ("co2", 2200.0, "ppm")],
        "CALIDAD_AIRE",
    ),
    (
        {
            "nombre": "Temperatura alta sostenida",
            "tipo_sensor": "TEMPERATURA",
            "categoria_variable": "AMBIENTAL",
            "umbral_max": "30.0",
            "ventana_confirmacion_min": "0.0000",
            "lectura_confirmacion_max": "1.0000",
        },
        [("temperatura_ambiente", 31.0, "C")],
        "VENTANA_CONFIRMACION",
    ),
]


def _compuesta(resultado):
    (evento,) = [e for e in resultado.eventos if e["clasificacion_rf55"] == "DESVIACION_COMPUESTA"]
    return evento  # JSON tal como salió por MQTT


def _caso_traza(reglas, lecturas, **extra):
    return {
        "nombre": "traza",
        "umbrales": [],
        "reglas": reglas,
        "lecturas": [{"t": 0, "variable": v, "valor": x, "unidad": u} for v, x, u in lecturas],
        **extra,
    }


@pytest.mark.parametrize(("cuerpo", "lecturas", "codigo"), _REGLAS_TRAZA)
def test_el_edge_publica_la_trazabilidad_de_cada_tipo_de_regla(cuerpo, lecturas, codigo):
    regla = {"id_regla_alerta": 44, "version_regla": 7, "regla": cuerpo}
    (resultado,) = inyector.ejecutar({"casos": [_caso_traza([regla], lecturas)]})

    evento = _compuesta(resultado)
    meta = evento["metadata_edge"]
    assert evento["clasificacion_rf55"] == "DESVIACION_COMPUESTA"
    assert meta["regla_aplicada"] == codigo
    assert meta["nombre_regla"] == cuerpo["nombre"]  # exacto, con tildes
    assert (meta["version_regla"], meta["id_regla_alerta"]) == (7, 44)
    assert type(meta["version_regla"]) is int and type(meta["id_regla_alerta"]) is int


def test_el_evento_lleva_la_version_vigente_de_la_regla():
    cuerpo, lecturas, _ = _REGLAS_TRAZA[0]
    v1 = {"id_regla_alerta": 44, "version_regla": 1, "regla": cuerpo}
    v2 = {"id_regla_alerta": 44, "version_regla": 2, "regla": {**cuerpo, "nombre": "THI"}}
    (resultado,) = inyector.ejecutar({"casos": [_caso_traza([v1, v2], lecturas)]})

    meta = _compuesta(resultado)["metadata_edge"]
    assert not resultado.acks_error
    assert (meta["version_regla"], meta["nombre_regla"]) == (2, "THI")


@pytest.mark.parametrize("nombre", [None, "", "   ", 123])
def test_regla_sin_nombre_valido_se_rechaza_y_no_emite(nombre):
    cuerpo = {**_REGLAS_TRAZA[3][0], "nombre": nombre}
    regla = {"id_regla_alerta": 44, "version_regla": 1, "regla": cuerpo}
    caso = _caso_traza(
        [regla],
        _REGLAS_TRAZA[3][1],
        acks_error=1,
        esperado=[],
        esperado_sin_umbral=["temperatura_ambiente"],  # el caso no trae umbral RF-17
    )
    (resultado,) = inyector.ejecutar({"casos": [caso]})

    assert resultado.ok, resultado.fallas
    assert "nombre debe ser un texto no vacío" in resultado.acks_error[0]["motivo"]


def test_el_inyector_exige_la_trazabilidad_en_todo_evento_compuesto():
    meta = {"regla_aplicada": "X"}
    evento = {"clasificacion_rf55": "DESVIACION_COMPUESTA", "metadata_edge": meta}
    fallas = inyector.trazabilidad([evento])
    assert len(fallas) == 3  # nombre_regla, version_regla, id_regla_alerta
    simple = {"clasificacion_rf55": "DESVIACION_SIMPLE", "metadata_edge": {}}
    assert inyector.trazabilidad([simple]) == []


def test_el_evento_va_al_topic_del_nodo_de_la_variable_primaria():
    caso = next(c for c in CASOS["casos"] if c["nombre"].startswith("C5"))
    link = inyector.LinkMemoria()
    original = inyector.LinkMemoria
    try:
        inyector.LinkMemoria = lambda: link
        inyector.ejecutar({**CASOS, "casos": [caso]})
    finally:
        inyector.LinkMemoria = original
    (topic,) = [t for t, _ in link.publicados if t.endswith("/evento")]
    assert topic == f"sgpmp/{inyector.SERIAL_DEFECTO}/evento"


def test_el_inyector_detecta_un_resultado_distinto_al_esperado():
    caso = {
        "nombre": "esperado falso",
        "lecturas": [{"t": 0, "variable": "temperatura_ambiente", "valor": 26.0, "unidad": "C"}],
        "esperado": [{"clasificacion_rf55": "DESVIACION_SIMPLE", "severidad": "CRITICO"}],
    }
    (resultado,) = inyector.ejecutar({**CASOS, "casos": [caso]})
    assert not resultado.ok
    assert "severidad='MODERADO'" in resultado.fallas[0]


def test_cli_devuelve_1_si_falla_algun_caso(tmp_path, capsys):
    archivo = tmp_path / "casos.json"
    caso = {"nombre": "x", "lecturas": [], "esperado": [{"clasificacion_rf55": "NORMAL"}]}
    archivo.write_text(json.dumps({"casos": [caso]}), encoding="utf-8")
    assert inyector.main([str(archivo)]) == 1
    assert inyector.main([str(SCRIPTS / "casos_rf55.json")]) == 0
    assert "casos OK" in capsys.readouterr().out
