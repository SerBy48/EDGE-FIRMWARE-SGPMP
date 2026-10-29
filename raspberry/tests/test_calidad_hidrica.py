"""RF-55.2: regla AND de calidad hídrica (TC-M03-106), fórmula `CALIDAD_HIDRICA`."""

import pytest
from test_reglas_compuestas import compuestas, evaluar, lectura, regla

from edge_agent.reglas.evaluador import validar_regla
from edge_agent.reglas.evento import a_payload

HIDRICA = {
    "nombre": "Calidad hídrica",
    "tipo_sensor": "PH",
    "categoria_variable": "AGUA",
    "prevalece_sobre_regla_simple": True,
}


def agua(ph, oxigeno, segundos_od=1.0):
    return [
        lectura("ph", ph, "pH"),
        lectura("oxigeno_disuelto", oxigeno, "mg/L", segundos_od),
    ]


def severidad(ph, oxigeno, cuerpo=HIDRICA):
    eventos = compuestas(evaluar(agua(ph, oxigeno), [regla(cuerpo, id_regla=21, version=1)]))
    assert len(eventos) <= 1
    return eventos[0].severidad if eventos else None


@pytest.mark.parametrize(
    ("ph", "oxigeno", "esperado"),
    [
        (5.8, 3.9, "CRITICO"),  # HIDRICA_106-1: ambas condiciones
        (5.8, 6.5, None),  # HIDRICA_106-2: solo pH fuera de rango
        (7.2, 3.9, None),  # HIDRICA_106-3: solo OD bajo
    ],
)
def test_escenarios_hidrica_106(ph, oxigeno, esperado):
    assert severidad(ph, oxigeno) == esperado


@pytest.mark.parametrize(
    ("ph", "oxigeno", "esperado"),
    [
        (6.5, 3.0, None),  # límite inferior del pH es permitido
        (6.49, 3.0, "CRITICO"),
        (8.5, 3.0, None),  # límite superior del pH es permitido
        (8.51, 3.0, "CRITICO"),
        (9.0, 5.0, None),  # 5 mg/L no es "inferior a 5"
        (9.0, 4.99, "CRITICO"),
        (7.0, 7.0, None),
    ],
)
def test_fronteras(ph, oxigeno, esperado):
    assert severidad(ph, oxigeno) == esperado


def test_trazabilidad_del_evento():
    (evento,) = compuestas(evaluar(agua(5.8, 3.9), [regla(HIDRICA, id_regla=21, version=1)]))
    payload = a_payload(evento)
    meta = payload["metadata_edge"]
    assert payload["clasificacion_rf55"] == "DESVIACION_COMPUESTA"
    assert meta["regla_aplicada"] == "CALIDAD_HIDRICA"
    assert meta["nombre_regla"] == "Calidad hídrica"
    assert (meta["id_regla_alerta"], meta["version_regla"]) == (21, 1)
    assert [(v["tipo_variable"], v["valor"]) for v in payload["variables_involucradas"]] == [
        ("ph", 5.8),
        ("oxigeno_disuelto", 3.9),
    ]


@pytest.mark.parametrize("nombre", ["CALIDAD_HIDRICA", "Calidad del agua", "calidad agua"])
def test_alias_eligen_la_formula(nombre):
    assert severidad(5.8, 3.9, {**HIDRICA, "nombre": nombre}) == "CRITICO"


def test_umbrales_severidad_parametriza_limites_y_severidad():
    cuerpo = {
        **HIDRICA,
        "umbrales_severidad": {"ph_min": "6.0", "oxigeno_min": 4, "severidad": "MODERADO"},
    }
    assert severidad(6.2, 3.5, cuerpo) is None  # 6.2 ya es permitido
    assert severidad(5.8, 4.5, cuerpo) is None  # 4.5 ya no es bajo
    assert severidad(5.8, 3.5, cuerpo) == "MODERADO"


def test_falta_una_variable_no_dispara():
    eventos = evaluar([lectura("ph", 5.8, "pH")], [regla(HIDRICA)])
    assert compuestas(eventos) == []


def test_od_fuera_de_la_ventana_de_correlacion_no_dispara():
    eventos = evaluar(agua(5.8, 3.9, segundos_od=31.0), [regla(HIDRICA)])
    assert compuestas(eventos) == []


@pytest.mark.parametrize(
    "umbrales",
    [
        {"ph_min": 9, "ph_max": 8},
        {"oxigeno_min": "bajo"},
        {"severidad": "GRAVE"},
        ["no", "dict"],
    ],
)
def test_parametros_invalidos_se_rechazan(umbrales):
    assert validar_regla({**HIDRICA, "umbrales_severidad": umbrales}) is not None
