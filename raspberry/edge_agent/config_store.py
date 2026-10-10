"""Persistencia local de la configuración vigente por serial y de los umbrales RF-17.

Sobrevive reinicios de la Raspberry y guarda el último comando aplicado, que es
lo que permite la idempotencia de RF-23 y RF-17 (ver `commands.py`).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

K = TypeVar("K")
V = TypeVar("V")


@dataclass(frozen=True)
class DeviceConfig:
    frecuencia_captura_min: int
    intervalo_transmision_min: int
    # Solo si RF-23 llega a enviarlo (opción B del plan, sección 4 punto 2).
    intervalo_heartbeat_min: float | None = None
    comando_id: str | int | None = None
    config_version: str | int | None = None
    # Cámaras (RF-23 v1.1): cuadros por segundo de captura, 1-60. Las cámaras no
    # usan frecuencia/intervalo; quedan con sus defaults.
    fps: int | None = None


@dataclass(frozen=True)
class Umbral:
    """Umbral ambiental RF-17 de una especie para una `variable` de telemetría."""

    id_umbral_ambiental: int
    # `fecha_actualizacion` del umbral en la plataforma; None si nunca se editó.
    version: str | None
    variable: str
    unidad: str
    valor_min: float
    valor_max: float
    niveles: tuple[dict[str, Any], ...]
    comando_id: str | int | None = None


class ConfigStore:
    def __init__(self, path: Path, defaults: DeviceConfig) -> None:
        self._path = path
        self._defaults = defaults
        self._configs: dict[str, DeviceConfig] = _leer(
            path, "serials", lambda data: {s: DeviceConfig(**cfg) for s, cfg in data.items()}
        )

    def get(self, serial: str) -> DeviceConfig:
        return self._configs.get(serial, self._defaults)

    def set(self, serial: str, config: DeviceConfig) -> None:
        """Guarda en disco antes de retornar: el ACK solo sale después de esto."""
        configs = {**self._configs, serial: config}
        _escribir(self._path, {"serials": {s: asdict(c) for s, c in configs.items()}})
        self._configs = configs


class UmbralStore:
    """Umbrales RF-17 vigentes en este Edge, indexados por `id_umbral_ambiental`.

    Quedan guardados para la evaluación local de las lecturas (RF-55): con el
    Edge desconectado sigue rigiendo el último que llegó (TC-M09-63).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._umbrales: dict[int, Umbral] = _leer(
            path,
            "umbrales",
            lambda data: {
                int(i): Umbral(**{**u, "niveles": tuple(u["niveles"])}) for i, u in data.items()
            },
        )

    def get(self, id_umbral_ambiental: int) -> Umbral | None:
        return self._umbrales.get(id_umbral_ambiental)

    def set(self, umbral: Umbral) -> None:
        """Guarda en disco antes de retornar: el ACK solo sale después de esto."""
        umbrales = {**self._umbrales, umbral.id_umbral_ambiental: umbral}
        _escribir(self._path, {"umbrales": {str(i): asdict(u) for i, u in umbrales.items()}})
        self._umbrales = umbrales


def _leer(path: Path, clave: str, construir: Callable[[dict[str, Any]], dict[K, V]]) -> dict[K, V]:
    if not path.exists():
        return {}
    try:
        return construir(json.loads(path.read_text(encoding="utf-8"))[clave])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Un archivo dañado (ej. corte de luz en una versión sin fsync) no
        # debe dejar el servicio caído: se aparta y se arranca con defaults.
        corrupt = path.with_suffix(path.suffix + ".corrupt")
        logger.exception("Estado local ilegible, se mueve a %s y se usan defaults", corrupt)
        os.replace(path, corrupt)
        return {}


def _escribir(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
