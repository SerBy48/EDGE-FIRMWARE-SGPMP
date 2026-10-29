"""Errores de configuración de RF-55.1 (TC-M03-083, TC-M03-095, TC-M03-122)."""

import pytest
from conftest import Harness
from test_agent import ack_all
from test_umbrales import umbral as comando_umbral

from edge_agent.commands import handle_umbral
from edge_agent.config_store import Umbral
from edge_agent.reglas.evaluador import SIN_UMBRAL, motivo_niveles


def niveles(*bandas):
    return [{"nivel": n, "limite_inferior": a, "limite_superior": b} for n, a, b in bandas]


VALIDOS = niveles(("normal", 18.0, 24.0), ("precaucion", 24.0, 28.0), ("critico", 28.0, 32.0))
TRES = "niveles debe traer normal, precaucion y critico, una vez cada uno"


# --- Forma de los niveles ----------------------------------------------------------


def test_niveles_validos():
    assert motivo_niveles(18.0, 32.0, VALIDOS) is None
    # El orden de la lista no importa; las bandas pueden ir de mayor a menor.
    assert motivo_niveles(18.0, 32.0, list(reversed(VALIDOS))) is None
    # Un límite en 0 es un valor como cualquier otro (TC-M03-095).
    cero = niveles(("critico", -10.0, 0.0), ("precaucion", 0.0, 5.0), ("normal", 5.0, 10.0))
    assert motivo_niveles(-10.0, 10.0, cero) is None


@pytest.mark.parametrize(
    ("valor_min", "valor_max", "bandas", "motivo"),
    [
        # TC-M03-095: umbral en cero.
        (0.0, 0.0, VALIDOS, "valor_min y valor_max deben ser números con valor_min < valor_max"),
        (
            0.0,
            10.0,
            niveles(("normal", 0.0, 0.0), ("precaucion", 0.0, 0.0), ("critico", 0.0, 10.0)),
            "la banda normal debe tener ancho mayor que 0 (0–0)",
        ),
        # TC-M03-122: faltan las bandas que dan la severidad.
        (18.0, 32.0, niveles(("normal", 18.0, 32.0)), TRES),
        (18.0, 32.0, niveles(("normal", 18.0, 24.0), ("precaucion", 24.0, 32.0)), TRES),
        (18.0, 32.0, [*VALIDOS, VALIDOS[0]], TRES),
        # Forma que el backend rechaza con SOLAPAMIENTO_NIVELES.
        (
            18.0,
            32.0,
            niveles(("normal", 18.0, 20.0), ("precaucion", 24.0, 28.0), ("critico", 28.0, 32.0)),
            "las bandas normal y precaucion deben ser contiguas (sin huecos ni solapes)",
        ),
        (
            18.0,
            32.0,
            niveles(("normal", 18.0, 26.0), ("precaucion", 24.0, 28.0), ("critico", 28.0, 32.0)),
            "las bandas normal y precaucion deben ser contiguas (sin huecos ni solapes)",
        ),
        (18.0, 40.0, VALIDOS, "las bandas deben cubrir exactamente [valor_min, valor_max]"),
    ],
)
def test_niveles_inutilizables(valor_min, valor_max, bandas, motivo):
    assert motivo_niveles(valor_min, valor_max, bandas) == motivo


@pytest.mark.parametrize(
    "overrides",
    [
        {"valor_min": 0.0, "valor_max": 0.0},
        {
            "niveles": niveles(
                ("normal", 18.0, 24.0), ("precaucion", 24.0, 24.0), ("critico", 24.0, 32.0)
            )
        },
        {"niveles": niveles(("normal", 18.0, 32.0))},
    ],
)
def test_el_ack_rechaza_el_umbral_y_no_lo_guarda(overrides):
    resultado = handle_umbral(comando_umbral(**overrides), None)
    assert resultado.ack["resultado"] == "ERROR"
    assert resultado.ack["motivo"] == motivo_niveles(
        overrides.get("valor_min", 18.0),
        overrides.get("valor_max", 32.0),
        overrides.get("niveles", VALIDOS),
    )
    assert resultado.umbral is None


# --- Agente --------------------------------------------------------------------------


def eventos(h):
    """Eventos distintos publicados (sin PUBACK el mismo se reenvía)."""
    return list({e["metadata_edge"]["id_evento"]: e for e in h.link.on("evento")}.values())


def test_tc_122_umbral_guardado_sin_severidad_no_inventa_una_clasificacion(tmp_path):
    h = Harness(tmp_path, historial=True)
    # Lo dejó guardado un firmware anterior, sin pasar por el ACK actual.
    h.umbrales.set(
        Umbral(
            7, None, "temperatura_ambiente", "C", 0.0, 50.0, tuple(niveles(("normal", 0.0, 1.0)))
        )
    )
    h.advance(0)  # la fuente falsa da 20-30 °C: fuera de la única banda
    ack_all(h)
    h.advance(15 * 60)

    (evento,) = eventos(h)
    assert evento["clasificacion_rf55"] == "ERROR_CONFIGURACION"
    assert "severidad" not in evento  # ni CRITICO ni ninguna otra
    meta = evento["metadata_edge"]
    assert (meta["id_umbral_ambiental"], meta["severidad_invalida"], meta["motivo"]) == (
        7,
        True,
        TRES,
    )
    # El dato crudo sigue saliendo al backend como telemetría (RF-62).
    crudos = {t["valor_crudo"] for t in h.link.on("telemetry")}
    assert evento["variables_involucradas"][0]["valor"] in crudos
    publicada, local = h.historial.recientes()
    assert (publicada["ruta"], local["ruta"]) == ("DIRECTA", "LOCAL")
    assert local["detalle"]["severidad_invalida"] is True
    h.buffer.close()


def test_tc_083_el_error_no_impide_clasificar_las_siguientes_mediciones(tmp_path):
    h = Harness(tmp_path)
    h.advance(0)
    ack_all(h)
    (error,) = eventos(h)
    assert error["metadata_edge"]["motivo"] == SIN_UMBRAL
    h.link.take()

    # Llega el umbral: desde la captura siguiente se clasifica con él.
    h.umbrales.set(
        Umbral(
            7,
            None,
            "temperatura_ambiente",
            "C",
            0.0,
            18.0,
            tuple(
                niveles(("normal", 0.0, 10.0), ("precaucion", 10.0, 15.0), ("critico", 15.0, 18.0))
            ),
        )
    )
    h.advance(15 * 60)
    (simple,) = eventos(h)
    assert (simple["clasificacion_rf55"], simple["severidad"]) == ("DESVIACION_SIMPLE", "CRITICO")
    h.buffer.close()


def test_un_error_por_variable_y_no_por_lectura(tmp_path):
    h = Harness(tmp_path)
    for _ in range(5):
        h.advance(15 * 60)
        ack_all(h)
    assert len(eventos(h)) == 1
    h.buffer.close()
