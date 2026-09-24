"""Fuentes de lecturas de sensores.

`DataSource` es la interfaz que usa el agente. `FakeLoraSource` genera datos
sintéticos (M1/M3, desarrollo); `lora.gateway.LoraGateway` la implementa con
el SX1276 real (Fase 2). El agente no distingue entre ambas.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from edge_agent.config import FakeVariable

if TYPE_CHECKING:
    from edge_agent.config_store import DeviceConfig


@dataclass(frozen=True)
class Reading:
    serial: str
    variable: str
    valor_crudo: float
    unidad: str
    timestamp_captura: datetime
    sensor: str | None = None
    nivel_bateria_pct: float | None = None
    calidad_senal_rssi: float | None = None
    calidad_senal_snr: float | None = None
    metadatos: dict[str, Any] = field(default_factory=dict)


class DataSource(Protocol):
    def read(self, serial: str, now: datetime) -> list[Reading]:
        """Lecturas nuevas de `serial` desde la última llamada."""
        ...

    def is_alive(self, serial: str, now: datetime, max_silence_s: float) -> bool:
        """Si se escuchó al nodo en los últimos `max_silence_s` segundos.

        Con serial por ESP32, la Raspberry no debe publicar heartbeat de un
        nodo que no escucha: si no, un ESP32 caído seguiría `ACTIVO` (plan, 2.1).
        """
        ...

    def status(self, serial: str) -> dict[str, Any]:
        """Campos extra del heartbeat MQTT (batería, RSSI, SNR) si se conocen."""
        ...

    def apply_config(self, serial: str, config: DeviceConfig) -> None:
        """Config vigente de `serial`, para propagarla a los nodos (downlink)."""
        ...


class FakeLoraSource:
    def __init__(self, variables: tuple[FakeVariable, ...], rng: random.Random | None = None):
        self._variables = variables
        self._rng = rng or random.Random()

    def read(self, serial: str, now: datetime) -> list[Reading]:
        return [
            Reading(
                serial=serial,
                variable=var.nombre,
                valor_crudo=round(self._rng.uniform(var.minimo, var.maximo), 2),
                unidad=var.unidad,
                timestamp_captura=now,
            )
            for var in self._variables
        ]

    def is_alive(self, serial: str, now: datetime, max_silence_s: float) -> bool:
        return True

    def status(self, serial: str) -> dict[str, Any]:
        return {}

    def apply_config(self, serial: str, config: DeviceConfig) -> None:
        pass
