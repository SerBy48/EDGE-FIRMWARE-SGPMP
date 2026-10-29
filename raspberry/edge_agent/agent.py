# | Fecha      | Nombre   | Que se hizo                                                    |
# |------------|----------|----------------------------------------------------------------|
# | 2026-10-29 | Arekkazu | TC-DIS-148 RF-54: el heartbeat lleva metricas_buffer           |
"""Orquestación del edge_agent.

Un único hilo maneja todo el estado: agenda de captura/transmisión/heartbeat
por serial, comandos RF-23, RF-17 (umbrales) y RF-55 (reglas) y vaciado del
buffer. El hilo de paho solo entrega eventos por una cola (`mqtt_client.py`).

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
import math
import queue
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from typing import Any, Protocol

from edge_agent import buffer as buf
from edge_agent.buffer import Buffer
from edge_agent.commands import (
    TIPO_ACK,
    TIPO_ACK_REGLA,
    TIPO_ACK_UMBRAL,
    TIPO_COMANDO_REGLA,
    TIPO_COMANDO_UMBRAL,
    error_ack,
    handle_command,
    handle_regla,
    handle_umbral,
)
from edge_agent.config import Settings
from edge_agent.config_store import ConfigStore, UmbralStore
from edge_agent.historial import RUTA_BUFFER, RUTA_DIRECTA, RUTA_LOCAL, Historial
from edge_agent.mqtt_client import Connected, Event, Message, PubAck
from edge_agent.reglas import disyuntor as dis
from edge_agent.reglas import evento as ev
from edge_agent.reglas.disyuntor import Disyuntor
from edge_agent.reglas.evaluador import evaluar_lote, lecturas_invalidas, normales
from edge_agent.reglas.evento import EventoEdge
from edge_agent.reglas.perfil import Habilitacion
from edge_agent.reglas.store import ReglaStore
from edge_agent.reglas.ventana import EstadoCompuestas
from edge_agent.sources import DataSource, Reading
from edge_agent.system import ntp_sincronizado

logger = logging.getLogger(__name__)

_FLUSH_BATCH = 100
# Tiempo máximo de capturas por vuelta del loop. Con una ráfaga (TC-M03-102:
# muchos nodos vaciando su memoria a la vez) los nodos que no alcanzan quedan
# vencidos para la vuelta siguiente y entretanto se atienden comandos y PUBACK.
# Siempre se hace al menos una captura por vuelta.
PRESUPUESTO_CAPTURAS_S = 0.2
# Si paho no confirmó en este tiempo, la fila vuelve a ser elegible. Puede
# causar un duplicado, que el servidor descarta por `event_id`.
_INFLIGHT_TIMEOUT_S = 300
# Telemetría que sale más tarde que esto respecto de su ventana de
# transmisión se marca como BUFFER_LOCAL (estuvo retenida por falta de red).
_GRACIA_TIEMPO_REAL_S = 60
# El backend rechaza con TIMESTAMP_EXPIRADO un evento BUFFER_LOCAL de más de
# 72 h: se descarta acá en vez de reintentar contra un rechazo seguro.
_ANTIGUEDAD_MAX_EVENTO_S = 72 * 3600
# Comandos que el broker ya despacha (BRK-05) pero que este Edge todavía no
# implementa (RF-65/RF-66): se rechazan con su propio tipo de ACK para que el
# broker resuelva la espera como ERROR en vez de agotar el timeout.
_ACK_NO_SOPORTADO = {
    "CONFIG_MOTOR_IA": "ACK_CONFIG_MOTOR",
    "TIPO_MODELO_ASIGNADO": "ACK_TIPO_MODELO",
}


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
        umbrales: UmbralStore,
        reglas: ReglaStore,
        rf55: Habilitacion | None = None,
        historial: Historial | None = None,
        reloj_sincronizado: Callable[[], bool] = ntp_sincronizado,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._link = link
        self._store = store
        self._buffer = buffer
        self._source = source
        self._umbrales = umbrales
        self._reglas = reglas
        self._historial = historial
        # Sin `tipo_comando` es la configuración RF-23 (caso por defecto).
        self._comandos: dict[str, Callable[[str, dict[str, Any], float], dict[str, Any]]] = {
            TIPO_COMANDO_UMBRAL: self._on_umbral,
            TIPO_COMANDO_REGLA: self._on_regla,
        }
        self._reloj_sincronizado = reloj_sincronizado
        self._clock = clock
        self._inflight: dict[int, tuple[int, float]] = {}  # mid -> (row_id, enviado_en)
        # mid -> (id_evento, origen) de los eventos en vuelo, para el historial.
        self._eventos_en_vuelo: dict[int, tuple[str, str]] = {}
        self._last_reported: dict[str, str] = {}
        # ERROR_CONFIGURACION ya emitidos, por (regla, id, versión, variable):
        # uno solo por configuración rota, no uno por lectura.
        self._errores_reportados: set[tuple[Any, ...]] = set()
        # RF-55.2 (opcional según el perfil de hardware): ventana de
        # correlación y confirmaciones, en memoria. None: solo RF-55.1.
        self._rf55 = rf55 or Habilitacion(True, ())
        self._compuestas = EstadoCompuestas() if self._rf55.rf55_2 else None
        # Capturas vencidas que no entraron en el presupuesto de la vuelta.
        self._capturas_pendientes = False
        # Fallas de evaluación en tiempo de ejecución (TC-M03-088/097).
        self._disyuntor = Disyuntor(settings.rf55_2_fallos_max, settings.rf55_2_reintento_s)
        now = clock()
        self._schedules = {
            serial: self._initial_schedule(serial, now) for serial in settings.serials
        }
        # Tras un reinicio, la config persistida vuelve a ser la deseada para
        # los nodos: convergen solos en su próxima ventana de downlink.
        for serial in settings.serials:
            source.apply_config(serial, store.get(serial))

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
                # Con capturas atrasadas por el presupuesto no se espera a la red.
                event = events.get(block=not self._capturas_pendientes, timeout=0.5)
            except queue.Empty:
                pass
            else:
                pendientes = [event]
                while not events.empty():
                    pendientes.append(events.get_nowait())
                self.handle_events(pendientes)
            self.tick(self._clock())

    def handle_events(self, eventos: list[Event]) -> None:
        """Eventos en el orden en que llegaron.

        Cada racha de PUBACK seguidos se confirma en una sola transacción: tras
        una ráfaga son miles y cada borrado suelto esperaría al disco
        (TC-M03-102). El resto va uno por uno: un comando publica su ACK
        apenas lo guarda.
        """
        for es_puback, grupo in groupby(eventos, key=lambda e: isinstance(e, PubAck)):
            with self._lote() if es_puback else nullcontext():
                for event in grupo:
                    self.handle_event(event, self._clock())

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
                enviado = self._eventos_en_vuelo.pop(mid, None)
                if enviado is not None and self._historial is not None:
                    self._historial.marcar_enviado(enviado[0], origen=enviado[1], ahora=now)
            case _:
                pass  # Disconnected: paho reintenta solo y el buffer retiene.

    def tick(self, now: float) -> None:
        inicio = time.perf_counter()
        capturadas = 0
        self._capturas_pendientes = False
        for serial, sched in self._schedules.items():
            cfg = self._store.get(serial)
            if now >= sched.next_capture:
                if capturadas and time.perf_counter() - inicio > PRESUPUESTO_CAPTURAS_S:
                    # Queda vencida (con su agenda intacta) para la vuelta siguiente.
                    self._capturas_pendientes = True
                    continue
                self._capture(serial, sched.next_tx, now)
                capturadas += 1
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

    @property
    def capturas_pendientes(self) -> bool:
        """Quedaron capturas vencidas para la vuelta siguiente (ráfaga, TC-M03-102)."""
        return self._capturas_pendientes

    def capturar_ahora(self, serial: str, now: float) -> None:
        """Captura fuera de agenda y vacía el buffer: la usa el inyector de QA
        (`scripts/inyectar_lecturas.py`) para ejercitar RF-55 sin esperar ventanas."""
        self._capture(serial, now, now)
        self._flush(now)

    # --- comandos -------------------------------------------------------

    def _on_command(self, topic: str, payload: bytes, now: float) -> None:
        serial = self._serial_from_command_topic(topic)
        if serial is None:
            logger.warning("Comando en topic ajeno a este edge: %s", topic)
            return

        data = _json_object(payload)
        tipo = data.get("tipo_comando") if data is not None else None
        handler = self._comandos.get(tipo) if isinstance(tipo, str) else None
        if tipo is None:
            ack = self._on_configuracion(serial, payload, now)
        elif handler is not None:
            ack = handler(serial, data, now)
        else:
            ack = self._on_no_soportado(serial, tipo, data)

        self._buffer.enqueue(
            serial=serial,
            kind=buf.KIND_ACK,
            topic=self._settings.topic(serial, self._settings.topic_status),
            payload=ack,
            due_at=now,
            now=now,
        )
        self._flush(now)

    def _on_configuracion(self, serial: str, payload: bytes, now: float) -> dict[str, Any]:
        result = handle_command(
            payload,
            self._store.get(serial),
            ahora=now if self._reloj_sincronizado() else None,
            antiguedad_max_s=self._settings.comando_antiguedad_max_s,
        )
        ack = result.ack
        if result.changed:
            try:
                self._store.set(serial, result.config)
            except OSError:
                logger.exception("No se pudo persistir la config de %s", serial)
                ack = error_ack(ack.get("id_comando"), "no se pudo persistir la configuración")
            else:
                self._reschedule(serial, now)
                self._source.apply_config(serial, result.config)
        detalle = ack.get("motivo") or ("aplicado" if result.changed else "sin cambios")
        logger.info("Comando %s → %s (%s)", serial, ack["resultado"], detalle)
        return ack

    def _on_umbral(self, serial: str, data: dict[str, Any], now: float) -> dict[str, Any]:
        result = handle_umbral(
            data,
            self._umbrales.get(data.get("id_umbral_ambiental")),
            ahora=now if self._reloj_sincronizado() else None,
            antiguedad_max_s=self._settings.comando_antiguedad_max_s,
        )
        ack = result.ack
        if result.umbral is not None:
            try:
                self._umbrales.set(result.umbral)
            except OSError:
                logger.exception("No se pudo persistir el umbral recibido por %s", serial)
                ack = error_ack(
                    ack.get("id_comando"), "no se pudo persistir el umbral", tipo=TIPO_ACK_UMBRAL
                )
        detalle = ack.get("motivo") or ("guardado" if result.umbral else "sin cambios")
        logger.info(
            "Umbral %s (%s) → %s (%s)",
            data.get("id_umbral_ambiental"),
            data.get("variable"),
            ack["resultado"],
            detalle,
        )
        return ack

    def _on_regla(self, serial: str, data: dict[str, Any], now: float) -> dict[str, Any]:
        result = handle_regla(
            data,
            self._reglas.get(data.get("id_regla_alerta")),
            ahora=now if self._reloj_sincronizado() else None,
            antiguedad_max_s=self._settings.comando_antiguedad_max_s,
            activas=len(self._reglas.activas()),
        )
        ack = result.ack
        changed = False
        if result.regla is not None:
            try:
                changed = self._reglas.set(result.regla)
            except OSError:
                logger.exception("No se pudo persistir la regla recibida por %s", serial)
                ack = error_ack(
                    ack.get("id_comando"), "no se pudo persistir la regla", tipo=TIPO_ACK_REGLA
                )
        detalle = ack.get("motivo") or ("guardada" if changed else "sin cambios")
        logger.info(
            "Regla %s v%s → %s (%s)",
            data.get("id_regla_alerta"),
            data.get("version_regla"),
            ack["resultado"],
            detalle,
        )
        return ack

    def _on_no_soportado(self, serial: str, tipo: Any, data: dict[str, Any]) -> dict[str, Any]:
        logger.warning("Comando de %s con tipo_comando no soportado: %r", serial, tipo)
        tipo_ack = _ACK_NO_SOPORTADO.get(tipo, TIPO_ACK) if isinstance(tipo, str) else TIPO_ACK
        return error_ack(data.get("id_comando"), "tipo de comando no soportado", tipo=tipo_ack)

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

    def _lote(self) -> AbstractContextManager[None]:
        """Buffer e historial en una transacción cada uno: un fsync por bloque."""
        pila = ExitStack()
        pila.enter_context(self._buffer.lote())
        if self._historial is not None:
            pila.enter_context(self._historial.lote())
        return pila

    def _capture(self, serial: str, due_at: float, now: float) -> None:
        # Todo lo que escribe una captura va junto: una ráfaga de cientos de
        # lecturas no hace un fsync por fila (TC-M03-102).
        with self._lote():
            self._capturar(serial, due_at, now)

    def _capturar(self, serial: str, due_at: float, now: float) -> None:
        readings = self._source.read(serial, _utc(now))
        invalidas = lecturas_invalidas(readings, self._settings.rf55_variables)
        for reading, motivo in invalidas:
            logger.warning(
                "Lectura de %s descartada para RF-55 (%s): %s",
                reading.serial,
                reading.variable,
                motivo,
            )
            if self._historial is not None:
                self._historial.registrar_invalida(reading, motivo=motivo, ahora=now)
        # Las lecturas crudas primero: si el motor de reglas falla, ya están a salvo.
        for reading in readings:
            if not _es_finito(reading.valor_crudo):
                continue  # NaN/None/texto no es telemetría (ni JSON válido)
            self._buffer.enqueue(
                serial=serial,
                kind=buf.KIND_TELEMETRY,
                topic=self._settings.topic(serial, self._settings.topic_telemetry),
                payload=_telemetry_payload(reading),
                due_at=due_at,
                now=now,
            )
        try:
            # La captura completa junta: una compuesta ve las variables que
            # llegaron juntas y prevalece sobre los simples de esas lecturas.
            compuestas = self._compuestas if self._disyuntor_permite(now) else None
            eventos = evaluar_lote(
                readings,
                self._umbrales.todos(),
                self._reglas.activas(),
                estado=compuestas,
                variables=self._settings.rf55_variables,
                ahora=_utc(now),
                version_firmware=self._settings.firmware_version,
            )
            if compuestas is not None:
                self._registrar_rf55_2(compuestas.fallo, now)
            for evento in eventos:
                self._encolar_evento(evento, now)
            if self._historial is not None:
                for reading, umbral in normales(readings, self._umbrales.todos(), eventos):
                    self._historial.registrar_normal(
                        reading, id_umbral=umbral.id_umbral_ambiental, ahora=now
                    )
        except Exception:
            # Un fallo de RF-55 nunca puede tumbar la captura ni el servicio.
            logger.exception("Falló la evaluación RF-55 de %s", serial)

    @property
    def estado_rf55_2(self) -> dict[str, Any]:
        """Estado observable de RF-55.2 (inyector de QA y diagnóstico)."""
        return {
            "habilitado_por_hardware": self._rf55.rf55_2,
            "motivos_hardware": list(self._rf55.motivos),
            "operativo": self._rf55_2_operativo(),
            **self._disyuntor.resumen(),
        }

    def _rf55_2_operativo(self) -> bool:
        return self._rf55.rf55_2 and self._disyuntor.estado == dis.ACTIVO

    def _disyuntor_permite(self, now: float) -> bool:
        return self._compuestas is not None and self._disyuntor.permite(now)

    def _registrar_rf55_2(self, fallo: Exception | None, now: float) -> None:
        transicion = self._disyuntor.registrar(fallo, now)
        d = self._disyuntor
        if transicion == dis.TRANSITORIO:
            logger.warning(
                "RF-55.2 falló en esta captura (%d/%d seguidas): se clasifica con RF-55.1. %r",
                d.fallos_consecutivos,
                d.fallos_max,
                fallo,
            )
        elif transicion == dis.DESHABILITA:
            logger.error(
                "RF-55.2 DESHABILITADO tras %d fallas seguidas; RF-55.1 sigue. "
                "Reintento en %g s. Última falla: %r",
                d.fallos_consecutivos,
                d.reintento_s,
                fallo,
            )
        elif transicion == dis.REINTENTO_FALLIDO:
            logger.warning(
                "RF-55.2 sigue DESHABILITADO: el reintento falló (%r). Próximo en %g s",
                fallo,
                d.reintento_s,
            )
        elif transicion == dis.REACTIVA:
            logger.warning("RF-55.2 REACTIVADO: el reintento evaluó sin fallas")
        if transicion in (dis.DESHABILITA, dis.REACTIVA):
            # Una ventana a medias de antes de la falla no debe correlacionar.
            self._compuestas = EstadoCompuestas()
            if self._link.connected:  # el cambio se ve ya en el heartbeat
                for serial in self._settings.serials:
                    self._send_heartbeat(serial, now)

    def _encolar_evento(self, evento: EventoEdge, now: float) -> None:
        if evento.clasificacion == ev.ERROR_CONFIGURACION:
            meta = evento.metadata
            clave = (
                meta.get("regla_aplicada"),
                meta.get("id_umbral_ambiental", meta.get("id_regla_alerta")),
                meta.get("version_umbral", meta.get("version_regla")),
                meta.get("variable"),
            )
            if clave in self._errores_reportados:
                # Ya se publicó: las lecturas siguientes solo quedan en el
                # historial local, sin clasificación (TC-M03-083/122).
                if self._historial is not None:
                    self._historial.registrar_evento(
                        ev.a_payload(evento, estado_conectividad=self._link.connected),
                        ruta=RUTA_LOCAL,
                        ahora=now,
                    )
                return
            self._errores_reportados.add(clave)
            logger.warning("RF-55: configuración inutilizable %s: %s", clave, meta.get("motivo"))
        # Todo evento pasa por el buffer, también con conexión (RF-56): la fila
        # se borra con el PUBACK y así un corte a mitad de envío no lo pierde.
        # Con conexión sale en este mismo ciclo (ruta DIRECTA, origen TIEMPO_REAL).
        conectado = self._link.connected
        payload = ev.a_payload(evento, estado_conectividad=conectado)
        # El serial es el del nodo dueño de la variable primaria, no el del Gateway.
        self._buffer.enqueue(
            serial=evento.serial,
            kind=buf.KIND_EVENTO,
            topic=self._settings.topic(evento.serial, self._settings.topic_evento),
            payload=payload,
            due_at=now,  # los eventos no esperan la ventana de transmisión
            now=now,
        )
        if self._historial is not None:
            ruta = RUTA_DIRECTA if conectado else RUTA_BUFFER
            self._historial.registrar_evento(payload, ruta=ruta, ahora=now)

    def _flush(self, now: float) -> None:
        if not self._link.connected:
            return
        for row in self._buffer.due(now, _FLUSH_BATCH, exclude=self._sending()):
            payload = row.payload
            late = now - row.due_at > _GRACIA_TIEMPO_REAL_S
            if row.kind == buf.KIND_TELEMETRY:
                payload = {
                    **payload,
                    "timestamp_envio": _iso(now),
                    "origen": "BUFFER_LOCAL" if late else "TIEMPO_REAL",
                }
            elif row.kind == buf.KIND_EVENTO:
                if _expirado(payload, now):
                    logger.warning(
                        "Evento %s con más de 72 h en buffer: se descarta (TIMESTAMP_EXPIRADO)",
                        payload.get("metadata_edge", {}).get("id_evento"),
                    )
                    self._buffer.delete(row.id)
                    if self._historial is not None:
                        self._historial.marcar_descartado(
                            payload["metadata_edge"]["id_evento"],
                            motivo="TIMESTAMP_EXPIRADO",
                            ahora=now,
                        )
                    continue
                payload = {**payload, "origen": ev.BUFFER_LOCAL if late else ev.TIEMPO_REAL}
            mid = self._link.publish(row.topic, json.dumps(payload).encode("utf-8"))
            if mid is None:
                break
            self._inflight[mid] = (row.id, now)
            if row.kind == buf.KIND_EVENTO:
                id_evento = payload["metadata_edge"]["id_evento"]
                self._eventos_en_vuelo[mid] = (id_evento, payload["origen"])

    def _sending(self) -> set[int]:
        return {row_id for row_id, _ in self._inflight.values()}

    def _expire_inflight(self, now: float) -> None:
        for mid, (_, sent_at) in list(self._inflight.items()):
            if now - sent_at > _INFLIGHT_TIMEOUT_S:
                del self._inflight[mid]
                self._eventos_en_vuelo.pop(mid, None)

    # --- heartbeat ------------------------------------------------------

    def _send_heartbeat(self, serial: str, now: float) -> None:
        sched = self._schedules[serial]
        sched.next_heartbeat = now + self.heartbeat_interval_s(serial)
        if not self._source.is_alive(serial, _utc(now), self._max_silence_s(serial)):
            logger.info("Sin heartbeat para %s: no se escucha al nodo", serial)
            return

        # Lo que ya está publicado esperando PUBACK no cuenta como retenido.
        sending = self._sending()
        estado = self._buffer.state(serial, now, exclude=sending)
        ultimo = self._buffer.ultimo_pendiente(serial, now, exclude=sending)
        payload = {
            "tipo_mensaje": "HEARTBEAT",
            "estado_local_buffer": estado,
            "datos_pendientes_buffer": self._buffer.pending_count(serial, now, exclude=sending),
            # RF-54 (TC-DIS-148): el panel del backend clasifica la ocupación (80 % / 95 %).
            # El outbox es uno para toda la Raspberry; lleno, descarta la telemetría más
            # vieja (POLITICA_SOBRESCRITURA). ACKs y eventos pueden excederlo: se acota.
            "metricas_buffer": {
                "capacidad_total": self._buffer.capacidad,
                "capacidad_utilizada": min(self._buffer.total(), self._buffer.capacidad),
                "ultimo_timestamp_pendiente": _iso(ultimo) if ultimo is not None else None,
                "politica_buffer_activa": "POLITICA_SOBRESCRITURA",
            },
            "version_firmware": self._settings.firmware_version,
            "reloj_sincronizado": self._reloj_sincronizado(),
            "fecha_registro": _iso(now),
        }
        payload.update({k: v for k, v in self._source.status(serial).items() if v is not None})
        # RF-55.2 activo o no, por el campo de D11 que el broker ya acepta.
        # `operativo` es falso si se apagó por hardware o lo abrió el disyuntor.
        payload["sensores_operativos"] = [
            *payload.get("sensores_operativos", []),
            {**self._rf55.sensor_operativo(), "operativo": self._rf55_2_operativo()},
        ]
        topic = self._settings.topic(serial, self._settings.topic_heartbeat)
        if self._link.publish(topic, json.dumps(payload).encode("utf-8")) is not None:
            self._last_reported[serial] = estado

    def _max_silence_s(self, serial: str) -> float:
        """Silencio tolerado de los nodos de `serial` antes de dejar de reportarlo.

        Un nodo transmite cada `intervalo_transmision` (y nunca más seguido que
        `frecuencia_captura`): se toleran dos ciclos perdidos.
        """
        cfg = self._store.get(serial)
        return 2 * max(cfg.frecuencia_captura_min, cfg.intervalo_transmision_min) * 60

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


def _json_object(payload: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _expirado(evento: dict[str, Any], now: float) -> bool:
    try:
        captura = datetime.fromisoformat(evento["timestamp_captura"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return False  # que decida el backend
    return now - captura > _ANTIGUEDAD_MAX_EVENTO_S


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
        "metadatos": {**reading.metadatos, "event_id": str(uuid.uuid4())},
    }
    optional = {
        "sensor": reading.sensor,
        "nivel_bateria_pct": reading.nivel_bateria_pct,
        "calidad_senal_rssi": reading.calidad_senal_rssi,
        "calidad_senal_snr": reading.calidad_senal_snr,
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    return payload


def _es_finito(valor: Any) -> bool:
    return isinstance(valor, int | float) and not isinstance(valor, bool) and math.isfinite(valor)


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def _iso(ts: float) -> str:
    return _utc(ts).isoformat()
