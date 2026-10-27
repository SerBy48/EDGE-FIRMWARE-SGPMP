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
