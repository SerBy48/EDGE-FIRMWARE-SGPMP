"""Enlace MQTT persistente con el broker SGPMP (paho-mqtt 2.x).

Los callbacks de paho corren en su propio hilo; acá solo se traducen a eventos
en una cola. Todo el estado (buffer, config) lo maneja el hilo del agente, así
no hay SQLite compartido entre hilos.

Sesión persistente (plan, M1):
- `client_id` fijo: la credencial es compartida, dos clientes con el mismo
  id se desconectan entre sí.
- `clean_session=False` + suscripción QoS 1: Mosquitto encola los comandos
  mientras la Raspberry está desconectada.
- Reconexión automática de paho con backoff exponencial.
"""

from __future__ import annotations

import logging
import queue
import ssl
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from edge_agent.config import Settings

logger = logging.getLogger(__name__)

_RECONNECT_MIN_S = 1
_RECONNECT_MAX_S = 120


@dataclass(frozen=True)
class Connected:
    session_present: bool


@dataclass(frozen=True)
class Disconnected:
    reason: str


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes


@dataclass(frozen=True)
class PubAck:
    mid: int


Event = Connected | Disconnected | Message | PubAck


class MqttLink:
    def __init__(self, settings: Settings, events: queue.Queue[Event]) -> None:
        self._settings = settings
        self._events = events
        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
        )
        self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_ca_cert:
            self._client.tls_set(
                ca_certs=str(settings.mqtt_ca_cert), tls_version=ssl.PROTOCOL_TLS_CLIENT
            )
        self._client.reconnect_delay_set(_RECONNECT_MIN_S, _RECONNECT_MAX_S)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_publish = self._on_publish

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def start(self) -> None:
        logger.info(
            "Conectando a %s:%s como %s (TLS=%s)",
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            self._settings.mqtt_client_id,
            bool(self._settings.mqtt_ca_cert),
        )
        # connect_async + loop_start: si el broker no está disponible al
        # arrancar, paho sigue reintentando en vez de fallar el proceso.
        self._client.connect_async(
            self._settings.mqtt_host,
            self._settings.mqtt_port,
            keepalive=self._settings.mqtt_keepalive_s,
        )
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> int | None:
        """Devuelve el `mid` para esperar el PUBACK, o None si no hay conexión."""
        if not self.connected:
            return None
        info = self._client.publish(topic, payload, qos=qos)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.warning("Publicación rechazada por paho en %s: rc=%s", topic, info.rc)
            return None
        return info.mid

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            # Ej. credencial inválida: paho sigue reintentando con backoff.
            logger.error("Broker rechazó la conexión: %s", reason_code)
            return
        topics = [
            (self._settings.topic(serial, self._settings.topic_command), 1)
            for serial in self._settings.serials
        ]
        # Con sesión persistente la suscripción ya existe; re-suscribir es
        # inocuo y cubre el caso en que el broker perdió la sesión.
        client.subscribe(topics)
        logger.info("Conectado (session_present=%s)", flags.session_present)
        self._events.put(Connected(session_present=flags.session_present))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        logger.warning("Desconectado del broker: %s", reason_code)
        self._events.put(Disconnected(reason=str(reason_code)))

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        self._events.put(Message(topic=msg.topic, payload=msg.payload))

    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        self._events.put(PubAck(mid=mid))
