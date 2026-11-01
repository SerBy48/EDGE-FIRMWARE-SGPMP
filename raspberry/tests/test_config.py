# | Fecha      | Nombre   | Que se hizo                                                    |
# |------------|----------|----------------------------------------------------------------|
# | 2026-11-01 | Arekkazu | RF-23/RF-22: `sensor` opcional en EDGE_FAKE_VARIABLES          |
# | 2026-11-01 | SerBy48  | RF-23/RF-55: avisos por mapa RF-55 con errata o regla incompleta |
import pytest

from edge_agent.config import ConfigError, Settings, avisos_configuracion

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


def test_variables_fake_con_sensor():
    s = Settings.from_env({**BASE_ENV, "EDGE_FAKE_VARIABLES": "temp:C:20:30:SEN-TEMP; hum:%:40:90"})
    assert [(v.nombre, v.sensor) for v in s.fake_variables] == [("temp", "SEN-TEMP"), ("hum", None)]
    with pytest.raises(ConfigError, match="sensor vacío"):
        Settings.from_env({**BASE_ENV, "EDGE_FAKE_VARIABLES": "temp:C:20:30: "})


def test_version_firmware_del_archivo_version(tmp_path):
    version = tmp_path / "VERSION"
    version.write_text("v0.2.0-3-gabc1234\n")
    s = Settings.from_env({**BASE_ENV, "EDGE_VERSION_FILE": str(version)})
    assert s.firmware_version == "v0.2.0+3.gabc1234"


def test_version_firmware_sin_archivo_usa_la_del_paquete(tmp_path):
    from edge_agent import __version__

    s = Settings.from_env({**BASE_ENV, "EDGE_VERSION_FILE": str(tmp_path / "no-existe")})
    assert s.firmware_version == f"v{__version__}"


@pytest.mark.parametrize(
    ("describe", "esperada"),
    [
        ("v0.2.0", "v0.2.0"),
        ("v0.2.0-rc.1", "v0.2.0-rc.1"),
        ("v0.2.0-3-gabc1234", "v0.2.0+3.gabc1234"),
        ("v0.2.0-rc.1-3-gabc1234", "v0.2.0-rc.1+3.gabc1234"),
        ("v0.2.0-dirty", "v0.2.0+dirty"),
        ("v0.2.0-3-gabc1234-dirty", "v0.2.0+3.gabc1234.dirty"),
        ("v0.2.0-rc.1-dirty", "v0.2.0-rc.1+dirty"),
        # Sin tag alcanzable `--always` devuelve solo el hash.
        ("f889231", "v{v}+gf889231"),
        ("f889231-dirty", "v{v}+gf889231.dirty"),
        ("", "v{v}"),
        # Lo que no se reconoce va tal cual: el backend lo marca no comparable.
        ("experimental", "experimental"),
        ("release-2-gabc1234", "release-2-gabc1234"),
    ],
)
def test_version_firmware_semver(tmp_path, describe, esperada):
    from edge_agent import __version__

    version = tmp_path / "VERSION"
    version.write_text(describe + "\n")
    s = Settings.from_env({**BASE_ENV, "EDGE_VERSION_FILE": str(version)})
    assert s.firmware_version == esperada.format(v=__version__)


def test_avisos_de_arranque_fake():
    sin_sensor = {**BASE_ENV, "EDGE_FAKE_VARIABLES": "Temperatura Ambiental:°C:20:30"}
    avisos = avisos_configuracion(Settings.from_env(sin_sensor))
    assert any("sin `sensor`" in a for a in avisos)
    assert any("EDGE_RF55_VARIABLES vacía" in a for a in avisos)

    completo = {
        **BASE_ENV,
        "EDGE_FAKE_VARIABLES": (
            "Temperatura Ambiental:°C:20:30:SEN-TEMP;Humedad Relativa:%:40:90:SEN-HUM"
        ),
        "EDGE_RF55_VARIABLES": (
            "TEMPERATURA_AMBIENTAL=Temperatura Ambiental;HUMEDAD_RELATIVA=Humedad Relativa"
        ),
    }
    assert avisos_configuracion(Settings.from_env(completo)) == []


@pytest.mark.parametrize(
    "fake, rf55, esperado",
    [
        (
            "Temperatura Ambiental:°C:20:30:T;Humedad Relativa:%:40:90:H",
            "TEMPERATURA_AMBIENTAL=Temperatura Ambiental",
            "ESTRES_TERMICO no disparará",
        ),
        (
            "Temperatura Ambiental:°C:20:30:T;Humedad Relativa:%:40:90:H",
            "TEMPERATURA_AMBIENTAL=Temperatura Ambiente;HUMEDAD_RELATIVA=Humedad Relativa",
            "apunta a 'Temperatura Ambiente'",
        ),
        (
            "Temperatura Ambiental:°C:20:30:T;Humedad Relativa:%:40:90:H",
            "TEMPERATURA_AMBIENTAL=Temperatura;HUMEDAD_RELATIVA=Humedad",
            "apunta a 'Humedad'",
        ),
        (
            "temperatura_ambiente:°C:20:30:T;Humedad Relativa:%:40:90:H",
            "",
            "ESTRES_TERMICO no disparará",
        ),
    ],
    ids=["mapa-parcial", "errata-en-un-nombre", "errata-en-ambos", "mezcla-sin-mapa"],
)
def test_avisos_de_regla_compuesta_que_no_dispararia(fake, rf55, esperado):
    env = {**BASE_ENV, "EDGE_FAKE_VARIABLES": fake, "EDGE_RF55_VARIABLES": rf55}
    avisos = avisos_configuracion(Settings.from_env(env))
    assert any(esperado in a for a in avisos)
