"""Configuración del edge_agent, leída de variables de entorno.

En la Raspberry vienen de `/etc/sgpmp/edge-agent.env` (cargado por systemd con
`EnvironmentFile=`, hito M2). Ningún valor real se versiona: ver
`edge-agent.env.example`.

Los parámetros de negocio (heartbeat, captura, transmisión) son obligatorios y
sin default en código — RF-60 Restricción 1 prohíbe valores fijos.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from edge_agent import __version__


class ConfigError(ValueError):
    """Configuración faltante o inválida; el proceso no debe arrancar."""


@dataclass(frozen=True)
class FakeVariable:
    """Variable que simula `FakeLoraSource` (`nombre` debe existir en BD)."""

    nombre: str
    unidad: str
    minimo: float
    maximo: float


@dataclass(frozen=True)
class Settings:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    mqtt_ca_cert: Path | None
    mqtt_keepalive_s: int
    topic_prefix: str
    topic_telemetry: str
    topic_heartbeat: str
    topic_status: str
    topic_command: str
    serials: tuple[str, ...]
    heartbeat_interval_s: float
    default_frecuencia_captura_min: int
    default_intervalo_transmision_min: int
    buffer_path: Path
    buffer_max_rows: int
    state_path: Path
    source: str
    fake_variables: tuple[FakeVariable, ...]
    firmware_version: str = __version__

    def topic(self, serial: str, suffix: str) -> str:
        return f"{self.topic_prefix}/{serial}/{suffix}"

    def __repr__(self) -> str:
        # La contraseña nunca debe terminar en un log.
        return f"Settings(host={self.mqtt_host!r}, port={self.mqtt_port}, serials={self.serials})"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        serials = tuple(s.strip() for s in _require(env, "EDGE_SERIALS").split(",") if s.strip())
        if not serials:
            raise ConfigError("EDGE_SERIALS no tiene ningún serial")

        ca_cert = env.get("EDGE_MQTT_CA_CERT") or None
        if ca_cert and not Path(ca_cert).is_file():
            raise ConfigError(f"EDGE_MQTT_CA_CERT no existe: {ca_cert}")

        source = env.get("EDGE_SOURCE", "fake")
        if source != "fake":
            # `lora` llega con la Fase 2 (lora_receiver.py).
            raise ConfigError(f"EDGE_SOURCE no soportado todavía: {source!r}")

        return cls(
            mqtt_host=_require(env, "EDGE_MQTT_HOST"),
            mqtt_port=_int(env, "EDGE_MQTT_PORT", 8883 if ca_cert else 1883),
            mqtt_username=_require(env, "EDGE_MQTT_USERNAME"),
            mqtt_password=_require(env, "EDGE_MQTT_PASSWORD"),
            mqtt_client_id=env.get("EDGE_MQTT_CLIENT_ID") or f"edge-{serials[0]}",
            mqtt_ca_cert=Path(ca_cert) if ca_cert else None,
            mqtt_keepalive_s=_int(env, "EDGE_MQTT_KEEPALIVE_S", 60),
            topic_prefix=env.get("EDGE_TOPIC_PREFIX", "sgpmp"),
            topic_telemetry=env.get("EDGE_TOPIC_TELEMETRY", "telemetry"),
            topic_heartbeat=env.get("EDGE_TOPIC_HEARTBEAT", "heartbeat"),
            topic_status=env.get("EDGE_TOPIC_STATUS", "status"),
            topic_command=env.get("EDGE_TOPIC_COMMAND", "command"),
            serials=serials,
            heartbeat_interval_s=_positive_float(env, "EDGE_HEARTBEAT_INTERVAL_S"),
            default_frecuencia_captura_min=_int(env, "EDGE_FRECUENCIA_CAPTURA_MIN"),
            default_intervalo_transmision_min=_int(env, "EDGE_INTERVALO_TRANSMISION_MIN"),
            buffer_path=Path(env.get("EDGE_BUFFER_PATH", "/var/lib/sgpmp-edge/buffer.db")),
            buffer_max_rows=_int(env, "EDGE_BUFFER_MAX_ROWS", 50_000),
            state_path=Path(env.get("EDGE_STATE_PATH", "/var/lib/sgpmp-edge/config.json")),
            source=source,
            fake_variables=_parse_fake_variables(env.get("EDGE_FAKE_VARIABLES", "")),
        )


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Falta la variable de entorno obligatoria {name}")
    return value


def _int(env: Mapping[str, str], name: str, default: int | None = None) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"Falta la variable de entorno obligatoria {name}")
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un entero, llegó {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} debe ser > 0, llegó {value}")
    return value


def _positive_float(env: Mapping[str, str], name: str) -> float:
    raw = _require(env, name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser numérico, llegó {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} debe ser > 0, llegó {value}")
    return value


def _parse_fake_variables(raw: str) -> tuple[FakeVariable, ...]:
    """Formato: `nombre:unidad:min:max;nombre:unidad:min:max`."""
    variables = []
    for item in filter(None, (part.strip() for part in raw.split(";"))):
        parts = item.split(":")
        if len(parts) != 4:
            raise ConfigError(f"EDGE_FAKE_VARIABLES mal formado en {item!r}")
        nombre, unidad, minimo, maximo = parts
        try:
            variables.append(FakeVariable(nombre, unidad, float(minimo), float(maximo)))
        except ValueError as exc:
            raise ConfigError(f"EDGE_FAKE_VARIABLES: rango no numérico en {item!r}") from exc
    return tuple(variables)
