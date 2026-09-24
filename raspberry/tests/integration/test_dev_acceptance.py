"""Aceptación M3: el edge_agent real contra el broker de un ambiente (dev).

Reemplaza la prueba manual por SSH de DOC_PRUEBA_ACL_EDGE_DEV.docx (P1, P2,
P5 y flujo F1) y agrega lo que esa prueba no cubría: comando entregado con el
agente desconectado (sesión persistente) y vaciado del buffer al reconectar.

Correr con:  pytest -m integration tests/integration -v
El primer serial de EDGE_SERIALS debe existir en modulo9.dispositivos_iot.
Los tests son secuenciales: cada uno parte del estado que dejó el anterior.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from edge_agent.config import Settings
from edge_agent.main import build

from .helpers import api, device_state, pump, requires_env

pytestmark = [pytest.mark.integration, requires_env]


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    base = Settings.from_env()
    workdir = tmp_path_factory.mktemp("edge")
    settings = replace(
        base,
        serials=base.serials[:1],
        buffer_path=workdir / "buffer.db",
        state_path=workdir / "config.json",
    )
    runtime = build(settings)
    runtime.link.start()
    yield runtime
    # Deja el dispositivo con la config por defecto del .env.
    try:
        send_command(
            runtime,
            settings.default_frecuencia_captura_min,
            settings.default_intervalo_transmision_min,
        )
    finally:
        runtime.close()


@pytest.fixture(scope="module")
def serial(rt):
    return rt.settings.serials[0]


def send_command(rt, frecuencia: int, intervalo: int) -> dict:
    """POST /v1/commands mientras el agente sigue atendiendo en este hilo."""
    body = {
        "origen": "configuracion",
        "serial": rt.settings.serials[0],
        "frecuencia_captura": frecuencia,
        "intervalo_transmision": intervalo,
    }
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(api, "/commands", body, 90)
        pump(rt, 90, until=future.done)
        return future.result()


def test_p1_p2_conecta_suscribe_y_queda_activo(rt, serial):
    assert pump(rt, 60, until=lambda: rt.link.connected), "no conectó al broker"

    deadline = time.monotonic() + 180
    state = None
    while time.monotonic() < deadline:
        pump(rt, 5)
        state = device_state(serial)
        if state == "ACTIVO":
            break
    assert state == "ACTIVO", f"el servidor no lo marca ACTIVO (estado={state})"


def test_f1_p5_comando_aplicado_con_ack_automatico(rt, serial):
    response = send_command(rt, 5, 30)

    assert response["estado"] == "APLICADA", response
    config = rt.store.get(serial)
    assert (config.frecuencia_captura_min, config.intervalo_transmision_min) == (5, 30)


def test_comando_entregado_con_agente_desconectado(rt, serial):
    """Sesión persistente: Mosquitto encola el comando y lo entrega al volver."""
    rt.link.stop()
    pump(rt, 2)

    response = send_command(rt, 15, 30)
    if response["estado"] == "PENDIENTE":
        pytest.skip("el servidor ya lo considera offline y no publicó el comando")
    assert response["estado"] == "NO_CONF", response  # nadie respondió en 30 s

    rt.link.start()
    delivered = pump(rt, 60, until=lambda: rt.store.get(serial).frecuencia_captura_min == 15)
    assert delivered, "el comando encolado no llegó al reconectar (¿sesión persistente?)"
    # El ACK tardío también llega al broker (PUBACK) aunque ya no cambie la UI.
    assert pump(rt, 30, until=lambda: rt.buffer.pending_count(serial, time.time()) == 0)


def test_buffer_se_vacia_al_reconectar(rt, serial):
    assert send_command(rt, 1, 1)["estado"] == "APLICADA"
    rt.link.stop()
    pump(rt, 130)  # >= 2 capturas y una ventana de transmisión sin red
    retained = rt.buffer.pending_count(serial, time.time())
    assert retained > 0, "no hubo telemetría retenida (¿EDGE_FAKE_VARIABLES vacío?)"

    rt.link.start()
    drained = pump(rt, 60, until=lambda: rt.buffer.pending_count(serial, time.time()) == 0)
    assert drained, f"quedaron {rt.buffer.pending_count(serial, time.time())} sin PUBACK"
