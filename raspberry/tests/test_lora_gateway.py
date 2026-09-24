from datetime import UTC, datetime

import pytest
from lora_fakes import FakeSx1276Spi

from edge_agent.config_store import DeviceConfig
from edge_agent.lora import protocol as p
from edge_agent.lora.gateway import LoraGateway, LoraVariable
from edge_agent.lora.sx1276 import SX1276, RadioConfig

NET = 0x2A
NOW = 1_750_000_000.0


class Env:
    def __init__(self, nodes=None):
        self.now = NOW
        self.spi = FakeSx1276Spi()
        radio = SX1276(self.spi, sleep=lambda s: None)
        self.gw = LoraGateway(
            radio,
            RadioConfig(freq_hz=915_000_000, tx_power_dbm=14),
            NET,
            nodes or {1: "IOT-A", 2: "IOT-B"},
            {1: LoraVariable("temperatura_ambiente", "C"), 2: LoraVariable("humedad", "%")},
            downlink_delay_s=0,
            clock=lambda: self.now,
            sleep=lambda s: None,
        )
        radio.init(RadioConfig(freq_hz=915_000_000, tx_power_dbm=14))
        radio.receive()
        self.radio = radio

    def rx(self, node, seq, message, net=NET, rssi_raw=100):
        self.spi.inject(p.build(net, node, seq, message), rssi_raw=rssi_raw)
        self.gw.handle_packet(self.radio.poll())

    def downlinks(self):
        return [p.parse(raw) for raw in self.spi.transmitted]

    @property
    def dt(self):
        return datetime.fromtimestamp(self.now, UTC)


@pytest.fixture
def env():
    return Env()


def estado(frec=10, interv=15, bat=80, flags=0):
    return p.Estado(bat, 0, frec, interv, 1, 0, flags)


def test_telemetria_se_traduce_a_lecturas(env):
    env.rx(1, 5, p.Telemetria(120, (p.Measurement(1, 21.5), p.Measurement(2, 60.0))))

    readings = env.gw.read("IOT-A", env.dt)
    assert [(x.variable, x.valor_crudo, x.unidad) for x in readings] == [
        ("temperatura_ambiente", 21.5, "C"),
        ("humedad", 60.0, "%"),
    ]
    assert readings[0].timestamp_captura.timestamp() == NOW - 120
    assert readings[0].calidad_senal_rssi == -57
    assert readings[0].metadatos["lora"]["node_id"] == 1
    assert env.gw.read("IOT-A", env.dt) == []  # se drena
    assert env.gw.read("IOT-B", env.dt) == []


def test_code_sin_mapear_se_omite(env):
    env.rx(1, 1, p.Telemetria(0, (p.Measurement(99, 1.0), p.Measurement(1, 2.0))))
    assert [x.variable for x in env.gw.read("IOT-A", env.dt)] == ["temperatura_ambiente"]


def test_tramas_ajenas_se_descartan(env):
    env.rx(1, 1, estado(), net=0x99)  # otro sitio
    env.rx(77, 1, estado())  # nodo no configurado
    env.spi.inject(b"\x01\x02\x03basura")
    env.gw.handle_packet(env.radio.poll())
    assert env.gw.descartadas == 3
    assert not env.gw.is_alive("IOT-A", env.dt, 600)


def test_duplicados_y_perdidas_por_seq(env):
    env.rx(1, 10, p.Telemetria(0, (p.Measurement(1, 1.0),)))
    env.rx(1, 10, p.Telemetria(0, (p.Measurement(1, 1.0),)))  # repetida
    env.rx(1, 13, p.Telemetria(0, (p.Measurement(1, 2.0),)))  # se perdieron 11 y 12
    readings = env.gw.read("IOT-A", env.dt)
    assert len(readings) == 2
    assert readings[-1].metadatos["lora"]["perdidas"] == 2

    env.rx(1, 0, estado())  # reinicio del nodo: salto enorme, no es pérdida
    env.rx(1, 1, p.Telemetria(0, (p.Measurement(1, 3.0),)))
    assert env.gw.read("IOT-A", env.dt)[-1].metadatos["lora"]["perdidas"] == 2


def test_config_desactualizada_dispara_downlink(env):
    env.gw.apply_config("IOT-A", DeviceConfig(5, 30))
    env.rx(1, 1, estado(frec=10, interv=15))

    [(frame, msg)] = env.downlinks()
    assert frame.node_id == 1 and frame.net_id == NET
    assert (msg.frecuencia_captura_min, msg.intervalo_transmision_min) == (5, 30)
    assert env.spi.mode == 5  # volvió a RX continuo tras transmitir

    # El nodo confirma: el próximo ESTADO ya coincide y no hay más downlinks.
    env.rx(1, 2, p.AckConfig(msg.cfg_version, p.ResultadoConfig.OK))
    env.rx(1, 3, estado(frec=5, interv=30))
    assert len(env.downlinks()) == 1


def test_downlink_se_reintenta_en_cada_estado_hasta_converger(env):
    env.gw.apply_config("IOT-A", DeviceConfig(5, 30))
    env.rx(1, 1, estado())
    env.rx(1, 2, estado())  # el CONFIG anterior se perdió
    assert len(env.downlinks()) == 2


def test_config_igual_no_genera_downlink(env):
    env.gw.apply_config("IOT-A", DeviceConfig(10, 15))
    env.rx(1, 1, estado(frec=10, interv=15))
    assert env.downlinks() == []


def test_serial_por_sitio_propaga_a_todos_los_nodos():
    env = Env(nodes={1: "IOT-SITIO", 2: "IOT-SITIO"})
    env.gw.apply_config("IOT-SITIO", DeviceConfig(5, 30))
    env.rx(1, 1, estado())
    env.rx(2, 1, estado())
    assert sorted(frame.node_id for frame, _ in env.downlinks()) == [1, 2]


def test_is_alive_y_status(env):
    assert not env.gw.is_alive("IOT-A", env.dt, 600)
    assert env.gw.is_alive("SIN-NODOS", env.dt, 600)  # serial propio de la RPi
    env.rx(1, 1, estado(bat=42), rssi_raw=80)

    assert env.gw.is_alive("IOT-A", env.dt, 600)
    assert env.gw.status("IOT-A") == {
        "calidad_senal_rssi": -77.0,
        "calidad_senal_snr": 8.0,
        "nivel_bateria_pct": 42.0,
    }
    env.now += 601
    assert not env.gw.is_alive("IOT-A", env.dt, 600)


def test_hilo_del_gateway_recibe_y_se_detiene():
    import threading
    import time

    spi = FakeSx1276Spi()
    gw = LoraGateway(
        SX1276(spi),
        RadioConfig(freq_hz=915_000_000, tx_power_dbm=14),
        NET,
        {1: "IOT-A"},
        {1: LoraVariable("temperatura_ambiente", "C")},
        poll_interval_s=0.001,
    )
    gw.start()
    try:
        spi.inject(p.build(NET, 1, 1, p.Telemetria(0, (p.Measurement(1, 5.0),))))
        deadline = time.monotonic() + 2
        readings = []
        while not readings and time.monotonic() < deadline:
            readings = gw.read("IOT-A", datetime.now(UTC))
            time.sleep(0.01)
        assert [x.valor_crudo for x in readings] == [5.0]
    finally:
        gw.stop()
    assert not any(t.name == "lora-gateway" for t in threading.enumerate())
