"""Punto de entrada: `python -m edge_agent` (o `edge-agent` si está instalado).

`--check-config` valida la configuración y sale, sin conectarse: lo usa
`scripts/install.sh` antes de habilitar el servicio.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import threading
from dataclasses import dataclass

from edge_agent.agent import Agent
from edge_agent.buffer import Buffer
from edge_agent.config import ConfigError, Settings
from edge_agent.config_store import ConfigStore, DeviceConfig
from edge_agent.mqtt_client import Event, MqttLink
from edge_agent.sources import FakeLoraSource
from edge_agent.systemd import SystemdNotifier

logger = logging.getLogger("edge_agent")


@dataclass
class Runtime:
    """Componentes ensamblados; también lo usan los tests de integración (M3)."""

    settings: Settings
    events: queue.Queue[Event]
    link: MqttLink
    buffer: Buffer
    store: ConfigStore
    agent: Agent

    def close(self) -> None:
        self.link.stop()
        self.buffer.close()


def build(settings: Settings) -> Runtime:
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
    return Runtime(settings, events, link, buffer, store, agent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge_agent")
    parser.add_argument("--check-config", action="store_true", help="validar configuración y salir")
    args = parser.parse_args(argv)

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
    if args.check_config:
        logger.info("Configuración válida: %s", settings)
        return 0

    runtime = build(settings)
    notifier = SystemdNotifier()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    logger.info("edge_agent %s arrancando: %s", settings.firmware_version, settings)
    runtime.link.start()
    notifier.ready()
    try:
        runtime.agent.run(runtime.events, stop, on_loop=notifier.watchdog)
    finally:
        notifier.stopping()
        runtime.close()
        logger.info("edge_agent detenido")
    return 0
