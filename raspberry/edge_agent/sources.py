"""Fuentes de lecturas de sensores.

`DataSource` es la interfaz que usa el agente. `FakeLoraSource` genera datos
sintéticos para M1/M3; `lora_receiver.py` (Fase 2) la implementará con el
SX1276 real sin cambiar nada del resto del agente.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from edge_agent.config import FakeVariable


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


class DataSource(Protocol):
    def read(self, serial: str, now: datetime) -> list[Reading]:
        """Lecturas nuevas de `serial` desde la última llamada."""
        ...

    def is_alive(self, serial: str, now: datetime) -> bool:
        """Si se escuchó al nodo recientemente.

        Con serial por ESP32, la Raspberry no debe publicar heartbeat de un
        nodo que no escucha: si no, un ESP32 caído seguiría `ACTIVO` (plan, 2.1).
        """
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

    def is_alive(self, serial: str, now: datetime) -> bool:
        return True
