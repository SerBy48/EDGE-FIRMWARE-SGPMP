"""Evento Edge de RF-55: lo que se publica en `sgpmp/<serial>/evento`.

Contrato: `PLAN_RF55_00_CONTEXTO_Y_CONTRATOS.md` §5. El backend ignora sin
error los campos que su DTO no declara (§5.3), así que todo lo que RF-55
necesita preservar (regla aplicada, versiones, valores derivados) va en
`metadata_edge`, que se persiste íntegro.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from edge_agent.sources import Reading

NORMAL = "NORMAL"
DESVIACION_SIMPLE = "DESVIACION_SIMPLE"
DESVIACION_COMPUESTA = "DESVIACION_COMPUESTA"
ERROR_CONFIGURACION = "ERROR_CONFIGURACION"

LEVE = "LEVE"
MODERADO = "MODERADO"
CRITICO = "CRITICO"

# `origen` del evento: EDGE_AGREGADO es válido en telemetría, aquí no (§5.2).
TIEMPO_REAL = "TIEMPO_REAL"
BUFFER_LOCAL = "BUFFER_LOCAL"


@dataclass(frozen=True)
class EventoEdge:
    clasificacion: str
    severidad: str | None
    # La primera es la variable que disparó: el broker toma de ella el
    # `sensor_id` raíz, y su serial es el del topic.
    lecturas: tuple[Reading, ...]
    timestamp_procesamiento: datetime
    tiempo_procesamiento_ms: float
    version_firmware: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id_evento: str = field(default_factory=lambda: f"edge-evt-{uuid4()}")

    @property
    def serial(self) -> str:
        return self.lecturas[0].serial


def nuevo_evento(
    clasificacion: str,
    severidad: str | None,
    lecturas: Sequence[Reading],
    *,
    ahora: datetime,
    inicio: float,
    fin: float,
    version_firmware: str,
    metadata: dict[str, Any],
) -> EventoEdge:
    """`inicio`/`fin`: `time.perf_counter()` alrededor de la evaluación (medido, no estimado)."""
    return EventoEdge(
        clasificacion=clasificacion,
        severidad=severidad,
        lecturas=tuple(lecturas),
        timestamp_procesamiento=ahora,
        tiempo_procesamiento_ms=round((fin - inicio) * 1000, 3),
        version_firmware=version_firmware,
        metadata=metadata,
    )


def a_payload(
    evento: EventoEdge, *, origen: str = TIEMPO_REAL, estado_conectividad: bool = True
) -> dict[str, Any]:
    """Cuerpo exacto de §5.1; `origen` lo reescribe el vaciado del buffer."""
    payload: dict[str, Any] = {
        "clasificacion_rf55": evento.clasificacion,
        "variables_involucradas": [_variable(r) for r in evento.lecturas],
        "timestamp_captura": iso_z(evento.lecturas[0].timestamp_captura),
        "timestamp_procesamiento_edge": iso_z(evento.timestamp_procesamiento),
        "origen": origen,
        "estado_conectividad": estado_conectividad,
        "metadata_edge": {
            "id_evento": evento.id_evento,
            **evento.metadata,
            "tiempo_procesamiento_ms": evento.tiempo_procesamiento_ms,
            "version_firmware": evento.version_firmware,
        },
    }
    if evento.severidad is not None:
        payload["severidad"] = evento.severidad
    return payload


def iso_z(fecha: datetime) -> str:
    """ISO-8601 en UTC con sufijo `Z` (una fecha sin zona se toma como UTC)."""
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return fecha.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _variable(reading: Reading) -> dict[str, Any]:
    variable: dict[str, Any] = {
        "tipo_variable": reading.variable,
        "valor": reading.valor_crudo,
        "unidad": reading.unidad,
        "timestamp_captura": iso_z(reading.timestamp_captura),
        # `sensor` y `serial` no los persiste hoy el backend, pero el broker
        # los usa para resolver el `sensor_id` de cada variable (§5.3).
        "serial": reading.serial,
    }
    if reading.sensor is not None:
        variable["sensor"] = reading.sensor
    return variable
