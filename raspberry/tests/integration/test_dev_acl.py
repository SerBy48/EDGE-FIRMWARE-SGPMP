"""ACL de la credencial compartida de dispositivos (P3, P4, P6, P7, E1, E2).

Versión automática de las verificaciones de DOC_PRUEBA_ACL_EDGE_DEV.docx.
Usa MQTT 5 para que las publicaciones no autorizadas devuelvan PUBACK 135.
P6/E1 requieren EDGE_IT_GATEWAY_USERNAME/PASSWORD; sin ellas se omiten.
"""

from __future__ import annotations

import os
import queue
import uuid

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.enums import CallbackAPIVersion

from .helpers import requires_env

pytestmark = [pytest.mark.integration, requires_env]

SERIAL = "TEST-ACL-001"
AJENO = "SERIAL-AJENO-P7"
QUIET_S = 4


class Probe:
    """Cliente MQTT 5 síncrono para pruebas de permisos."""

    def __init__(self, username: str | None, password: str | None) -> None:
        self.messages: queue.Queue = queue.Queue()
        self.pubacks: queue.Queue = queue.Queue()
        self.connack: queue.Queue = queue.Queue()
        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"acl-probe-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv5,
        )
        if username:
            self.client.username_pw_set(username, password)
        ca = os.environ.get("EDGE_MQTT_CA_CERT")
        if ca:
            self.client.tls_set(ca_certs=ca)
        self.client.on_connect = lambda c, u, f, rc, p: self.connack.put(rc)
        self.client.on_message = lambda c, u, m: self.messages.put((m.topic, m.payload))
        self.client.on_publish = lambda c, u, mid, rc, p: self.pubacks.put(rc)

    def connect(self):
        host = os.environ["EDGE_MQTT_HOST"]
        port = int(os.environ.get("EDGE_MQTT_PORT") or 1883)
        self.client.connect(host, port)
        self.client.loop_start()
        return self.connack.get(timeout=10)

    def publish(self, topic: str, payload: str = '{"acl":1}'):
        self.client.publish(topic, payload, qos=1)
        return self.pubacks.get(timeout=10)

    def silent(self, seconds: float = QUIET_S) -> bool:
        try:
            self.messages.get(timeout=seconds)
        except queue.Empty:
            return True
        return False

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


@pytest.fixture
def device():
    probe = Probe(os.environ["EDGE_MQTT_USERNAME"], os.environ["EDGE_MQTT_PASSWORD"])
    assert not probe.connect().is_failure
    yield probe
    probe.close()


@pytest.fixture
def gateway():
    user = os.environ.get("EDGE_IT_GATEWAY_USERNAME")
    if not user:
        pytest.skip("sin credencial del gateway (EDGE_IT_GATEWAY_USERNAME)")
    probe = Probe(user, os.environ.get("EDGE_IT_GATEWAY_PASSWORD"))
    assert not probe.connect().is_failure
    yield probe
    probe.close()


def test_p3_dispositivo_no_lee_telemetria(device):
    device.client.subscribe("sgpmp/#", qos=1)
    device.publish(f"sgpmp/{SERIAL}/telemetry")
    assert device.silent()


def test_p4_dispositivo_no_publica_comandos(device):
    assert device.publish(f"sgpmp/{SERIAL}/command").value == 135


def test_p7_dispositivo_no_ve_otros_seriales(device):
    for suffix in ("telemetry", "heartbeat", "status"):
        device.client.subscribe(f"sgpmp/+/{suffix}", qos=1)
    for suffix in ("telemetry", "heartbeat", "status"):
        assert not device.publish(f"sgpmp/{AJENO}/{suffix}").is_failure
    assert device.silent()


def test_p6_gateway_recibe_telemetria(device, gateway):
    gateway.client.subscribe("sgpmp/+/telemetry", qos=1)
    gateway.silent(1)
    device.publish(f"sgpmp/{SERIAL}/telemetry", '{"test":6}')
    topic, _ = gateway.messages.get(timeout=10)
    assert topic == f"sgpmp/{SERIAL}/telemetry"


def test_e1_gateway_no_publica_telemetria(gateway):
    assert gateway.publish(f"sgpmp/{SERIAL}/telemetry").value == 135


def test_e2_conexion_anonima_rechazada():
    probe = Probe(None, None)
    try:
        assert probe.connect().is_failure
    finally:
        probe.close()
