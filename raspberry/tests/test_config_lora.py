import pytest

from edge_agent.config import ConfigError, Settings

BASE = {
    "EDGE_MQTT_HOST": "broker.test",
    "EDGE_MQTT_USERNAME": "sgpmp_devices",
    "EDGE_MQTT_PASSWORD": "secreto",
    "EDGE_SERIALS": "IOT-A,IOT-B",
    "EDGE_HEARTBEAT_INTERVAL_S": "300",
    "EDGE_FRECUENCIA_CAPTURA_MIN": "10",
    "EDGE_INTERVALO_TRANSMISION_MIN": "15",
    "EDGE_SOURCE": "lora",
    "EDGE_LORA_FREQ_HZ": "915000000",
    "EDGE_LORA_TX_POWER_DBM": "14",
    "EDGE_LORA_NET_ID": "0x2A",
    "EDGE_LORA_NODES": "1=IOT-A, 2=IOT-B",
    "EDGE_LORA_VARIABLES": "1=temperatura_ambiente:C; 2=humedad_relativa:%",
}


def test_config_lora_completa():
    lora = Settings.from_env(BASE).lora
    assert lora.net_id == 0x2A
    assert lora.nodes == {1: "IOT-A", 2: "IOT-B"}
    assert lora.variables[2] == ("humedad_relativa", "%")
    assert (lora.sf, lora.bw_hz, lora.cr, lora.sync_word) == (9, 125_000, 5, 0x12)
    assert lora.reset_gpio is None


def test_fake_no_trae_lora():
    assert Settings.from_env({**BASE, "EDGE_SOURCE": "fake"}).lora is None


@pytest.mark.parametrize(
    "key", ["EDGE_LORA_FREQ_HZ", "EDGE_LORA_TX_POWER_DBM", "EDGE_LORA_NET_ID", "EDGE_LORA_NODES"]
)
def test_obligatorios_sin_default(key):
    env = {k: v for k, v in BASE.items() if k != key}
    with pytest.raises(ConfigError, match=key):
        Settings.from_env(env)


@pytest.mark.parametrize(
    "override",
    [
        {"EDGE_LORA_NODES": "1=IOT-X"},  # serial fuera de EDGE_SERIALS
        {"EDGE_LORA_NODES": "0=IOT-A"},  # 0 reservado al gateway
        {"EDGE_LORA_NODES": "1=IOT-A,1=IOT-B"},
        {"EDGE_LORA_VARIABLES": "1=temperatura"},  # sin unidad
        {"EDGE_LORA_NET_ID": "300"},
        {"EDGE_SOURCE": "otra"},
    ],
)
def test_config_lora_invalida(override):
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE, **override})
