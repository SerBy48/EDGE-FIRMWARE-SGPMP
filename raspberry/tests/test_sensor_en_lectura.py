# | Fecha      | Nombre   | Que se hizo                                                    |
# |------------|----------|----------------------------------------------------------------|
# | 2026-11-01 | Arekkazu | RF-23/RF-22: el `sensor` viaja en la telemetría y en el evento |
"""El broker resuelve el sensor de cada lectura por su nombre: sin él, un dispositivo con
varios sensores rechaza la lectura (ERROR_SENSOR). Aquí se comprueba que el nombre
configurado llega a la telemetría y a `variables_involucradas` del evento RF-55."""

from datetime import UTC, datetime

from edge_agent.agent import _telemetry_payload
from edge_agent.config import FakeVariable
from edge_agent.reglas.evento import _variable
from edge_agent.sources import FakeLoraSource

AHORA = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)


def test_fake_con_sensor_lo_envia_en_telemetria_y_evento():
    fuente = FakeLoraSource((FakeVariable("Temperatura Corporal", "°C", 38.5, 38.5, "SEN-TEMP"),))
    (lectura,) = fuente.read("NODO-1", AHORA)
    assert _telemetry_payload(lectura)["sensor"] == "SEN-TEMP"
    assert _variable(lectura)["sensor"] == "SEN-TEMP"


def test_sin_sensor_no_se_envia_el_campo():
    fuente = FakeLoraSource((FakeVariable("Humedad Relativa", "%", 60, 60),))
    (lectura,) = fuente.read("NODO-1", AHORA)
    assert "sensor" not in _telemetry_payload(lectura)
    assert "sensor" not in _variable(lectura)
