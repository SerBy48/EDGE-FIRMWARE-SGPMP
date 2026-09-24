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
from edge_agent.sources import DataSource, FakeLoraSource
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
    source: DataSource

    def start(self) -> None:
        start = getattr(self.source, "start", None)
        if start is not None:
            start()  # gateway LoRa: inicializa la radio y su hilo
        self.link.start()

    def close(self) -> None:
        self.link.stop()
        stop = getattr(self.source, "stop", None)
        if stop is not None:
            stop()
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
    source = _build_source(settings)
    agent = Agent(settings, link, store, buffer, source)
    return Runtime(settings, events, link, buffer, store, agent, source)


def _build_source(settings: Settings) -> DataSource:
    if settings.lora is None:
        return FakeLoraSource(settings.fake_variables)
    # Import diferido: spidev/gpiozero solo existen en la Raspberry.
    from edge_agent.lora.gateway import LoraGateway, LoraVariable
    from edge_agent.lora.sx1276 import SX1276, RadioConfig, gpio_reset, open_spi

    lora = settings.lora
    reset = gpio_reset(lora.reset_gpio) if lora.reset_gpio is not None else None
    radio = SX1276(open_spi(lora.spi_bus, lora.spi_device), reset=reset)
    return LoraGateway(
        radio,
        RadioConfig(
            freq_hz=lora.freq_hz,
            tx_power_dbm=lora.tx_power_dbm,
            sf=lora.sf,
            bw_hz=lora.bw_hz,
            cr=lora.cr,
            sync_word=lora.sync_word,
        ),
        lora.net_id,
        lora.nodes,
        {code: LoraVariable(*var) for code, var in lora.variables.items()},
        downlink_delay_s=lora.downlink_delay_ms / 1000,
    )


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
    runtime.start()
    notifier.ready()
    try:
        runtime.agent.run(runtime.events, stop, on_loop=notifier.watchdog)
    finally:
        notifier.stopping()
        runtime.close()
        logger.info("edge_agent detenido")
    return 0
