# | Fecha      | Nombre   | Que se hizo                                                     |
# |------------|----------|-----------------------------------------------------------------|
# | 2026-11-02 | Arekkazu | V1 RF-53: detector YOLOv8n en ONNX sin el paquete ultralytics    |
# | 2026-11-02 | Arekkazu | V1 RF-53: captura JPEG con las cajas para ver qué detectó la cámara |
"""Detector de la cascada: el `.onnx` YOLOv8n de M04 (poblacionales V4) con onnxruntime.

Pre y postproceso propios (letterbox, NMS de OpenCV): no se importa `ultralytics`.
Los pesos siguen siendo AGPL-3.0 (metadato del modelo); no van en git y su uso
fuera de DEV espera la decisión D-D del líder.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from edge_vision.cascada import Caja


class Detector:
    def __init__(self, ruta: str, confianza: float = 0.35, iou_nms: float = 0.5) -> None:
        self.sesion = ort.InferenceSession(ruta, providers=["CPUExecutionProvider"])
        entrada = self.sesion.get_inputs()[0]
        self.nombre_entrada = entrada.name
        self.lado = int(entrada.shape[-1])  # 320 en los modelos V4
        self.confianza, self.iou_nms = confianza, iou_nms
        digest = hashlib.sha256(Path(ruta).read_bytes()).hexdigest()[:12]
        self.version = f"{Path(ruta).stem}@{digest}"

    def detectar(self, cuadro: np.ndarray) -> list[Caja]:
        alto, ancho = cuadro.shape[:2]
        r = self.lado / max(alto, ancho)
        nh, nw = round(alto * r), round(ancho * r)
        arriba, izq = (self.lado - nh) // 2, (self.lado - nw) // 2
        lienzo = np.full((self.lado, self.lado, 3), 114, np.uint8)
        lienzo[arriba : arriba + nh, izq : izq + nw] = cv2.resize(cuadro, (nw, nh))
        x = (
            cv2.cvtColor(lienzo, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32)
            / 255
        )
        salida = self.sesion.run(None, {self.nombre_entrada: x})[0][0].T  # [anclas, 4 + clases]
        puntaje = salida[:, 4:].max(axis=1)
        salida, puntaje = salida[puntaje >= self.confianza], puntaje[puntaje >= self.confianza]
        cajas = (
            np.stack(
                [
                    (salida[:, 0] - salida[:, 2] / 2 - izq) / r,
                    (salida[:, 1] - salida[:, 3] / 2 - arriba) / r,
                    salida[:, 2] / r,
                    salida[:, 3] / r,
                ],
                axis=1,
            )
            if len(salida)
            else np.empty((0, 4))
        )
        quedan = cv2.dnn.NMSBoxes(cajas.tolist(), puntaje.tolist(), self.confianza, self.iou_nms)
        return [tuple(float(v) for v in cajas[i]) for i in np.array(quedan).flatten()]


def captura_jpeg(cuadro: np.ndarray, cajas: list[tuple[int, Caja]], ancho_max: int = 640) -> str:
    """Cuadro con cada animal en su caja y su número, en JPEG base64 de ≤ ancho_max px."""
    img = cuadro.copy()
    for id_track, (x, y, w, h) in cajas:
        esquina = (int(x), int(y))
        cv2.rectangle(img, esquina, (int(x + w), int(y + h)), (0, 200, 255), 2)
        cv2.putText(
            img,
            str(id_track),
            (int(x) + 3, int(y) + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 200, 255),
            2,
        )
    if img.shape[1] > ancho_max:
        r = ancho_max / img.shape[1]
        img = cv2.resize(img, (ancho_max, round(img.shape[0] * r)))
    ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if not ok:
        raise ValueError("No se pudo codificar la captura en JPEG.")
    return base64.b64encode(jpeg.tobytes()).decode()
