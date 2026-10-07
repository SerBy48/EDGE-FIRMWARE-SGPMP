"""TC-M09-63: el Edge avisa al broker que se desconecta (Last Will y cierre ordenado)."""

import json
import queue

from conftest import SERIAL, make_settings

from edge_agent import mqtt_client

AVISO = {"tipo_mensaje": "DESCONEXION"}


class FakePaho:
    """Lo mínimo de paho.mqtt.client.Client que usa MqttLink."""

    def __init__(self, **_):
        self.will = None
        self.publicados: list[tuple[str, dict]] = []
        self.conectado = True
        self.desconectado = False

    def username_pw_set(self, *_):
        pass

    def reconnect_delay_set(self, *_):
        pass

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, json.loads(payload), qos, retain)

    def is_connected(self):
        return self.conectado

    def publish(self, topic, payload, qos):
        self.publicados.append((topic, json.loads(payload)))
        return type("Info", (), {"wait_for_publish": lambda self, timeout: True})()

    def disconnect(self):
        self.desconectado = True

    def loop_stop(self):
        pass


def _link(tmp_path, monkeypatch, **settings) -> tuple[mqtt_client.MqttLink, FakePaho]:
    monkeypatch.setattr(mqtt_client.mqtt, "Client", FakePaho)
    link = mqtt_client.MqttLink(make_settings(tmp_path, **settings), queue.Queue())
    return link, link._client


def test_declara_el_aviso_como_last_will_en_el_status_del_gateway(tmp_path, monkeypatch):
    _, paho = _link(tmp_path, monkeypatch, serials=(SERIAL, "ESP-1"))
    assert paho.will == (f"sgpmp/{SERIAL}/status", AVISO, 1, False)


def test_un_cierre_ordenado_publica_el_aviso_antes_de_desconectar(tmp_path, monkeypatch):
    link, paho = _link(tmp_path, monkeypatch)
    link.stop()
    assert paho.publicados == [(f"sgpmp/{SERIAL}/status", AVISO)]
    assert paho.desconectado


def test_sin_conexion_no_intenta_publicar_el_aviso(tmp_path, monkeypatch):
    link, paho = _link(tmp_path, monkeypatch)
    paho.conectado = False
    link.stop()
    assert paho.publicados == []
    assert paho.desconectado
