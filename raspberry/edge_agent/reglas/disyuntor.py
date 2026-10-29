"""Disyuntor de RF-55.2 ante fallas de evaluación (TC-M03-088 y TC-M03-097).

- Una captura en la que RF-55.2 falla se clasifica entera con RF-55.1
  (`evaluar_lote` descarta lo de RF-55.2): es la alternativa del fallo transitorio.
- `fallos_max` capturas seguidas con falla lo deshabilitan; una captura sin
  falla reinicia la cuenta.
- Deshabilitado, pasados `reintento_s` la siguiente captura vuelve a evaluarlo
  (prueba): si evalúa bien queda ACTIVO; si falla, vuelve a DESHABILITADO por
  otros `reintento_s`. RF-55.1 sigue en todo momento.

Es independiente del perfil de hardware (`perfil.py`): lo que se apagó al
arrancar por hardware o por EDGE_RF55_2_DESHABILITAR no se reactiva solo.
"""

from __future__ import annotations

ACTIVO = "ACTIVO"
DESHABILITADO = "DESHABILITADO"

# Transiciones que devuelve `registrar` (para el log y el heartbeat).
TRANSITORIO = "TRANSITORIO"  # falló, pero no alcanza el umbral
DESHABILITA = "DESHABILITA"
REACTIVA = "REACTIVA"
REINTENTO_FALLIDO = "REINTENTO_FALLIDO"


class Disyuntor:
    def __init__(self, fallos_max: int = 3, reintento_s: float = 600.0) -> None:
        self.fallos_max = fallos_max
        self.reintento_s = reintento_s
        self.estado = ACTIVO
        self.fallos_consecutivos = 0
        self.reintento_en: float | None = None
        self.ultimo_fallo: str | None = None

    def permite(self, ahora: float) -> bool:
        """Si esta captura evalúa RF-55.2 (activo, o deshabilitado y toca reintentar)."""
        if self.estado == ACTIVO:
            return True
        return self.reintento_en is not None and ahora >= self.reintento_en

    def registrar(self, fallo: Exception | None, ahora: float) -> str | None:
        """Resultado de una captura en la que se evaluó RF-55.2; la transición, si hubo."""
        if fallo is None:
            self.fallos_consecutivos = 0
            if self.estado == DESHABILITADO:
                self.estado, self.reintento_en = ACTIVO, None
                return REACTIVA
            return None

        self.ultimo_fallo = repr(fallo)
        self.fallos_consecutivos += 1
        if self.estado == DESHABILITADO or self.fallos_consecutivos >= self.fallos_max:
            primera_vez = self.estado == ACTIVO
            self.estado, self.reintento_en = DESHABILITADO, ahora + self.reintento_s
            return DESHABILITA if primera_vez else REINTENTO_FALLIDO
        return TRANSITORIO

    def resumen(self) -> dict[str, object]:
        return {
            "estado": self.estado,
            "fallos_consecutivos": self.fallos_consecutivos,
            "fallos_max": self.fallos_max,
            "reintento_en": self.reintento_en,
            "ultimo_fallo": self.ultimo_fallo,
        }
