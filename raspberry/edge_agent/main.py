"""Punto de entrada: `python -m edge_agent` (o `edge-agent` si está instalado)."""

from __future__ import annotations

import logging
import os
import queue
import signal
import threading

from edge_agent.agent import Agent
from edge_agent.buffer import Buffer
from edge_agent.config import ConfigError, Settings
from edge_agent.config_store import ConfigStore, DeviceConfig
from edge_agent.mqtt_client import Event, MqttLink
from edge_agent.sources import FakeLoraSource

logger = logging.getLogger("edge_agent")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EDGE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # Exit != 0 sin traceback: systemd lo registra y reintenta.
        logger.error("Configuración inválida: %s", exc)
        return 2

    store = ConfigStore(
        settings.state_path,
        DeviceConfig(
            frecuencia_captura_min=settings.default_frecuencia_captura_min,
            intervalo_transmision_min=settings.default_intervalo_transmision_min,
        ),
    )
    buffer = Buffer(settings.buffer_path, settings.buffer_max_rows)
    events: queue.Queue[Event] = queue.Queue()
    link = MqttLink(settings, events)
    agent = Agent(settings, link, store, buffer, FakeLoraSource(settings.fake_variables))

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    logger.info("edge_agent %s arrancando: %s", settings.firmware_version, settings)
    link.start()
    try:
        agent.run(events, stop)
    finally:
        link.stop()
        buffer.close()
        logger.info("edge_agent detenido")
    return 0
