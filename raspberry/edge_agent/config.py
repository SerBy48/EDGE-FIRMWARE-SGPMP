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
class LoraSettings:
    """Radio y mapeo del gateway LoRa (docs/PROTOCOLO_LORA.md §2–3)."""

    freq_hz: int
    tx_power_dbm: int
    sf: int
    bw_hz: int
    cr: int
    sync_word: int
    net_id: int
    nodes: dict[int, str]  # node_id -> serial MQTT
    variables: dict[int, tuple[str, str]]  # code -> (nombre, unidad)
    spi_bus: int
    spi_device: int
    reset_gpio: int | None
    downlink_delay_ms: int


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
    lora: LoraSettings | None = None
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
        if source not in ("fake", "lora"):
            raise ConfigError(f"EDGE_SOURCE debe ser 'fake' o 'lora', llegó {source!r}")
        lora = parse_lora_settings(env, serials) if source == "lora" else None

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
            lora=lora,
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


def parse_lora_settings(
    env: Mapping[str, str], serials: tuple[str, ...] | None = None
) -> LoraSettings:
    """`serials=None` omite validar contra EDGE_SERIALS (monitor de enlace)."""
    # Frecuencia y potencia: sin default, dependen de la regulación (ANE).
    nodes = _parse_mapping(_require(env, "EDGE_LORA_NODES"), "EDGE_LORA_NODES", ",")
    parsed_nodes: dict[int, str] = {}
    for key, serial in nodes.items():
        node_id = _parse_int_value(key, "EDGE_LORA_NODES", 1, 0xFFFE)
        if serials is not None and serial not in serials:
            raise ConfigError(f"EDGE_LORA_NODES: el serial {serial!r} no está en EDGE_SERIALS")
        parsed_nodes[node_id] = serial

    variables: dict[int, tuple[str, str]] = {}
    raw_vars = _parse_mapping(_require(env, "EDGE_LORA_VARIABLES"), "EDGE_LORA_VARIABLES", ";")
    for key, value in raw_vars.items():
        code = _parse_int_value(key, "EDGE_LORA_VARIABLES", 0, 0xFF)
        nombre, sep, unidad = value.partition(":")
        if not sep or not nombre:
            raise ConfigError(f"EDGE_LORA_VARIABLES: se espera code=nombre:unidad en {value!r}")
        variables[code] = (nombre, unidad)

    reset = env.get("EDGE_LORA_RESET_GPIO", "").strip()
    return LoraSettings(
        freq_hz=_int(env, "EDGE_LORA_FREQ_HZ"),
        tx_power_dbm=_int(env, "EDGE_LORA_TX_POWER_DBM"),
        sf=_int(env, "EDGE_LORA_SF", 9),
        bw_hz=_int(env, "EDGE_LORA_BW_HZ", 125_000),
        cr=_int(env, "EDGE_LORA_CR", 5),
        sync_word=_int_in(env, "EDGE_LORA_SYNC_WORD", "0x12", 0, 0xFF),
        net_id=_parse_int_value(_require(env, "EDGE_LORA_NET_ID"), "EDGE_LORA_NET_ID", 0, 0xFF),
        nodes=parsed_nodes,
        variables=variables,
        spi_bus=_int_in(env, "EDGE_LORA_SPI_BUS", "0", 0, 9),
        spi_device=_int_in(env, "EDGE_LORA_SPI_DEVICE", "0", 0, 9),
        reset_gpio=_parse_int_value(reset, "EDGE_LORA_RESET_GPIO", 0, 53) if reset else None,
        downlink_delay_ms=_int_in(env, "EDGE_LORA_DOWNLINK_DELAY_MS", "50", 0, 1000),
    )


def _parse_mapping(raw: str, name: str, separator: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in raw.split(separator))):
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ConfigError(f"{name} mal formado en {item!r} (se espera clave=valor)")
        if key.strip() in result:
            raise ConfigError(f"{name}: clave repetida {key.strip()!r}")
        result[key.strip()] = value.strip()
    if not result:
        raise ConfigError(f"{name} está vacío")
    return result


def _int_in(env: Mapping[str, str], name: str, default: str, minimo: int, maximo: int) -> int:
    return _parse_int_value(env.get(name, "").strip() or default, name, minimo, maximo)


def _parse_int_value(raw: str, name: str, minimo: int, maximo: int) -> int:
    try:
        value = int(raw.strip(), 0)  # acepta 0x12
    except ValueError as exc:
        raise ConfigError(f"{name}: {raw!r} no es un entero") from exc
    if not minimo <= value <= maximo:
        raise ConfigError(f"{name}: {value} fuera de rango [{minimo}, {maximo}]")
    return value
