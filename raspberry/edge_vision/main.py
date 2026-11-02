# | Fecha      | Nombre   | Que se hizo                                                     |
# |------------|----------|-----------------------------------------------------------------|
# | 2026-11-02 | Arekkazu | V1 RF-53: nodo de visión que publica el sobre D4 por MQTT        |
"""Nodo con cámara: cuadro → detector → seguimiento → métricas → sobre D4 en `<prefijo>/<serial>/vision`.

La fuente es una cámara (`EDGE_VISION_FUENTE=0`), un flujo (`rtsp://…`) o un archivo de
video, que se repite en bucle y a velocidad real: así un video hace de cámara simulada.
El broker reenvía el sobre a `POST /iot/telemetria/vision` (BRK-03); la anomalía la calcula
el servidor contra la línea base del área (RF-24), no el nodo.

Variables (las de MQTT son las mismas del edge_agent):
  EDGE_MQTT_HOST, EDGE_MQTT_PORT, EDGE_MQTT_USERNAME, EDGE_MQTT_PASSWORD, EDGE_MQTT_CA_CERT, EDGE_TOPIC_PREFIX
  EDGE_VISION_SERIAL        serial de la cámara (dispositivo CAMARA_VISION de M09)
  EDGE_VISION_AREA_ID       área productiva de la cámara (RF-22)
  EDGE_VISION_MODELO        ruta del .onnx (p. ej. porcinos_yolov8n.onnx)
  EDGE_VISION_FUENTE        índice de cámara, URL o archivo de video
  EDGE_VISION_FPS=2  EDGE_VISION_VENTANA_S=60  EDGE_VISION_CONFIANZA=0.35  EDGE_VISION_UMBRAL_ACTIVO=0.02
  EDGE_VISION_ESTADO_CALIBRACION=CALIBRADO
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone

import cv2
import paho.mqtt.client as mqtt

from edge_vision.cascada import VERSION_METRICAS, VERSION_SEGUIMIENTO, Seguidor, Ventana
from edge_vision.detector import Detector

logger = logging.getLogger("edge_vision")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def sobre_d4(vector: dict, *, serial: str, area_id: int, inicio: float, fin: float,
             estado_calibracion: str, version_detector: str) -> dict:
    """Sobre D4 normativo (`ObservacionVisionD4DTO` del backend): solo los campos que acepta."""
    return {
        "serial": serial, "area_id": area_id, "tipo_dato": "VECTOR_COMPORTAMIENTO", "origen": "TIEMPO_REAL",
        "ventana_inicio": _iso(inicio), "ventana_fin": _iso(fin),
        "n_individuos_observados": vector["n_individuos_observados"],
        "componentes": vector["componentes"],
        "calidad": {**vector["calidad"], "estado_calibracion": estado_calibracion},
        "version_cascada": {"detector": version_detector, "seguimiento": VERSION_SEGUIMIENTO,
                            "metricas": VERSION_METRICAS, "anomalias": "servidor-rf24"},
    }


def _conectar(env: dict) -> mqtt.Client:
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vision-{env['EDGE_VISION_SERIAL']}")
    cliente.username_pw_set(env["EDGE_MQTT_USERNAME"], env["EDGE_MQTT_PASSWORD"])
    if env.get("EDGE_MQTT_CA_CERT"):
        cliente.tls_set(ca_certs=env["EDGE_MQTT_CA_CERT"])
    puerto = int(env.get("EDGE_MQTT_PORT") or (8883 if env.get("EDGE_MQTT_CA_CERT") else 1883))
    cliente.connect_async(env["EDGE_MQTT_HOST"], puerto)
    cliente.loop_start()  # paho reconecta solo
    return cliente


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = os.environ
    serial, area_id = env["EDGE_VISION_SERIAL"], int(env["EDGE_VISION_AREA_ID"])
    fuente = env["EDGE_VISION_FUENTE"]
    fps, duracion = float(env.get("EDGE_VISION_FPS", "2")), float(env.get("EDGE_VISION_VENTANA_S", "60"))
    detector = Detector(env["EDGE_VISION_MODELO"], float(env.get("EDGE_VISION_CONFIANZA", "0.35")))
    topic = f"{env.get('EDGE_TOPIC_PREFIX', 'sgpmp')}/{serial}/vision"
    cliente = _conectar(env)

    captura = cv2.VideoCapture(int(fuente) if fuente.isdigit() else fuente)
    es_archivo = not fuente.isdigit() and "://" not in fuente
    # Un archivo se lee a su velocidad real: entre dos cuadros procesados se saltan los intermedios.
    saltar = max(round((captura.get(cv2.CAP_PROP_FPS) or 25) / fps) - 1, 0) if es_archivo else 0
    seguidor, ventana, inicio = Seguidor(), None, time.time()
    logger.info("Visión %s → %s con %s, fuente %s", serial, topic, detector.version, fuente)

    # ponytail: sin buffer local; si el broker no está, la ventana se pierde (paho reintenta la conexión).
    while True:
        t = time.time()
        for _ in range(saltar):
            captura.grab()
        ok, cuadro = captura.read()
        if not ok:
            if es_archivo:
                captura.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            raise SystemExit(f"La fuente {fuente} no entrega cuadros.")
        if ventana is None:
            ventana = Ventana(math.hypot(*cuadro.shape[:2]), float(env.get("EDGE_VISION_UMBRAL_ACTIVO", "0.02")))
        cajas = detector.detectar(cuadro)
        seguidor.actualizar(t, cajas)
        ventana.agregar_cuadro(cajas)
        if t - inicio >= duracion:
            cuerpo = sobre_d4(ventana.cerrar(seguidor, inicio, t), serial=serial, area_id=area_id, inicio=inicio,
                              fin=t, estado_calibracion=env.get("EDGE_VISION_ESTADO_CALIBRACION", "CALIBRADO"),
                              version_detector=detector.version)
            cliente.publish(topic, json.dumps(cuerpo), qos=1)
            logger.info("Ventana publicada: %s", json.dumps({k: cuerpo[k] for k in ("n_individuos_observados", "componentes")}))
            ventana, inicio = Ventana(ventana.diagonal, ventana.umbral_activo), t
        time.sleep(max(0.0, 1 / fps - (time.time() - t)))


if __name__ == "__main__":
    main()
