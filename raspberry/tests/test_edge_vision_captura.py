# | Fecha      | Nombre   | Que se hizo                                                     |
# |------------|----------|-----------------------------------------------------------------|
# | 2026-11-02 | Arekkazu | V1 RF-53: captura JPEG con cajas                                 |
"""La captura que acompaña a la ventana: JPEG reducido con las cajas dibujadas."""

import base64

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")

from edge_vision.detector import captura_jpeg  # noqa: E402


def test_captura_es_un_jpeg_reducido_con_la_caja_dibujada():
    cuadro = np.zeros((720, 1280, 3), np.uint8)
    jpeg = base64.b64decode(captura_jpeg(cuadro, [(7, (100.0, 100.0, 200.0, 150.0))]))
    assert jpeg[:3] == b"\xff\xd8\xff"
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (360, 640)
    assert img.max() > 0  # la caja quedó dibujada sobre el fondo negro
    assert cuadro.max() == 0  # el cuadro original no se modifica
