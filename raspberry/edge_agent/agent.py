"""Orquestación del edge_agent.

Un único hilo maneja todo el estado: agenda de captura/transmisión/heartbeat
por serial, comandos RF-23 y vaciado del buffer. El hilo de paho solo entrega
eventos por una cola (`mqtt_client.py`).

Reglas de `docs/PLAN_DESARROLLO.md` sección 2:
- heartbeat_efectivo = min(HEARTBEAT_INTERVAL, frecuencia_captura / 2),
  recalculado con cada comando (RF-60 Restricción 12).
- Heartbeats directos, nunca por el buffer. Al reconectar: heartbeat con el
  estado del buffer → vaciado → heartbeat INACTIVO al terminar.
- ACK siempre, aunque pasen más de 30 s; la config se persiste antes.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from edge_agent import buffer as buf
from edge_agent.buffer import Buffer
from edge_agent.commands import error_ack, handle_command
from edge_agent.config import Settings
from edge_agent.config_store import ConfigStore
from edge_agent.mqtt_client import Connected, Event, Message, PubAck
from edge_agent.sources import DataSource, Reading
from edge_agent.system import ntp_sincronizado

logger = logging.getLogger(__name__)

_FLUSH_BATCH = 100
# Si paho no confirmó en este tiempo, la fila vuelve a ser elegible. Puede
# causar un duplicado, que el servidor descarta por `event_id`.
_INFLIGHT_TIMEOUT_S = 300
# Telemetría que sale más tarde que esto respecto de su ventana de
# transmisión se marca como BUFFER_LOCAL (estuvo retenida por falta de red).
_GRACIA_TIEMPO_REAL_S = 60


class Link(Protocol):
    @property
    def connected(self) -> bool: ...

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> int | None: ...


@dataclass
class _Schedule:
    next_capture: float
    next_tx: float
    next_heartbeat: float


class Agent:
    def __init__(
        self,
        settings: Settings,
        link: Link,
        store: ConfigStore,
        buffer: Buffer,
        source: DataSource,
        *,
        reloj_sincronizado: Callable[[], bool] = ntp_sincronizado,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._link = link
        self._store = store
        self._buffer = buffer
        self._source = source
        self._reloj_sincronizado = reloj_sincronizado
        self._clock = clock
        self._inflight: dict[int, tuple[int, float]] = {}  # mid -> (row_id, enviado_en)
        self._last_reported: dict[str, str] = {}
        now = clock()
        self._schedules = {
            serial: self._initial_schedule(serial, now) for serial in settings.serials
        }

    def heartbeat_interval_s(self, serial: str) -> float:
        cfg = self._store.get(serial)
        if cfg.intervalo_heartbeat_min is not None:
            base = cfg.intervalo_heartbeat_min * 60
        else:
            base = self._settings.heartbeat_interval_s
        return min(base, cfg.frecuencia_captura_min * 60 / 2)

    def run(
        self,
        events: queue.Queue[Event],
        stop: threading.Event,
        on_loop: Callable[[], None] | None = None,
    ) -> None:
        """Loop principal. `on_loop` se llama en cada vuelta (watchdog de systemd)."""
        while not stop.is_set():
            if on_loop is not None:
                on_loop()
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                pass
            else:
                self.handle_event(event, self._clock())
                while not events.empty():
                    self.handle_event(events.get_nowait(), self._clock())
            self.tick(self._clock())

    def handle_event(self, event: Event, now: float) -> None:
        match event:
            case Connected():
                for serial in self._settings.serials:
                    self._send_heartbeat(serial, now)
                self._flush(now)
            case Message(topic=topic, payload=payload):
                self._on_command(topic, payload, now)
            case PubAck(mid=mid):
                entry = self._inflight.pop(mid, None)
                if entry is not None:
                    self._buffer.delete(entry[0])
            case _:
                pass  # Disconnected: paho reintenta solo y el buffer retiene.

    def tick(self, now: float) -> None:
        for serial, sched in self._schedules.items():
            cfg = self._store.get(serial)
            if now >= sched.next_capture:
                self._capture(serial, sched.next_tx, now)
                sched.next_capture = _advance(
                    sched.next_capture, cfg.frecuencia_captura_min * 60, now
                )
            if now >= sched.next_tx:
                sched.next_tx = _advance(sched.next_tx, cfg.intervalo_transmision_min * 60, now)
        # Vaciar antes del heartbeat: si coincide con la ventana de
        # transmisión, no debe reportar ACTIVO por datos que salen ya mismo.
        self._expire_inflight(now)
        self._flush(now)
        if self._link.connected:
            for serial, sched in self._schedules.items():
                if now >= sched.next_heartbeat:
                    self._send_heartbeat(serial, now)
        self._report_sync_finished(now)

    # --- comandos -------------------------------------------------------

    def _on_command(self, topic: str, payload: bytes, now: float) -> None:
        serial = self._serial_from_command_topic(topic)
        if serial is None:
            logger.warning("Comando en topic ajeno a este edge: %s", topic)
            return

        result = handle_command(payload, self._store.get(serial))
        ack = result.ack
        if result.changed:
            try:
                self._store.set(serial, result.config)
            except OSError:
                logger.exception("No se pudo persistir la config de %s", serial)
                ack = error_ack(ack.get("comando_id"), "no se pudo persistir la configuración")
            else:
                self._reschedule(serial, now)
        detalle = ack.get("motivo") or ("aplicado" if result.changed else "sin cambios")
        logger.info("Comando %s → %s (%s)", serial, ack["resultado"], detalle)

        self._buffer.enqueue(
            serial=serial,
            kind=buf.KIND_ACK,
            topic=self._settings.topic(serial, self._settings.topic_status),
            payload=ack,
            due_at=now,
            now=now,
        )
        self._flush(now)

    def _serial_from_command_topic(self, topic: str) -> str | None:
        prefix = f"{self._settings.topic_prefix}/"
        suffix = f"/{self._settings.topic_command}"
        if not (topic.startswith(prefix) and topic.endswith(suffix)):
            return None
        serial = topic[len(prefix) : -len(suffix)]
        return serial if serial in self._schedules else None

    # --- agenda ---------------------------------------------------------

    def _initial_schedule(self, serial: str, now: float) -> _Schedule:
        cfg = self._store.get(serial)
        return _Schedule(
            next_capture=now,
            next_tx=now + cfg.intervalo_transmision_min * 60,
            next_heartbeat=now,
        )

    def _reschedule(self, serial: str, now: float) -> None:
        # min(): un intervalo más corto rige de inmediato; uno más largo, al
        # cumplirse el período en curso.
        cfg = self._store.get(serial)
        sched = self._schedules[serial]
        sched.next_capture = min(sched.next_capture, now + cfg.frecuencia_captura_min * 60)
        next_tx = now + cfg.intervalo_transmision_min * 60
        if next_tx < sched.next_tx:
            sched.next_tx = next_tx
            self._buffer.advance_due(serial, next_tx)
        sched.next_heartbeat = min(sched.next_heartbeat, now + self.heartbeat_interval_s(serial))

    # --- telemetría y buffer --------------------------------------------

    def _capture(self, serial: str, due_at: float, now: float) -> None:
        for reading in self._source.read(serial, _utc(now)):
            self._buffer.enqueue(
                serial=serial,
                kind=buf.KIND_TELEMETRY,
                topic=self._settings.topic(serial, self._settings.topic_telemetry),
                payload=_telemetry_payload(reading),
                due_at=due_at,
                now=now,
            )

    def _flush(self, now: float) -> None:
        if not self._link.connected:
            return
        for row in self._buffer.due(now, _FLUSH_BATCH, exclude=self._sending()):
            payload = row.payload
            if row.kind == buf.KIND_TELEMETRY:
                late = now - row.due_at > _GRACIA_TIEMPO_REAL_S
                payload = {
                    **payload,
                    "timestamp_envio": _iso(now),
                    "origen": "BUFFER_LOCAL" if late else "TIEMPO_REAL",
                }
            mid = self._link.publish(row.topic, json.dumps(payload).encode("utf-8"))
            if mid is None:
                break
            self._inflight[mid] = (row.id, now)

    def _sending(self) -> set[int]:
        return {row_id for row_id, _ in self._inflight.values()}

    def _expire_inflight(self, now: float) -> None:
        for mid, (_, sent_at) in list(self._inflight.items()):
            if now - sent_at > _INFLIGHT_TIMEOUT_S:
                del self._inflight[mid]

    # --- heartbeat ------------------------------------------------------

    def _send_heartbeat(self, serial: str, now: float) -> None:
        sched = self._schedules[serial]
        sched.next_heartbeat = now + self.heartbeat_interval_s(serial)
        if not self._source.is_alive(serial, _utc(now)):
            logger.info("Sin heartbeat para %s: no se escucha al nodo", serial)
            return

        # Lo que ya está publicado esperando PUBACK no cuenta como retenido.
        sending = self._sending()
        estado = self._buffer.state(serial, now, exclude=sending)
        payload = {
            "tipo_mensaje": "HEARTBEAT",
            "estado_local_buffer": estado,
            "datos_pendientes_buffer": self._buffer.pending_count(serial, now, exclude=sending),
            "version_firmware": self._settings.firmware_version,
            "reloj_sincronizado": self._reloj_sincronizado(),
            "fecha_registro": _iso(now),
        }
        topic = self._settings.topic(serial, self._settings.topic_heartbeat)
        if self._link.publish(topic, json.dumps(payload).encode("utf-8")) is not None:
            self._last_reported[serial] = estado

    def _report_sync_finished(self, now: float) -> None:
        """BUFFER_ACTIVO → ACTIVO en el servidor apenas se vacía el buffer.

        A diferencia del heartbeat periódico, acá se exige el PUBACK de todo:
        "sincronización terminada" significa que el broker ya lo tiene.
        """
        if not self._link.connected:
            return
        for serial in self._settings.serials:
            reported = self._last_reported.get(serial)
            if reported in (buf.ESTADO_ACTIVO, buf.ESTADO_LLENO) and (
                self._buffer.state(serial, now) == buf.ESTADO_INACTIVO
            ):
                self._send_heartbeat(serial, now)


def _advance(previous: float, period: float, now: float) -> float:
    # Si el proceso estuvo detenido, no se recupera en ráfaga: se reprograma.
    nxt = previous + period
    return nxt if nxt > now else now + period


def _telemetry_payload(reading: Reading) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "variable": reading.variable,
        "valor_crudo": reading.valor_crudo,
        "unidad": reading.unidad,
        "timestamp_captura": reading.timestamp_captura.isoformat(),
        "metadatos": {"event_id": str(uuid.uuid4())},
    }
    optional = {
        "sensor": reading.sensor,
        "nivel_bateria_pct": reading.nivel_bateria_pct,
        "calidad_senal_rssi": reading.calidad_senal_rssi,
        "calidad_senal_snr": reading.calidad_senal_snr,
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    return payload


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def _iso(ts: float) -> str:
    return _utc(ts).isoformat()
