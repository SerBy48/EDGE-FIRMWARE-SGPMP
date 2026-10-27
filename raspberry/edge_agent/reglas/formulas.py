"""Catálogo cerrado de fórmulas multivariable de RF-55.2 (reglas Tipo B).

El esquema de `modulo3.reglas_alertas` no transporta fórmulas (§6 del
documento de contratos): la fórmula vive compilada acá y se elige por el
`nombre` de la regla, normalizado. Evaluar expresiones arbitrarias llegadas
por la red en un dispositivo de campo queda descartado a propósito; cuando el
backend modele reglas multivariable (BE-5), se amplía el catálogo.

`umbrales_severidad` (JSONB libre) parametriza los cortes; lo que no traiga
usa los valores por defecto de la fórmula.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from edge_agent.reglas.evento import CRITICO, LEVE, MODERADO

if TYPE_CHECKING:
    from edge_agent.sources import Reading

SEVERIDADES = (LEVE, MODERADO, CRITICO)

TEMPERATURA_AMBIENTAL = "TEMPERATURA_AMBIENTAL"
HUMEDAD_RELATIVA = "HUMEDAD_RELATIVA"
TEMPERATURA_CORPORAL = "TEMPERATURA_CORPORAL"
FRECUENCIA_RESPIRATORIA = "FRECUENCIA_RESPIRATORIA"

# Nombre canónico → `variable` de la telemetría (nombres de modulo9). Se
# amplía o corrige con EDGE_RF55_VARIABLES sin tocar código.
VARIABLES_DEFECTO = {
    TEMPERATURA_AMBIENTAL: "temperatura_ambiente",
    HUMEDAD_RELATIVA: "humedad_relativa",
    TEMPERATURA_CORPORAL: "temperatura_corporal",
    FRECUENCIA_RESPIRATORIA: "frecuencia_respiratoria",
}

# (lecturas por nombre canónico, parámetros, severidad simple de una lectura)
#   → (severidad o None, valores derivados, canónicas opcionales que escalaron)
Evaluar = Callable[
    [Mapping[str, "Reading"], Mapping[str, float], Callable[["Reading"], str | None]],
    tuple[str | None, dict[str, float], tuple[str, ...]],
]


@dataclass(frozen=True)
class Formula:
    codigo: str  # va en `regla_aplicada`
    alias: frozenset[str]  # otros `nombre` normalizados que la eligen
    variables: tuple[str, ...]  # canónicas, todas requeridas dentro de la ventana
    opcionales: tuple[str, ...]  # canónicas que solo modifican el resultado
    parametros: Callable[[Any], dict[str, float]]  # ValueError si no sirven
    evaluar: Evaluar


def normalizar(nombre: str) -> str:
    """`"Estrés térmico por THI"` → `"ESTRES_TERMICO_POR_THI"`."""
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "_", sin_tildes.upper()).strip("_")


def buscar_formula(nombre: Any) -> Formula | None:
    if not isinstance(nombre, str):
        return None
    clave = normalizar(nombre)
    for formula in FORMULAS.values():
        if clave == formula.codigo or clave in formula.alias:
            return formula
    return None


def numero(valor: Any, campo: str) -> float:
    """Número finito; acepta el texto con que el backend serializa sus Decimal."""
    if isinstance(valor, bool):
        raise ValueError(f"{campo} debe ser numérico")
    if isinstance(valor, str):
        try:
            valor = float(valor.strip())
        except ValueError:
            raise ValueError(f"{campo} debe ser numérico") from None
    if not isinstance(valor, int | float) or not math.isfinite(valor):
        raise ValueError(f"{campo} debe ser numérico")
    return float(valor)


def subir(severidad: str) -> str:
    """Un nivel más grave; CRITICO es tope."""
    return SEVERIDADES[min(SEVERIDADES.index(severidad) + 1, len(SEVERIDADES) - 1)]


# --- ESTRES_TERMICO --------------------------------------------------------

_PARAMETROS_THI = {LEVE: 72.0, MODERADO: 79.0, CRITICO: 84.0, "temperatura_corporal_max": 40.0}


def _parametros_thi(umbrales_severidad: Any) -> dict[str, float]:
    params = dict(_PARAMETROS_THI)
    if umbrales_severidad is None:
        return params
    if not isinstance(umbrales_severidad, dict):
        raise ValueError("umbrales_severidad debe ser un objeto")
    for clave in params:
        if clave in umbrales_severidad:
            params[clave] = numero(umbrales_severidad[clave], f"umbrales_severidad.{clave}")
    if not params[LEVE] < params[MODERADO] < params[CRITICO]:
        raise ValueError("umbrales_severidad: los cortes deben cumplir LEVE < MODERADO < CRITICO")
    return params


def thi(temperatura_f: float, humedad_relativa: float) -> float:
    """THI = T - (0.55 - 0.0055 * HR) * (T - 14.5), con T en °F.

    Los cortes 72/79/84 son de la escala en °F: T=32.5 °C (90.5 °F) y HR=78 %
    dan 81.3. Con T en °C la misma expresión daría ~30 y nunca dispararía.
    """
    return temperatura_f - (0.55 - 0.0055 * humedad_relativa) * (temperatura_f - 14.5)


def _fahrenheit(reading: Reading) -> float:
    unidad = reading.unidad.strip().upper().lstrip("°º")
    if unidad in ("F", "FAHRENHEIT"):
        return reading.valor_crudo
    return reading.valor_crudo * 1.8 + 32  # la plataforma registra °C


def _evaluar_thi(
    lecturas: Mapping[str, Reading],
    params: Mapping[str, float],
    severidad_simple: Callable[[Reading], str | None],
) -> tuple[str | None, dict[str, float], tuple[str, ...]]:
    # Se clasifica con el mismo valor que se reporta: sin el redondeo, el
    # residuo de punto flotante dejaría un THI de 79 en la banda de abajo.
    indice = round(
        thi(_fahrenheit(lecturas[TEMPERATURA_AMBIENTAL]), lecturas[HUMEDAD_RELATIVA].valor_crudo),
        2,
    )
    derivados = {"THI": indice}
    severidad = next((s for s in reversed(SEVERIDADES) if indice >= params[s]), None)
    if severidad is None:
        return None, derivados, ()

    escalaron: tuple[str, ...] = ()
    corporal = lecturas.get(TEMPERATURA_CORPORAL)
    if corporal is not None and corporal.valor_crudo > params["temperatura_corporal_max"]:
        escalaron += (TEMPERATURA_CORPORAL,)
    respiratoria = lecturas.get(FRECUENCIA_RESPIRATORIA)
    if respiratoria is not None and severidad_simple(respiratoria) in (MODERADO, CRITICO):
        escalaron += (FRECUENCIA_RESPIRATORIA,)
    # La escalada sube exactamente un nivel, aunque la disparen ambas variables.
    return (subir(severidad) if escalaron else severidad), derivados, escalaron


ESTRES_TERMICO = Formula(
    codigo="ESTRES_TERMICO",
    alias=frozenset({"ESTRES_TERMICO_POR_THI", "THI", "INDICE_TEMPERATURA_HUMEDAD"}),
    variables=(TEMPERATURA_AMBIENTAL, HUMEDAD_RELATIVA),
    opcionales=(TEMPERATURA_CORPORAL, FRECUENCIA_RESPIRATORIA),
    parametros=_parametros_thi,
    evaluar=_evaluar_thi,
)

FORMULAS: dict[str, Formula] = {ESTRES_TERMICO.codigo: ESTRES_TERMICO}
