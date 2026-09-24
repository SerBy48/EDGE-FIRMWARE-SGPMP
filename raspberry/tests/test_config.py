import pytest

from edge_agent.config import ConfigError, Settings

BASE_ENV = {
    "EDGE_MQTT_HOST": "broker.test",
    "EDGE_MQTT_USERNAME": "sgpmp_devices",
    "EDGE_MQTT_PASSWORD": "secreto",
    "EDGE_SERIALS": "IOT-A, IOT-B",
    "EDGE_HEARTBEAT_INTERVAL_S": "300",
    "EDGE_FRECUENCIA_CAPTURA_MIN": "10",
    "EDGE_INTERVALO_TRANSMISION_MIN": "15",
}


def test_defaults_de_conexion():
    s = Settings.from_env(BASE_ENV)
    assert s.serials == ("IOT-A", "IOT-B")
    assert s.mqtt_port == 1883
    assert s.mqtt_client_id == "edge-IOT-A"
    assert s.topic("IOT-A", s.topic_command) == "sgpmp/IOT-A/command"


@pytest.mark.parametrize(
    "missing",
    ["EDGE_HEARTBEAT_INTERVAL_S", "EDGE_FRECUENCIA_CAPTURA_MIN", "EDGE_MQTT_PASSWORD"],
)
def test_parametros_obligatorios_sin_default(missing):
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        Settings.from_env(env)


def test_tls_cambia_puerto_por_defecto(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    s = Settings.from_env({**BASE_ENV, "EDGE_MQTT_CA_CERT": str(ca)})
    assert s.mqtt_port == 8883
    assert s.mqtt_ca_cert == ca


def test_ca_inexistente_falla():
    with pytest.raises(ConfigError, match="CA_CERT"):
        Settings.from_env({**BASE_ENV, "EDGE_MQTT_CA_CERT": "/no/existe.pem"})


def test_password_no_aparece_en_repr():
    assert "secreto" not in repr(Settings.from_env(BASE_ENV))


def test_variables_fake():
    s = Settings.from_env({**BASE_ENV, "EDGE_FAKE_VARIABLES": "temp:C:20:30; hum:%:40:90"})
    assert [v.nombre for v in s.fake_variables] == ["temp", "hum"]
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE_ENV, "EDGE_FAKE_VARIABLES": "temp:C:20"})
