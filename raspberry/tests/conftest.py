from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from edge_agent.agent import Agent
from edge_agent.buffer import Buffer
from edge_agent.config import FakeVariable, Settings
from edge_agent.config_store import ConfigStore, DeviceConfig
from edge_agent.sources import FakeLoraSource

SERIAL = "IOT-TEST-001"
T0 = 1_750_000_000.0


def make_settings(tmp_path: Path, **overrides) -> Settings:
    settings = Settings(
        mqtt_host="broker.test",
        mqtt_port=1883,
        mqtt_username="sgpmp_devices",
        mqtt_password="secreto",
        mqtt_client_id=f"edge-{SERIAL}",
        mqtt_ca_cert=None,
        mqtt_keepalive_s=60,
        topic_prefix="sgpmp",
        topic_telemetry="telemetry",
        topic_heartbeat="heartbeat",
        topic_status="status",
        topic_command="command",
        serials=(SERIAL,),
        heartbeat_interval_s=300,
        default_frecuencia_captura_min=10,
        default_intervalo_transmision_min=15,
        buffer_path=tmp_path / "buffer.db",
        buffer_max_rows=1000,
        state_path=tmp_path / "config.json",
        source="fake",
        fake_variables=(FakeVariable("temperatura_ambiente", "C", 20, 30),),
    )
    return replace(settings, **overrides)


class FakeLink:
    """Sustituto de MqttLink: registra publicaciones y entrega mids."""

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.published: list[tuple[str, dict]] = []
        self._next_mid = 1

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> int | None:
        if not self.connected:
            return None
        self.published.append((topic, json.loads(payload)))
        mid = self._next_mid
        self._next_mid += 1
        return mid

    def on(self, suffix: str) -> list[dict]:
        return [p for t, p in self.published if t.endswith("/" + suffix)]

    def take(self) -> list[tuple[str, dict]]:
        out, self.published = self.published, []
        return out


class Harness:
    def __init__(self, tmp_path: Path, connected: bool = True, **overrides) -> None:
        self.tmp_path = tmp_path
        self.settings = make_settings(tmp_path, **overrides)
        self.now = T0
        self.link = FakeLink(connected)
        self.buffer = Buffer(self.settings.buffer_path, self.settings.buffer_max_rows)
        self.store = ConfigStore(
            self.settings.state_path,
            DeviceConfig(
                self.settings.default_frecuencia_captura_min,
                self.settings.default_intervalo_transmision_min,
            ),
        )
        self.agent = Agent(
            self.settings,
            self.link,
            self.store,
            self.buffer,
            FakeLoraSource(self.settings.fake_variables),
            reloj_sincronizado=lambda: True,
            clock=lambda: self.now,
        )

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.agent.tick(self.now)


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    yield h
    h.buffer.close()
