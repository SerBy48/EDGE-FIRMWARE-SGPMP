# | Fecha      | Nombre   | Que se hizo                                                     |
# |------------|----------|-----------------------------------------------------------------|
# | 2026-11-02 | Arekkazu | V1 RF-53: seguimiento y métricas de la cascada de visión (D4)    |
"""Seguimiento de individuos y métricas de una ventana (cascada V1, sobre D4).

Puro Python: no depende del detector ni de OpenCV, así que se prueba con cajas
sintéticas. Todas las distancias van en fracciones de la diagonal del cuadro
(D) para que el vector no cambie con la resolución de la cámara.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

Caja = tuple[float, float, float, float]  # x, y, ancho, alto en píxeles

VERSION_SEGUIMIENTO = "iou-cercania-voraz-2"
VERSION_METRICAS = "edge-vision-1"


def parecido(track: Caja, det: Caja) -> float:
    """IoU, o cercanía de centros (hasta 0,5) cuando el animal se movió más que su caja entre cuadros."""
    distancia = math.dist((track[0] + track[2] / 2, track[1] + track[3] / 2), (det[0] + det[2] / 2, det[1] + det[3] / 2))
    return max(iou(track, det), 0.5 * max(0.0, 1 - distancia / math.hypot(track[2], track[3])))


def iou(a: Caja, b: Caja) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    ancho = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    alto = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = ancho * alto
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    id: int
    caja: Caja
    puntos: list[tuple[float, float, float]] = field(default_factory=list)  # (t, cx, cy)
    sin_ver: int = 0
    confirmado: bool = False  # ya se confirmó en una ventana anterior (sus puntos viejos se descartan)


class Seguidor:
    """Asociación voraz: cada detección se une al track más parecido (IoU o cercanía);
    un track que no se ve en `max_sin_ver` cuadros se da por perdido. Un track cuenta
    solo con `confirmacion` detecciones: un parpadeo del detector no es un animal.

    ponytail: sin filtro de Kalman ni re-identificación; con animales que se
    cruzan mucho cambia de id. Pasar a ByteTrack si las métricas lo necesitan.
    """

    def __init__(self, iou_minimo: float = 0.25, max_sin_ver: int = 4, confirmacion: int = 3) -> None:
        self.iou_minimo = iou_minimo
        self.confirmacion = confirmacion
        self.max_sin_ver = max_sin_ver
        self.activos: list[Track] = []
        self.perdidos: list[Track] = []
        self._siguiente = 1

    def actualizar(self, t: float, cajas: list[Caja]) -> None:
        pares = sorted(
            ((parecido(tr.caja, c), i, j) for i, tr in enumerate(self.activos) for j, c in enumerate(cajas)),
            reverse=True,
        )
        tracks_usados: set[int] = set()
        cajas_usadas: set[int] = set()
        for valor, i, j in pares:
            if valor < self.iou_minimo:
                break
            if i in tracks_usados or j in cajas_usadas:
                continue
            tracks_usados.add(i)
            cajas_usadas.add(j)
            self._ver(self.activos[i], t, cajas[j])
        for i, tr in enumerate(self.activos):
            if i not in tracks_usados:
                tr.sin_ver += 1
        for j, c in enumerate(cajas):
            if j not in cajas_usadas:
                tr = Track(self._siguiente, c)
                self._siguiente += 1
                self._ver(tr, t, c)
                self.activos.append(tr)
        self.perdidos += [tr for tr in self.activos if tr.sin_ver > self.max_sin_ver and self.confirmado(tr)]
        self.activos = [tr for tr in self.activos if tr.sin_ver <= self.max_sin_ver]

    def confirmado(self, tr: Track) -> bool:
        return len(tr.puntos) >= self.confirmacion or tr.confirmado

    @staticmethod
    def _ver(tr: Track, t: float, c: Caja) -> None:
        tr.caja, tr.sin_ver = c, 0
        tr.puntos.append((t, c[0] + c[2] / 2, c[1] + c[3] / 2))


@dataclass
class Ventana:
    """Acumula los cuadros de una ventana y calcula el vector D4 al cerrarla."""

    diagonal: float
    umbral_activo: float = 0.02  # fracciones de D por segundo para contar un track como activo
    conteos: list[int] = field(default_factory=list)
    dispersiones: list[float] = field(default_factory=list)

    def agregar_cuadro(self, cajas: list[Caja]) -> None:
        self.conteos.append(len(cajas))
        if len(cajas) >= 2:
            cx = [c[0] + c[2] / 2 for c in cajas]
            cy = [c[1] + c[3] / 2 for c in cajas]
            self.dispersiones.append(
                math.sqrt(statistics.pvariance(cx) + statistics.pvariance(cy)) / self.diagonal
            )

    def cerrar(self, seguidor: Seguidor, inicio: float, fin: float) -> dict:
        """Componentes y calidad de la ventana [inicio, fin]; vacía los perdidos del seguidor."""
        tracks = [tr for tr in seguidor.activos + seguidor.perdidos
                  if seguidor.confirmado(tr) and any(inicio <= p[0] <= fin for p in tr.puntos)]
        velocidades = []
        for tr in tracks:
            pts = [p for p in tr.puntos if inicio <= p[0] <= fin]
            if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
                recorrido = sum(math.dist(a[1:], b[1:]) for a, b in zip(pts, pts[1:]))
                velocidades.append(recorrido / self.diagonal / (pts[-1][0] - pts[0][0]))
        perdidos = sum(1 for tr in seguidor.perdidos if tr in tracks)
        seguidor.perdidos.clear()
        for tr in seguidor.activos:  # solo se guarda lo necesario para la ventana siguiente
            tr.confirmado = seguidor.confirmado(tr)
            tr.puntos = tr.puntos[-1:]
        cuadros = len(self.conteos)
        duracion = max(fin - inicio, 1e-9)
        return {
            "componentes": {
                "densidad_actividad": round(sum(v > self.umbral_activo for v in velocidades) / len(velocidades), 4)
                if velocidades else 0.0,
                "dispersion_espacial": round(statistics.fmean(self.dispersiones), 4) if self.dispersiones else 0.0,
                "tasa_movimiento": round(statistics.fmean(velocidades), 4) if velocidades else 0.0,
            },
            "calidad": {
                "cobertura_ventana": round(sum(n > 0 for n in self.conteos) / cuadros, 4) if cuadros else 0.0,
                "n_tracks": len(tracks),
                "n_tracks_perdidos": perdidos,
                "fps_efectivo": round(min(cuadros / duracion, 999.99), 2),
            },
            "n_individuos_observados": int(statistics.median(self.conteos)) if cuadros else 0,
        }
