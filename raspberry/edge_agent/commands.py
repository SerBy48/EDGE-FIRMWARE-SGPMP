"""Procesamiento de los comandos RF-23 y RF-17 (`sgpmp/<serial>/command`) y sus ACK.

Contrato: `docs/PLAN_DESARROLLO.md` sección 2.2. El ACK base
(`tipo_mensaje`/`resultado`) es el que el broker ya valida; los campos extra
son compatibles hacia atrás porque `ingest_status()` los ignora.

Anti-replay (TC-M09-252, `INTEGRACION_DISPOSITIVOS_RF23.md` del broker): el
comando trae `id_comando` y `emitido_en`. El ACK devuelve `id_comando` (sin él
el broker no puede asociar la confirmación), un `id_comando` ya procesado no se
re-aplica, y con el reloj sincronizado se rechaza un comando demasiado viejo.

RF-17 (INC-M09-104-G29, `INTEGRACION_DISPOSITIVOS_RF17.md` del broker): por el
mismo topic llega el umbral ambiental de una especie, con
`tipo_comando: "UMBRAL_AMBIENTAL"`, y se confirma con `ACK_UMBRAL`. Sin
`tipo_comando` es la configuración RF-23 de siempre.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from edge_agent.config_store import DeviceConfig, Umbral

TIPO_ACK = "ACK_CONFIGURACION"
TIPO_COMANDO_UMBRAL = "UMBRAL_AMBIENTAL"
TIPO_ACK_UMBRAL = "ACK_UMBRAL"
_NIVELES = {"normal", "precaucion", "critico"}


@dataclass(frozen=True)
class CommandResult:
    ack: dict[str, Any]
    config: DeviceConfig
    changed: bool


@dataclass(frozen=True)
class UmbralResult:
    ack: dict[str, Any]
    umbral: Umbral | None  # None: no hay nada que guardar (error, repetido o más viejo)


def handle_command(
    raw: bytes,
    current: DeviceConfig,
    *,
    ahora: float | None = None,
    antiguedad_max_s: float | None = None,
) -> CommandResult:
    """`ahora` (epoch) solo si el reloj está sincronizado: sin reloj fiable no se
    puede juzgar la antigüedad y basta con no re-aplicar un id ya procesado."""
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error(current, None, "payload no es JSON válido")
    if not isinstance(data, dict):
        return _error(current, None, "payload debe ser un objeto JSON")

    # `id_comando` es el campo del broker; `comando_id`, el del diseño M09.
    comando_id = data.get("id_comando", data.get("comando_id"))
    config_version = data.get("config_version")

    # Reentrega QoS 1 o reintento de M09: se re-confirma sin reaplicar.
    if comando_id is not None and comando_id == current.comando_id:
        return CommandResult(_ack("OK", comando_id, current.config_version), current, False)

    if ahora is not None and antiguedad_max_s is not None:
        emitido = _epoch(data.get("emitido_en"))
        if emitido is not None and ahora - emitido > antiguedad_max_s:
            return _error(current, comando_id, "comando vencido: emitido_en demasiado antiguo")

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


def handle_umbral(
    data: dict[str, Any],
    current: Umbral | None,
    *,
    ahora: float | None = None,
    antiguedad_max_s: float | None = None,
) -> UmbralResult:
    """Valida el umbral de `data` contra el vigente con ese id (`current`).

    El ACK OK sale también cuando no hay nada que guardar (reentrega o versión
    más vieja que la vigente): el Edge ya tiene lo último y el broker solo
    necesita la confirmación.
    """
    comando_id = data.get("id_comando")

    def error(motivo: str) -> UmbralResult:
        return UmbralResult(_ack("ERROR", comando_id, None, motivo, tipo=TIPO_ACK_UMBRAL), None)

    def ok() -> dict[str, Any]:
        return _ack("OK", comando_id, None, tipo=TIPO_ACK_UMBRAL)

    if comando_id is not None and current is not None and comando_id == current.comando_id:
        return UmbralResult(ok(), None)

    if ahora is not None and antiguedad_max_s is not None:
        emitido = _epoch(data.get("emitido_en"))
        if emitido is not None and ahora - emitido > antiguedad_max_s:
            return error("comando vencido: emitido_en demasiado antiguo")

    id_umbral = data.get("id_umbral_ambiental")
    variable = data.get("variable")
    unidad = data.get("unidad")
    valor_min = data.get("valor_min")
    valor_max = data.get("valor_max")
    niveles = data.get("niveles")
    version = data.get("version")
    if not _is_positive_int(id_umbral):
        return error("id_umbral_ambiental debe ser un entero > 0")
    if not isinstance(variable, str) or not variable.strip():
        return error("variable es obligatoria")
    if not isinstance(unidad, str):
        return error("unidad debe ser texto")
    if not (_is_number(valor_min) and _is_number(valor_max) and valor_min < valor_max):
        return error("valor_min y valor_max deben ser números con valor_min < valor_max")
    if not isinstance(niveles, list) or not niveles or not all(map(_is_nivel, niveles)):
        return error("niveles debe traer normal/precaucion/critico con límites numéricos")
    if version is not None and _epoch(version) is None:
        return error("version debe ser una fecha ISO 8601 con zona o null")

    # `version` es la fecha_actualizacion del umbral; null (nunca editado) es la
    # más vieja de todas. Una versión anterior a la vigente llegó tarde.
    if current is not None and _version_epoch(version) < _version_epoch(current.version):
        return UmbralResult(ok(), None)

    umbral = Umbral(
        id_umbral_ambiental=id_umbral,
        version=version,
        variable=variable,
        unidad=unidad,
        valor_min=valor_min,
        valor_max=valor_max,
        niveles=tuple(
            {k: n[k] for k in ("nivel", "limite_inferior", "limite_superior")} for n in niveles
        ),
        comando_id=comando_id,
    )
    return UmbralResult(ok(), umbral)


def error_ack(comando_id: Any, motivo: str, *, tipo: str = TIPO_ACK) -> dict[str, Any]:
    return _ack("ERROR", comando_id, None, motivo, tipo=tipo)


def _ack(
    resultado: str,
    comando_id: Any,
    config_version: Any,
    motivo: str | None = None,
    *,
    tipo: str = TIPO_ACK,
) -> dict[str, Any]:
    ack: dict[str, Any] = {"tipo_mensaje": tipo, "resultado": resultado}
    if comando_id is not None:
        ack["id_comando"] = comando_id
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


def _epoch(value: Any) -> float | None:
    # ISO 8601 con zona (el broker manda UTC "+00:00"); sin zona no se puede comparar.
    if not isinstance(value, str):
        return None
    try:
        fecha = datetime.fromisoformat(value)
    except ValueError:
        return None
    return fecha.timestamp() if fecha.tzinfo is not None else None


def _is_older(new: Any, current: Any) -> bool:
    # Solo se puede ordenar si ambas versiones son enteros; si no, se aplica.
    return _is_int(new) and _is_int(current) and new < current


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _is_positive_number(value: Any) -> bool:
    return _is_number(value) and value > 0


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_nivel(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("nivel") in _NIVELES
        and _is_number(value.get("limite_inferior"))
        and _is_number(value.get("limite_superior"))
    )


def _version_epoch(version: str | None) -> float:
    return float("-inf") if version is None else _epoch(version)
