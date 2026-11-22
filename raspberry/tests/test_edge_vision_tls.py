# | Fecha      | Nombre   | Que se hizo                                                    |
# |------------|----------|----------------------------------------------------------------|
# | 2026-11-22 | SerBy48  | SEG-EDGE-02: el nodo de visión tampoco cae a MQTT en claro     |
"""El nodo de visión aplica la misma regla de TLS que el edge_agent (SEG-EDGE-02, #29)."""

import pytest

pytest.importorskip("cv2")
pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")

from edge_vision import main as vision  # noqa: E402

ENV = {
    "EDGE_VISION_SERIAL": "CAM-1",
    "EDGE_MQTT_HOST": "broker.test",
    "EDGE_MQTT_USERNAME": "u",
    "EDGE_MQTT_PASSWORD": "p",
}


def test_sin_ca_ni_opt_in_no_conecta():
    with pytest.raises(SystemExit, match="EDGE_MQTT_CA_CERT"):
        vision._conectar(dict(ENV))


def test_ca_y_sin_tls_a_la_vez_no_conecta(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    env = {**ENV, "EDGE_MQTT_CA_CERT": str(ca), "EDGE_MQTT_SIN_TLS": "1"}
    with pytest.raises(SystemExit, match="a la vez"):
        vision._conectar(env)
