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
PH = "PH"
OXIGENO_DISUELTO = "OXIGENO_DISUELTO"
AMONIACO = "AMONIACO"
DIOXIDO_CARBONO = "DIOXIDO_CARBONO"

# Nombre canónico → `variable` de la telemetría (nombres de modulo9). Se
# amplía o corrige con EDGE_RF55_VARIABLES sin tocar código.
VARIABLES_DEFECTO = {
    TEMPERATURA_AMBIENTAL: "temperatura_ambiente",
    HUMEDAD_RELATIVA: "humedad_relativa",
    TEMPERATURA_CORPORAL: "temperatura_corporal",
    FRECUENCIA_RESPIRATORIA: "frecuencia_respiratoria",
    PH: "ph",
    OXIGENO_DISUELTO: "oxigeno_disuelto",
    AMONIACO: "nh3",
    DIOXIDO_CARBONO: "co2",
}

# Rango físicamente posible por nombre canónico (TC-M03-081): una lectura fuera
# de él es un dato malformado, no una desviación, y no se clasifica.
RANGOS_FISICOS: dict[str, tuple[float, float]] = {
    HUMEDAD_RELATIVA: (0.0, 100.0),
    PH: (0.0, 14.0),
    OXIGENO_DISUELTO: (0.0, math.inf),
    AMONIACO: (0.0, math.inf),
    DIOXIDO_CARBONO: (0.0, math.inf),
}

# (lecturas por nombre canónico, parámetros, severidad simple de una lectura)
#   → (severidad o None, valores derivados, canónicas opcionales que escalaron)
Evaluar = Callable[
    [Mapping[str, "Reading"], Mapping[str, Any], Callable[["Reading"], str | None]],
    tuple[str | None, dict[str, float], tuple[str, ...]],
]


@dataclass(frozen=True)
class Formula:
    codigo: str  # va en `regla_aplicada`
    alias: frozenset[str]  # otros `nombre` normalizados que la eligen
    variables: tuple[str, ...]  # canónicas, todas requeridas dentro de la ventana
    opcionales: tuple[str, ...]  # canónicas que solo modifican el resultado
    parametros: Callable[[Any], dict[str, Any]]  # ValueError si no sirven
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

_PARAMETROS_THI = {LEVE: 72.0, MODERADO: 78.0, CRITICO: 84.0, "temperatura_corporal_max": 40.0}


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
    """THI del NRC (1971): T - (0.55 - 0.0055 * HR) * (T - 58), con T en °F.

    Es la forma en °F de (1.8·Tc + 32) - (0.55 - 0.0055·HR)·(1.8·Tc - 26), la
    escala de los cortes 72/78/84: 25 °C y 60 % dan 72.82. Con HR=100 % el THI
    es exactamente la temperatura en °F.
    """
    return temperatura_f - (0.55 - 0.0055 * humedad_relativa) * (temperatura_f - 58)


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
    # residuo de punto flotante dejaría un THI de 78 en la banda de abajo.
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


# --- CALIDAD_HIDRICA -------------------------------------------------------

# Regla AND de TC-M03-106: pH fuera de [ph_min, ph_max] y OD < oxigeno_min. Los
# límites del pH son permitidos (6.5 y 8.5 no disparan) y 5 mg/L tampoco.
_PARAMETROS_HIDRICA: dict[str, Any] = {
    "ph_min": 6.5,
    "ph_max": 8.5,
    "oxigeno_min": 5.0,
    "severidad": CRITICO,
}


def _parametros_and(defectos: Mapping[str, Any], umbrales_severidad: Any) -> dict[str, Any]:
    """Límites numéricos y `severidad` de una regla AND, sobre sus defaults."""
    params = dict(defectos)
    if umbrales_severidad is None:
        return params
    if not isinstance(umbrales_severidad, dict):
        raise ValueError("umbrales_severidad debe ser un objeto")
    for clave in params.keys() - {"severidad"}:
        if clave in umbrales_severidad:
            params[clave] = numero(umbrales_severidad[clave], f"umbrales_severidad.{clave}")
    if "severidad" in umbrales_severidad:
        if umbrales_severidad["severidad"] not in SEVERIDADES:
            raise ValueError(f"umbrales_severidad.severidad debe ser una de {SEVERIDADES}")
        params["severidad"] = umbrales_severidad["severidad"]
    return params


def _parametros_hidrica(umbrales_severidad: Any) -> dict[str, Any]:
    params = _parametros_and(_PARAMETROS_HIDRICA, umbrales_severidad)
    if not params["ph_min"] < params["ph_max"]:
        raise ValueError("umbrales_severidad: ph_min debe ser menor que ph_max")
    return params


def _evaluar_hidrica(
    lecturas: Mapping[str, Reading],
    params: Mapping[str, Any],
    severidad_simple: Callable[[Reading], str | None],
) -> tuple[str | None, dict[str, float], tuple[str, ...]]:
    ph = lecturas[PH].valor_crudo
    oxigeno = lecturas[OXIGENO_DISUELTO].valor_crudo
    ph_fuera = ph < params["ph_min"] or ph > params["ph_max"]
    if ph_fuera and oxigeno < params["oxigeno_min"]:
        return params["severidad"], {}, ()
    return None, {}, ()


CALIDAD_HIDRICA = Formula(
    codigo="CALIDAD_HIDRICA",
    alias=frozenset({"CALIDAD_DEL_AGUA", "CALIDAD_AGUA", "CALIDAD_HIDRICA_PH_OD"}),
    variables=(PH, OXIGENO_DISUELTO),
    opcionales=(),
    parametros=_parametros_hidrica,
    evaluar=_evaluar_hidrica,
)


# --- CALIDAD_AIRE (NH3 / CO2) ----------------------------------------------

# Regla AND de TC-M03-085/105: NH3 > nh3_max y CO2 > co2_max (ppm). "Superar"
# es estricto: 25 ppm de NH3 o 2000 ppm de CO2 no cumplen su condición.
_PARAMETROS_AIRE: dict[str, Any] = {
    "nh3_max": 25.0,
    "co2_max": 2000.0,
    "severidad": MODERADO,
}


def _parametros_aire(umbrales_severidad: Any) -> dict[str, Any]:
    return _parametros_and(_PARAMETROS_AIRE, umbrales_severidad)


def _evaluar_aire(
    lecturas: Mapping[str, Reading],
    params: Mapping[str, Any],
    severidad_simple: Callable[[Reading], str | None],
) -> tuple[str | None, dict[str, float], tuple[str, ...]]:
    nh3 = lecturas[AMONIACO].valor_crudo
    co2 = lecturas[DIOXIDO_CARBONO].valor_crudo
    if nh3 > params["nh3_max"] and co2 > params["co2_max"]:
        return params["severidad"], {}, ()
    return None, {}, ()


CALIDAD_AIRE = Formula(
    codigo="CALIDAD_AIRE",
    alias=frozenset(
        {"CALIDAD_DEL_AIRE", "CORRELACION_NH3_CO2", "NH3_CO2", "AMONIACO_CO2", "GASES_NH3_CO2"}
    ),
    variables=(AMONIACO, DIOXIDO_CARBONO),
    opcionales=(),
    parametros=_parametros_aire,
    evaluar=_evaluar_aire,
)

FORMULAS: dict[str, Formula] = {
    f.codigo: f for f in (ESTRES_TERMICO, CALIDAD_HIDRICA, CALIDAD_AIRE)
}
