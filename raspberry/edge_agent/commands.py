"""Procesamiento del comando RF-23 (`sgpmp/<serial>/command`) y armado del ACK.

Contrato: `docs/PLAN_DESARROLLO.md` sección 2.2. El ACK base
(`tipo_mensaje`/`resultado`) es el que el broker ya valida; los campos extra
son compatibles hacia atrás porque `ingest_status()` los ignora.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from edge_agent.config_store import DeviceConfig

TIPO_ACK = "ACK_CONFIGURACION"


@dataclass(frozen=True)
class CommandResult:
    ack: dict[str, Any]
    config: DeviceConfig
    changed: bool


def handle_command(raw: bytes, current: DeviceConfig) -> CommandResult:
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(current, None, "payload no es JSON válido")
    if not isinstance(data, dict):
        return _error(current, None, "payload debe ser un objeto JSON")

    comando_id = data.get("comando_id")
    config_version = data.get("config_version")

    # Reentrega QoS 1 o reintento de M09: se re-confirma sin reaplicar.
    if comando_id is not None and comando_id == current.comando_id:
        return CommandResult(_ack("OK", comando_id, current.config_version), current, False)

    # Llegó un comando más viejo que el vigente (cola persistente + reintentos).
    if _is_older(config_version, current.config_version):
        return CommandResult(_ack("OK", comando_id, current.config_version), current, False)

    frecuencia = data.get("frecuencia_captura")
    intervalo = data.get("intervalo_transmision")
    heartbeat = data.get("intervalo_heartbeat")
    if not _is_positive_int(frecuencia):
        return _error(current, comando_id, "frecuencia_captura debe ser un entero > 0")
    if not _is_positive_int(intervalo):
        return _error(current, comando_id, "intervalo_transmision debe ser un entero > 0")
    if heartbeat is not None and not _is_positive_number(heartbeat):
        return _error(current, comando_id, "intervalo_heartbeat debe ser un número > 0")

    new = replace(
        current,
        frecuencia_captura_min=frecuencia,
        intervalo_transmision_min=intervalo,
        intervalo_heartbeat_min=heartbeat,
        comando_id=comando_id,
        config_version=config_version,
    )
    return CommandResult(_ack("OK", comando_id, config_version), new, new != current)


def error_ack(comando_id: Any, motivo: str) -> dict[str, Any]:
    return _ack("ERROR", comando_id, None, motivo)


def _ack(
    resultado: str,
    comando_id: Any,
    config_version: Any,
    motivo: str | None = None,
) -> dict[str, Any]:
    ack: dict[str, Any] = {"tipo_mensaje": TIPO_ACK, "resultado": resultado}
    if comando_id is not None:
        ack["comando_id"] = comando_id
        # Solo se reporta versión cuando el servidor usa el contrato extendido.
        if config_version is not None:
            ack["config_version_aplicada"] = config_version
    if motivo is not None:
        ack["motivo"] = motivo
    # event_id se fija acá y viaja en el buffer: un reenvío lleva el mismo id.
    ack["event_id"] = str(uuid.uuid4())
    return ack


def _error(current: DeviceConfig, comando_id: Any, motivo: str) -> CommandResult:
    return CommandResult(_ack("ERROR", comando_id, None, motivo), current, False)


def _is_older(new: Any, current: Any) -> bool:
    # Solo se puede ordenar si ambas versiones son enteros; si no, se aplica.
    return _is_int(new) and _is_int(current) and new < current


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0
