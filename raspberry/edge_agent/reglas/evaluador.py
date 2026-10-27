"""Evaluación de lecturas contra reglas y umbrales (RF-55).

Funciones puras: sin I/O ni estado global. Lo que sí tiene estado (ventanas,
deduplicación de errores) vive en el agente.

RF-55.1, umbrales simples (RF-17): los niveles `normal`/`precaucion`/`critico`
son bandas contiguas que cubren [valor_min, valor_max]. Se sigue la misma regla
que el broker (`prioridad.py`, fix `bandas excluyentes de RF-17`) y que el
semáforo del backend:
- un valor en el límite entre dos bandas cae en la más grave;
- fuera de todas las bandas es crítico (el ROJO de la plataforma);
- con varios umbrales para la variable (una especie por umbral) rige el más
  restrictivo (RFC-012).
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from edge_agent.config_store import Umbral
from edge_agent.reglas import evento as ev
from edge_agent.reglas.evento import EventoEdge
from edge_agent.sources import Reading

REGLA_UMBRAL = "UMBRAL_AMBIENTAL"
FUERA_DE_RANGO = "fuera_de_rango"

_NIVELES = ("normal", "precaucion", "critico")
# Gravedad de cada nivel de RF-17 y la severidad del evento que le corresponde.
# `normal` no emite: un evento por muestra saturaría el rate limit del backend.
_GRAVEDAD = {"normal": 0, "precaucion": 1, "critico": 2, FUERA_DE_RANGO: 2}
_SEVERIDAD = {"precaucion": ev.MODERADO, "critico": ev.CRITICO, FUERA_DE_RANGO: ev.CRITICO}


def evaluar_simple(
    reading: Reading,
    umbrales: Sequence[Umbral],
    *,
    ahora: datetime,
    version_firmware: str,
) -> EventoEdge | None:
    """Evento de la lectura, o None si está en rango o no hay umbral para su variable.

    Sin umbral para la variable no hay nada que evaluar y no es error (al
    arrancar sin `umbrales.json`, toda lectura estaría en ese caso). Sí lo es
    un umbral que existe pero no se puede usar: `ERROR_CONFIGURACION`.
    """
    inicio = time.perf_counter()
    candidatos = [u for u in umbrales if u.variable == reading.variable]
    if not candidatos or not _es_numero(reading.valor_crudo):
        return None

    peor: tuple[str, Umbral] | None = None
    mal_formado: Umbral | None = None
    for umbral in candidatos:
        nivel = clasificar(reading.valor_crudo, umbral.niveles)
        if nivel is None:
            mal_formado = mal_formado or umbral
        elif peor is None or _GRAVEDAD[nivel] > _GRAVEDAD[peor[0]]:
            peor = (nivel, umbral)

    if peor is not None and peor[0] != "normal":
        nivel, umbral = peor
        clasificacion, severidad = ev.DESVIACION_SIMPLE, _SEVERIDAD[nivel]
    elif mal_formado is not None:
        nivel, umbral = None, mal_formado
        clasificacion, severidad = ev.ERROR_CONFIGURACION, None
    else:
        return None

    metadata: dict[str, Any] = {
        "regla_aplicada": REGLA_UMBRAL,
        "id_umbral_ambiental": umbral.id_umbral_ambiental,
        "version_umbral": umbral.version,
    }
    if nivel is not None:
        metadata["nivel_umbral"] = nivel
    return ev.nuevo_evento(
        clasificacion,
        severidad,
        [reading],
        ahora=ahora,
        inicio=inicio,
        fin=time.perf_counter(),
        version_firmware=version_firmware,
        metadata=metadata,
    )


def clasificar(valor: float, niveles: Sequence[Any]) -> str | None:
    """Nivel de RF-17 de `valor`, `fuera_de_rango`, o None si los niveles no sirven."""
    bandas = []
    for nivel in niveles:
        if not isinstance(nivel, dict) or nivel.get("nivel") not in _NIVELES:
            return None
        inferior, superior = nivel.get("limite_inferior"), nivel.get("limite_superior")
        if not (_es_numero(inferior) and _es_numero(superior) and inferior <= superior):
            return None
        bandas.append((nivel["nivel"], inferior, superior))
    if not bandas:
        return None
    dentro = [nombre for nombre, inferior, superior in bandas if inferior <= valor <= superior]
    if not dentro:
        return FUERA_DE_RANGO
    return max(dentro, key=_GRAVEDAD.__getitem__)


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, int | float) and not isinstance(valor, bool) and math.isfinite(valor)
