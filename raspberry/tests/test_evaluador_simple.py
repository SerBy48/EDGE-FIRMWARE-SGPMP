"""RF-55.1: evaluación de lecturas contra umbrales RF-17 y sobre del evento Edge."""

import re
import time
from datetime import UTC, datetime, timedelta

import pytest

from edge_agent.config_store import Umbral
from edge_agent.reglas.evaluador import evaluar_simple
from edge_agent.reglas.evento import a_payload
from edge_agent.sources import Reading

AHORA = datetime(2026, 10, 26, 14, 30, 0, 247000, tzinfo=UTC)
VERSION = "v0.3.0"
NIVELES = (
    {"nivel": "normal", "limite_inferior": 18.0, "limite_superior": 24.0},
    {"nivel": "precaucion", "limite_inferior": 24.0, "limite_superior": 28.0},
    {"nivel": "critico", "limite_inferior": 28.0, "limite_superior": 32.0},
)
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
CAMPOS_PRIMER_NIVEL = {
    "clasificacion_rf55",
    "severidad",
    "variables_involucradas",
    "timestamp_captura",
    "timestamp_procesamiento_edge",
    "origen",
    "estado_conectividad",
    "metadata_edge",
}


def umbral(id_umbral: int = 7, niveles=NIVELES, variable: str = "temperatura_ambiente") -> Umbral:
    return Umbral(
        id_umbral_ambiental=id_umbral,
        version="2026-10-05T17:59:58+00:00",
        variable=variable,
        unidad="C",
        valor_min=18.0,
        valor_max=32.0,
        niveles=tuple(niveles),
    )


def lectura(valor: float, variable: str = "temperatura_ambiente") -> Reading:
    return Reading(
        serial="TC-M09-XXXX-0001",
        variable=variable,
        valor_crudo=valor,
        unidad="C",
        timestamp_captura=AHORA - timedelta(seconds=1),
        sensor="dht22_ambiente",
    )


def evaluar(valor: float, umbrales=None, variable: str = "temperatura_ambiente"):
    umbrales = [umbral()] if umbrales is None else umbrales
    return evaluar_simple(lectura(valor, variable), umbrales, ahora=AHORA, version_firmware=VERSION)


@pytest.mark.parametrize("valor", [18.0, 20.0, 23.99])
def test_dentro_de_rango_no_emite(valor):
    assert evaluar(valor) is None


@pytest.mark.parametrize(
    ("valor", "severidad", "nivel"),
    [
        (25.0, "MODERADO", "precaucion"),
        (30.0, "CRITICO", "critico"),
        (35.0, "CRITICO", "fuera_de_rango"),
        (10.0, "CRITICO", "fuera_de_rango"),
    ],
)
def test_cada_nivel_produce_su_severidad(valor, severidad, nivel):
    evento = evaluar(valor)
    assert (evento.clasificacion, evento.severidad) == ("DESVIACION_SIMPLE", severidad)
    assert evento.metadata["nivel_umbral"] == nivel


@pytest.mark.parametrize(
    ("borde", "esperado"),
    [(18.0, None), (24.0, "MODERADO"), (28.0, "CRITICO"), (32.0, "CRITICO")],
)
def test_los_bordes_caen_en_una_sola_banda_la_mas_grave(borde, esperado):
    # Mismo criterio que el broker: en el límite entre dos bandas cuenta la más grave.
    evento = evaluar(borde)
    assert (evento.severidad if evento else None) == esperado


def test_con_varios_umbrales_rige_el_mas_restrictivo():
    laxo = umbral(
        8,
        (
            {"nivel": "normal", "limite_inferior": 10.0, "limite_superior": 30.0},
            {"nivel": "precaucion", "limite_inferior": 30.0, "limite_superior": 40.0},
            {"nivel": "critico", "limite_inferior": 40.0, "limite_superior": 50.0},
        ),
    )
    evento = evaluar(29.0, [laxo, umbral()])
    assert evento.severidad == "CRITICO"
    assert evento.metadata["id_umbral_ambiental"] == 7


def test_sin_umbral_para_la_variable_es_error_sin_severidad():
    """TC-M03-083: no se inventa una clasificación con umbrales que no existen."""
    evento = evaluar(99.0, [])
    assert (evento.clasificacion, evento.severidad) == ("ERROR_CONFIGURACION", None)
    assert evento.metadata["id_umbral_ambiental"] is None
    assert evento.metadata["variable"] == "temperatura_ambiente"
    assert evento.metadata["severidad_invalida"] is True
    assert "severidad" not in a_payload(evento)
    assert evaluar(99.0, variable="humedad_relativa").metadata["variable"] == "humedad_relativa"


@pytest.mark.parametrize(
    "niveles",
    [
        (),
        ({"nivel": "alto", "limite_inferior": 1, "limite_superior": 2},),
        ({"nivel": "normal", "limite_inferior": "18", "limite_superior": 24},),
        ({"nivel": "normal", "limite_inferior": 24, "limite_superior": 18},),
        ({"nivel": "fuera_de_rango", "limite_inferior": 1, "limite_superior": 2},),
        ("no es dict",),
    ],
)
def test_umbral_mal_formado_es_error_de_configuracion(niveles):
    evento = evaluar(20.0, [umbral(niveles=niveles)])
    assert (evento.clasificacion, evento.severidad) == ("ERROR_CONFIGURACION", None)
    assert evento.metadata["id_umbral_ambiental"] == 7
    assert "severidad" not in a_payload(evento)


def test_una_desviacion_valida_prevalece_sobre_un_umbral_mal_formado():
    roto = umbral(9, niveles=())
    assert evaluar(30.0, [roto, umbral()]).clasificacion == "DESVIACION_SIMPLE"
    assert evaluar(20.0, [roto, umbral()]).clasificacion == "ERROR_CONFIGURACION"


def test_lectura_no_finita_no_emite():
    assert evaluar(float("nan")) is None


def test_payload_cumple_el_contrato():
    payload = a_payload(evaluar(30.0))

    assert set(payload) == CAMPOS_PRIMER_NIVEL
    assert payload["clasificacion_rf55"] == "DESVIACION_SIMPLE"
    assert payload["severidad"] == "CRITICO"
    assert payload["origen"] == "TIEMPO_REAL"
    assert payload["estado_conectividad"] is True
    assert payload["timestamp_captura"] == "2026-10-26T14:29:59.247Z"
    assert payload["timestamp_procesamiento_edge"] == "2026-10-26T14:30:00.247Z"
    (variable,) = payload["variables_involucradas"]
    assert variable == {
        "tipo_variable": "temperatura_ambiente",
        "valor": 30.0,
        "unidad": "C",
        "timestamp_captura": "2026-10-26T14:29:59.247Z",
        "sensor": "dht22_ambiente",
        "serial": "TC-M09-XXXX-0001",
    }
    meta = payload["metadata_edge"]
    assert re.fullmatch(r"edge-evt-[0-9a-f-]{36}", meta["id_evento"])
    assert meta["regla_aplicada"] == "UMBRAL_AMBIENTAL"
    assert meta["version_firmware"] == VERSION
    assert isinstance(meta["tiempo_procesamiento_ms"], float)
    assert meta["tiempo_procesamiento_ms"] >= 0


def test_timestamps_siempre_en_utc_con_z():
    from datetime import timezone

    bogota = timezone(timedelta(hours=-5))
    reading = Reading(
        "S", "temperatura_ambiente", 30.0, "C", datetime(2026, 10, 26, 9, 0, tzinfo=bogota)
    )
    evento = evaluar_simple(
        reading, [umbral()], ahora=AHORA.astimezone(bogota), version_firmware=VERSION
    )
    payload = a_payload(evento)
    assert payload["timestamp_captura"] == "2026-10-26T14:00:00.000Z"
    for valor in (payload["timestamp_captura"], payload["timestamp_procesamiento_edge"]):
        assert ISO_Z.match(valor)


def test_cada_evento_tiene_id_propio():
    assert evaluar(30.0).id_evento != evaluar(30.0).id_evento


def test_latencia_por_lectura_bajo_500_ms():
    umbrales = [umbral(i) for i in range(1, 51)]
    peor = 0.0
    for valor in range(0, 1000):
        inicio = time.perf_counter()
        evaluar(float(valor % 40), umbrales)
        peor = max(peor, time.perf_counter() - inicio)
    assert peor < 0.5
