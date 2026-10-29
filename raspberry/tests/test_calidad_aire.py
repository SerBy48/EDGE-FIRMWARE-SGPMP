"""RF-55.2: regla AND de NH3/CO2 (TC-M03-085, TC-M03-105), fórmula `CALIDAD_AIRE`."""

import pytest
from test_reglas_compuestas import compuestas, evaluar, lectura, regla

from edge_agent.reglas.evaluador import validar_regla
from edge_agent.reglas.evento import a_payload
from edge_agent.reglas.ventana import EstadoCompuestas

AIRE = {
    "nombre": "Calidad del aire",
    "tipo_sensor": "GAS",
    "categoria_variable": "AMBIENTAL",
    "prevalece_sobre_regla_simple": True,
}


def gases(nh3, co2, segundos_co2=1.0):
    return [lectura("nh3", nh3, "ppm"), lectura("co2", co2, "ppm", segundos_co2)]


def severidad(nh3, co2, cuerpo=AIRE, segundos_co2=1.0):
    eventos = compuestas(evaluar(gases(nh3, co2, segundos_co2), [regla(cuerpo, 31, 1)]))
    assert len(eventos) <= 1
    return eventos[0].severidad if eventos else None


def test_tc_085_ambos_superan_su_umbral():
    assert severidad(30.0, 2200.0) == "MODERADO"


@pytest.mark.parametrize(
    ("nh3", "co2", "esperado"),
    [
        (30.0, 2200.0, "MODERADO"),  # TC-105-1: ambos superan
        (30.0, 1800.0, None),  # TC-105-2: solo CO2 por debajo
        (20.0, 2200.0, None),  # TC-105-3: solo NH3 por debajo
    ],
)
def test_tc_105_matriz_and(nh3, co2, esperado):
    assert severidad(nh3, co2) == esperado


@pytest.mark.parametrize(
    ("nh3", "co2", "esperado"),
    [
        (25.0, 2200.0, None),  # "superar" es estricto: 25 ppm no cumple
        (25.01, 2200.0, "MODERADO"),
        (30.0, 2000.0, None),  # 2000 ppm no cumple
        (30.0, 2000.01, "MODERADO"),
        (20.0, 1800.0, None),
    ],
)
def test_fronteras(nh3, co2, esperado):
    assert severidad(nh3, co2) == esperado


@pytest.mark.parametrize(("segundos", "esperado"), [(29.0, "MODERADO"), (30.0, "MODERADO")])
def test_dentro_de_la_ventana_de_30_s_correlaciona(segundos, esperado):
    assert _en_dos_capturas(segundos) == esperado


def test_fuera_de_la_ventana_de_30_s_no_correlaciona():
    assert _en_dos_capturas(31.0) is None


def test_ventana_mas_corta_por_regla():
    cuerpo = {**AIRE, "ventana_correlacion_s": 5}
    assert _en_dos_capturas(5.0, cuerpo) == "MODERADO"
    assert _en_dos_capturas(6.0, cuerpo) is None


def test_dos_nodos_del_mismo_edge_correlacionan_una_sola_vez():
    # La ventana prefiere el mismo nodo y si no, cualquier nodo del Edge (un
    # Gateway cubre un sitio). En la misma captura no debe salir duplicado.
    lecturas = [
        lectura("nh3", 30.0, "ppm", serial="NODO-A"),
        lectura("co2", 2200.0, "ppm", 1.0, serial="NODO-B"),
    ]
    (evento,) = compuestas(evaluar(lecturas, [regla(AIRE)]))
    assert [r.serial for r in evento.lecturas] == ["NODO-A", "NODO-B"]


def test_el_mismo_nodo_tiene_prioridad_sobre_otro():
    estado = EstadoCompuestas()
    reglas = [regla(AIRE)]
    evaluar([lectura("co2", 2500.0, "ppm", serial="NODO-B")], reglas, estado=estado)
    evaluar([lectura("co2", 2200.0, "ppm", 1.0, serial="NODO-A")], reglas, estado=estado)
    (evento,) = compuestas(
        evaluar([lectura("nh3", 30.0, "ppm", 2.0, serial="NODO-A")], reglas, estado=estado)
    )
    assert [(r.serial, r.valor_crudo) for r in evento.lecturas] == [
        ("NODO-A", 30.0),
        ("NODO-A", 2200.0),
    ]


def test_trazabilidad_del_evento():
    (evento,) = compuestas(evaluar(gases(30.0, 2200.0), [regla(AIRE, 31, 1)]))
    payload = a_payload(evento)
    meta = payload["metadata_edge"]
    assert payload["clasificacion_rf55"] == "DESVIACION_COMPUESTA"
    assert payload["severidad"] == "MODERADO"
    assert meta["regla_aplicada"] == "CALIDAD_AIRE"
    assert (meta["id_regla_alerta"], meta["version_regla"]) == (31, 1)
    assert meta["ventana_temporal_ms"] == 1000
    assert [(v["tipo_variable"], v["valor"]) for v in payload["variables_involucradas"]] == [
        ("nh3", 30.0),
        ("co2", 2200.0),
    ]


@pytest.mark.parametrize("nombre", ["CALIDAD_AIRE", "Correlación NH3/CO2", "nh3 co2"])
def test_alias_eligen_la_formula(nombre):
    assert severidad(30.0, 2200.0, {**AIRE, "nombre": nombre}) == "MODERADO"


def test_umbrales_severidad_parametriza_limites_y_severidad():
    cuerpo = {
        **AIRE,
        "umbrales_severidad": {"nh3_max": "35", "co2_max": 2500, "severidad": "CRITICO"},
    }
    assert severidad(30.0, 3000.0, cuerpo) is None  # 30 ya no supera 35
    assert severidad(40.0, 2200.0, cuerpo) is None  # 2200 ya no supera 2500
    assert severidad(40.0, 3000.0, cuerpo) == "CRITICO"


def test_falta_una_variable_no_dispara():
    assert compuestas(evaluar([lectura("nh3", 30.0, "ppm")], [regla(AIRE)])) == []


@pytest.mark.parametrize("variable", ["nh3", "co2"])
def test_concentracion_negativa_es_lectura_invalida(variable):
    otra = "co2" if variable == "nh3" else "nh3"
    lecturas = [lectura(variable, -5.0, "ppm"), lectura(otra, 5000.0, "ppm")]
    assert evaluar(lecturas, [regla(AIRE)]) == []


@pytest.mark.parametrize(
    "umbrales",
    [{"nh3_max": "alto"}, {"severidad": "GRAVE"}, ["no", "dict"]],
)
def test_parametros_invalidos_se_rechazan(umbrales):
    assert validar_regla({**AIRE, "umbrales_severidad": umbrales}) is not None


def _en_dos_capturas(segundos, cuerpo=AIRE):
    """NH3 en una captura y CO2 en otra, `segundos` después (mismo nodo)."""
    estado = EstadoCompuestas()
    reglas = [regla(cuerpo, 31, 1)]
    assert compuestas(evaluar([lectura("nh3", 30.0, "ppm")], reglas, estado=estado)) == []
    eventos = compuestas(evaluar([lectura("co2", 2200.0, "ppm", segundos)], reglas, estado=estado))
    return eventos[0].severidad if eventos else None
