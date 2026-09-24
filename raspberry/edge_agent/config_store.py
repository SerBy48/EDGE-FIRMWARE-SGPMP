"""Persistencia local de la configuración vigente por serial.

Sobrevive reinicios de la Raspberry y guarda el último comando aplicado, que es
lo que permite la idempotencia de RF-23 (ver `commands.py`).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceConfig:
    frecuencia_captura_min: int
    intervalo_transmision_min: int
    # Solo si RF-23 llega a enviarlo (opción B del plan, sección 4 punto 2).
    intervalo_heartbeat_min: float | None = None
    comando_id: str | int | None = None
    config_version: str | int | None = None


class ConfigStore:
    def __init__(self, path: Path, defaults: DeviceConfig) -> None:
        self._path = path
        self._defaults = defaults
        self._configs: dict[str, DeviceConfig] = self._load()

    def get(self, serial: str) -> DeviceConfig:
        return self._configs.get(serial, self._defaults)

    def set(self, serial: str, config: DeviceConfig) -> None:
        """Guarda en disco antes de retornar: el ACK solo sale después de esto."""
        configs = {**self._configs, serial: config}
        self._write(configs)
        self._configs = configs

    def _load(self) -> dict[str, DeviceConfig]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {serial: DeviceConfig(**cfg) for serial, cfg in data["serials"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            # Un archivo dañado (ej. corte de luz en una versión sin fsync) no
            # debe dejar el servicio caído: se aparta y se arranca con defaults.
            corrupt = self._path.with_suffix(self._path.suffix + ".corrupt")
            logger.exception("Config local ilegible, se mueve a %s y se usan defaults", corrupt)
            os.replace(self._path, corrupt)
            return {}

    def _write(self, configs: dict[str, DeviceConfig]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {"serials": {serial: asdict(cfg) for serial, cfg in configs.items()}}
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)
