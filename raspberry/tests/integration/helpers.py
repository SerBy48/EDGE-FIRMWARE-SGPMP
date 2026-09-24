"""Utilidades de los tests de aceptación contra un ambiente real (M3).

Variables de entorno, además de las EDGE_* del agente:
- EDGE_IT_API_URL:   base del API del broker, ej. https://host/v1
- EDGE_IT_API_TOKEN: token Bearer de servicio (el mismo que usa el backend)
- EDGE_IT_GATEWAY_USERNAME / EDGE_IT_GATEWAY_PASSWORD: opcionales (P6/E1)
"""

from __future__ import annotations

import json
import os
import queue
import time
import urllib.request
from collections.abc import Callable

import pytest

requires_env = pytest.mark.skipif(
    not (os.environ.get("EDGE_IT_API_URL") and os.environ.get("EDGE_MQTT_HOST")),
    reason="tests contra ambiente real: definir EDGE_IT_API_URL y las variables EDGE_*",
)


def api(path: str, body: dict | None = None, timeout: float = 60) -> dict | list:
    url = os.environ["EDGE_IT_API_URL"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {os.environ['EDGE_IT_API_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def device_state(serial: str) -> str | None:
    for device in api("/devices"):
        if device.get("serial") == serial:
            return device.get("estado_actual")
    return None


def pump(runtime, seconds: float, until: Callable[[], bool] | None = None) -> bool:
    """Corre el loop del agente en este hilo (SQLite no se comparte entre hilos)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            event = runtime.events.get(timeout=0.2)
        except queue.Empty:
            pass
        else:
            runtime.agent.handle_event(event, time.time())
        runtime.agent.tick(time.time())
        if until is not None and until():
            return True
    return until() if until is not None else True
