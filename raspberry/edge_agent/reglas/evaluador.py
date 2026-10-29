"""Evaluación de lecturas contra reglas y umbrales (RF-55).

Funciones sin I/O ni estado global: el único estado (ventana de correlación y
conteos de confirmación de RF-55.2) entra explícito como `EstadoCompuestas`.

RF-55.1, umbrales simples (RF-17): los niveles `normal`/`precaucion`/`critico`
son bandas contiguas que cubren [valor_min, valor_max]. Se sigue la misma regla
que el broker (`prioridad.py`, fix `bandas excluyentes de RF-17`) y que el
semáforo del backend:
- un valor en el límite entre dos bandas cae en la más grave;
- fuera de todas las bandas es crítico (el ROJO de la plataforma);
- con varios umbrales para la variable (una especie por umbral) rige el más
  restrictivo (RFC-012);
- un umbral que no cumple esa forma, o una variable sin umbral, no se
  clasifica: es ERROR_CONFIGURACION con `severidad_invalida` (TC-M03-083/095/122).

RF-55.2, reglas de `modulo3.reglas_alertas` (§6 del documento de contratos):
- Tipo B: el `nombre` elige una fórmula del catálogo cerrado (`formulas.py`)
  que correlaciona varias variables dentro de una ventana de ≤ 30 s.
- Tipo A, cualquier otro nombre: `tipo_sensor` fuera de [umbral_min,
  umbral_max] que se repite `lectura_confirmacion_max` veces dentro de
  `ventana_confirmacion_min` minutos (es lo que el esquema sí expresa).
Una compuesta que dispara con `prevalece_sobre_regla_simple` suprime el
DESVIACION_SIMPLE de sus mismas lecturas (RF-57 E14/E15), pero conserva la
severidad más grave de las dos evaluaciones (TC-M03-089).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import pairwise
from typing import Any

from edge_agent.config_store import Umbral
from edge_agent.reglas import evento as ev
from edge_agent.reglas.evento import EventoEdge
from edge_agent.reglas.formulas import (
    RANGOS_FISICOS,
    SEVERIDADES,
    VARIABLES_DEFECTO,
    Formula,
    buscar_formula,
    numero,
)
from edge_agent.reglas.store import Regla
from edge_agent.reglas.ventana import VENTANA_MAX_S, EstadoCompuestas
from edge_agent.sources import Reading

REGLA_UMBRAL = "UMBRAL_AMBIENTAL"
REGLA_VENTANA = "VENTANA_CONFIRMACION"
FUERA_DE_RANGO = "fuera_de_rango"
SIN_UMBRAL = "sin umbral RF-17 para la variable en este Edge"
# `severidad_origen` de una compuesta que prevalece: qué evaluación fijó la severidad.
ORIGEN_RF55_1 = "RF-55.1"
ORIGEN_RF55_2 = "RF-55.2"

# Límites duros de RF-55.2: se rechazan al recibir la regla, no al evaluarla.
MAX_REGLAS_ACTIVAS = 5
# Cardinalidad de una correlación Tipo B (TC-M03-086/107): cuenta las variables
# requeridas de la fórmula; las opcionales solo modifican el resultado y no
# cuentan (THI: 2 requeridas + 2 opcionales). Tipo A no correlaciona: confirma
# una variable en el tiempo y no se le aplica.
MIN_VARIABLES_POR_REGLA = 2
MAX_VARIABLES_POR_REGLA = 3
# Acota la memoria de las confirmaciones Tipo A (no es un límite de RF-55.2).
MAX_LECTURAS_CONFIRMACION = 100

_NIVELES = ("normal", "precaucion", "critico")
# Gravedad de cada nivel de RF-17 y la severidad del evento que le corresponde.
# `normal` no emite: un evento por muestra saturaría el rate limit del backend.
_GRAVEDAD = {"normal": 0, "precaucion": 1, "critico": 2, FUERA_DE_RANGO: 2}
_SEVERIDAD = {"precaucion": ev.MODERADO, "critico": ev.CRITICO, FUERA_DE_RANGO: ev.CRITICO}


class ReglaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class _TipoA:
    # Canónicas a buscar en el mapa de variables: TIPO_CATEGORIA y luego TIPO.
    claves: tuple[str, ...]
    minimo: float | None
    maximo: float | None
    lecturas: int
    ventana_min: float | None
    severidad: str


@dataclass(frozen=True)
class _TipoB:
    formula: Formula
    parametros: dict[str, float]
    ventana_s: float


# --- RF-55.1 -----------------------------------------------------------------


def evaluar_simple(
    reading: Reading,
    umbrales: Sequence[Umbral],
    *,
    ahora: datetime,
    version_firmware: str,
) -> EventoEdge | None:
    """Evento de la lectura, o None si está en la banda `normal` de su umbral.

    Sin umbral usable no se inventa una severidad (TC-M03-083/095/122): la
    variable sin ningún umbral o con uno que no cumple la forma de RF-17
    (`motivo_niveles`) es `ERROR_CONFIGURACION` con `severidad_invalida`. La
    lectura cruda igual sale como telemetría; el agente publica el error una
    vez por variable o por umbral y versión.
    """
    if not _es_numero(reading.valor_crudo):
        return None
    inicio = time.perf_counter()
    peor, mal_formado = _peor_nivel(reading, umbrales)
    metadata: dict[str, Any] = {"regla_aplicada": REGLA_UMBRAL}
    if peor is not None and peor[0] != "normal":
        nivel, umbral = peor
        clasificacion, severidad = ev.DESVIACION_SIMPLE, _SEVERIDAD[nivel]
        metadata |= {
            "id_umbral_ambiental": umbral.id_umbral_ambiental,
            "version_umbral": umbral.version,
            "nivel_umbral": nivel,
        }
    elif mal_formado is not None or peor is None:
        clasificacion, severidad = ev.ERROR_CONFIGURACION, None
        if mal_formado is not None:
            umbral, motivo = mal_formado
            metadata |= {
                "id_umbral_ambiental": umbral.id_umbral_ambiental,
                "version_umbral": umbral.version,
            }
        else:
            metadata |= {"id_umbral_ambiental": None, "version_umbral": None}
            motivo = SIN_UMBRAL
        metadata |= {"variable": reading.variable, "motivo": motivo, "severidad_invalida": True}
    else:
        return None
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


def severidad_simple(reading: Reading, umbrales: Sequence[Umbral]) -> str | None:
    """Severidad RF-55.1 de la lectura, sin armar evento (None si está en rango)."""
    peor, _ = _peor_nivel(reading, umbrales)
    return _SEVERIDAD.get(peor[0]) if peor is not None else None


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


def motivo_niveles(valor_min: Any, valor_max: Any, niveles: Any) -> str | None:
    """Por qué un umbral RF-17 no sirve para clasificar, o None si sirve.

    Es la forma que exige el backend (rechaza otra con SOLAPAMIENTO_NIVELES):
    las tres bandas `normal`/`precaucion`/`critico`, una vez cada una, de ancho
    > 0, contiguas y cubriendo [valor_min, valor_max]. Con una banda faltante,
    en cero o con huecos, lo que cae fuera de la `normal` saldría CRITICO sin
    que nadie lo haya definido (TC-M03-095/122). Se aplica al recibir el umbral
    (ACK_UMBRAL) y otra vez al evaluar uno ya guardado.
    """
    if not (_es_numero(valor_min) and _es_numero(valor_max) and valor_min < valor_max):
        return "valor_min y valor_max deben ser números con valor_min < valor_max"
    if not isinstance(niveles, list | tuple) or not niveles:
        return "niveles debe traer normal/precaucion/critico con límites numéricos"
    bandas = []
    for nivel in niveles:
        if not isinstance(nivel, Mapping) or nivel.get("nivel") not in _NIVELES:
            return "niveles debe traer normal/precaucion/critico con límites numéricos"
        nombre = nivel["nivel"]
        inferior, superior = nivel.get("limite_inferior"), nivel.get("limite_superior")
        if not (_es_numero(inferior) and _es_numero(superior)):
            return "niveles debe traer normal/precaucion/critico con límites numéricos"
        if not inferior < superior:
            return f"la banda {nombre} debe tener ancho mayor que 0 ({inferior:g}–{superior:g})"
        bandas.append((inferior, superior, nombre))
    if sorted(b[2] for b in bandas) != sorted(_NIVELES):
        return "niveles debe traer normal, precaucion y critico, una vez cada uno"
    bandas.sort()
    if not (_igual(bandas[0][0], valor_min) and _igual(bandas[-1][1], valor_max)):
        return "las bandas deben cubrir exactamente [valor_min, valor_max]"
    for (_, fin, una), (comienzo, _, otra) in pairwise(bandas):
        if not _igual(fin, comienzo):
            return f"las bandas {una} y {otra} deben ser contiguas (sin huecos ni solapes)"
    return None


def _peor_nivel(
    reading: Reading, umbrales: Sequence[Umbral]
) -> tuple[tuple[str, Umbral] | None, tuple[Umbral, str] | None]:
    """(nivel más grave y su umbral, primer umbral inutilizable y su motivo)."""
    peor: tuple[str, Umbral] | None = None
    mal_formado: tuple[Umbral, str] | None = None
    if not _es_numero(reading.valor_crudo):
        return None, None
    for umbral in umbrales:
        if umbral.variable != reading.variable:
            continue
        motivo = motivo_niveles(umbral.valor_min, umbral.valor_max, umbral.niveles)
        nivel = clasificar(reading.valor_crudo, umbral.niveles) if motivo is None else None
        if nivel is None:
            mal_formado = mal_formado or (umbral, motivo or "niveles inutilizables")
        elif peor is None or _GRAVEDAD[nivel] > _GRAVEDAD[peor[0]]:
            peor = (nivel, umbral)
    return peor, mal_formado


def normales(
    lecturas: Sequence[Reading], umbrales: Sequence[Umbral], eventos: Sequence[EventoEdge]
) -> list[tuple[Reading, Umbral]]:
    """Lecturas en banda `normal` de su umbral que no entraron en ningún evento.

    Son las clasificaciones NORMAL: no se publican (un evento por muestra
    saturaría el rate limit del backend), solo van al historial local.
    """
    en_eventos = {id(r) for evento in eventos for r in evento.lecturas}
    resultado = []
    for reading in lecturas:
        if id(reading) in en_eventos:
            continue
        peor, _ = _peor_nivel(reading, umbrales)
        if peor is not None and peor[0] == "normal":
            resultado.append((reading, peor[1]))
    return resultado


# --- RF-55.2 -----------------------------------------------------------------


def lecturas_invalidas(
    lecturas: Sequence[Reading], variables: Mapping[str, str]
) -> list[tuple[Reading, str]]:
    """Lecturas que no se clasifican, con el motivo (TC-M03-081).

    Valor nulo, no numérico, booleano, NaN o infinito, o fuera del rango
    físico de su variable (`RANGOS_FISICOS`). No generan ningún evento, ni
    siquiera ERROR_CONFIGURACION: el problema es el dato, no la regla.
    """
    nombres = {**VARIABLES_DEFECTO, **variables}
    rangos = {nombres.get(c, c): rango for c, rango in RANGOS_FISICOS.items()}
    invalidas = []
    for reading in lecturas:
        valor = reading.valor_crudo
        if not _es_numero(valor):
            invalidas.append((reading, f"valor no numérico o no finito: {valor!r}"))
        elif reading.variable in rangos:
            minimo, maximo = rangos[reading.variable]
            if not minimo <= valor <= maximo:
                invalidas.append(
                    (reading, f"{valor!r} fuera del rango físico [{minimo:g}, {maximo:g}]")
                )
    return invalidas


def validar_regla(cuerpo: Any) -> str | None:
    """Motivo por el que la regla no se puede aceptar, o None si sirve."""
    try:
        _interpretar(cuerpo)
    except ReglaInvalida as exc:
        return str(exc)
    return None


def evaluar_lote(
    lecturas: Sequence[Reading],
    umbrales: Sequence[Umbral],
    reglas: Sequence[Regla],
    *,
    estado: EstadoCompuestas | None,
    variables: Mapping[str, str],
    ahora: datetime,
    version_firmware: str,
) -> list[EventoEdge]:
    """Eventos de una captura. `estado=None`: RF-55.2 deshabilitado, solo RF-55.1.

    Se evalúa la captura completa y no lectura por lectura: así una
    compuesta ve juntas las variables que llegaron juntas y puede prevalecer
    sobre los simples de esas mismas lecturas.
    """
    eventos: list[EventoEdge] = []
    suprimidas: set[int] = set()  # id() de las lecturas cubiertas por una compuesta
    # Un dato inválido no entra a la ventana ni a ninguna regla: así no puede
    # producir una clasificación válida falsa (TC-M03-081).
    invalidas = {id(r) for r, _ in lecturas_invalidas(lecturas, variables)}
    lecturas = [r for r in lecturas if id(r) not in invalidas]
    if estado is not None:
        try:
            eventos, suprimidas = _evaluar_rf55_2(
                lecturas, umbrales, reglas, estado, variables, ahora, version_firmware
            )
        except Exception as exc:
            # RF-55.2 falló en esta captura: se descarta todo lo suyo y la
            # captura entera se clasifica con RF-55.1 (TC-M03-097). El agente
            # cuenta las fallas seguidas y lo deshabilita (TC-M03-088).
            estado.fallo = exc
            eventos, suprimidas = [], set()
        else:
            estado.fallo = None
    for reading in lecturas:
        if id(reading) in suprimidas:
            continue
        evento = evaluar_simple(reading, umbrales, ahora=ahora, version_firmware=version_firmware)
        if evento is not None:
            eventos.append(evento)
    return eventos


def _evaluar_rf55_2(
    lecturas: Sequence[Reading],
    umbrales: Sequence[Umbral],
    reglas: Sequence[Regla],
    estado: EstadoCompuestas,
    variables: Mapping[str, str],
    ahora: datetime,
    version_firmware: str,
) -> tuple[list[EventoEdge], set[int]]:
    """Eventos de las reglas compuestas e id() de las lecturas que suprimen."""
    eventos: list[EventoEdge] = []
    suprimidas: set[int] = set()
    for reading in lecturas:
        estado.ventana.agregar(reading, ahora)
    estado.confirmaciones.retener((r.id_regla_alerta, r.version_regla) for r in reglas)
    nombres = {**VARIABLES_DEFECTO, **variables}
    for regla in reglas:
        for evento in _evaluar_regla(
            regla, lecturas, umbrales, estado, nombres, ahora, version_firmware
        ):
            if evento.clasificacion == ev.DESVIACION_COMPUESTA and (
                regla.regla.get("prevalece_sobre_regla_simple") is True
            ):
                evento = _con_maxima_severidad(evento, umbrales)
                suprimidas.update(id(r) for r in evento.lecturas)
            eventos.append(evento)
    return eventos, suprimidas


def _con_maxima_severidad(evento: EventoEdge, umbrales: Sequence[Umbral]) -> EventoEdge:
    """La compuesta que prevalece con la máxima severidad entre RF-55.2 y RF-55.1 (TC-M03-089).

    Suprimir el DESVIACION_SIMPLE no puede bajar la gravedad: si el umbral
    simple de alguna de sus lecturas es más grave que la regla, el evento
    toma esa severidad. Empate: decide RF-55.2. La metadata deja ambas para
    reconstruir cómo se obtuvo la clasificación.
    """
    simples = [s for r in evento.lecturas if (s := severidad_simple(r, umbrales)) is not None]
    simple = max(simples, key=SEVERIDADES.index, default=None)
    gana_simple = simple is not None and (
        SEVERIDADES.index(simple) > SEVERIDADES.index(evento.severidad)
    )
    return replace(
        evento,
        severidad=simple if gana_simple else evento.severidad,
        metadata={
            **evento.metadata,
            "severidad_regla": evento.severidad,
            "severidad_rf55_1": simple,
            "severidad_origen": ORIGEN_RF55_1 if gana_simple else ORIGEN_RF55_2,
        },
    )


def _evaluar_regla(
    regla: Regla,
    lecturas: Sequence[Reading],
    umbrales: Sequence[Umbral],
    estado: EstadoCompuestas,
    nombres: Mapping[str, str],
    ahora: datetime,
    version_firmware: str,
) -> list[EventoEdge]:
    inicio = time.perf_counter()
    metadata = {
        "id_regla_alerta": regla.id_regla_alerta,
        "version_regla": regla.version_regla,
        "nombre_regla": regla.regla.get("nombre"),
    }
    try:
        plan = _interpretar(regla.regla)
    except ReglaInvalida as exc:
        # `regla` es un dict libre que el broker no interpreta: una que no se
        # puede usar es ERROR_CONFIGURACION. Una excepción al *evaluar* una
        # regla válida no lo es: es una falla de RF-55.2 y la maneja
        # `evaluar_lote` (TC-M03-088/097).
        if not lecturas:
            return []
        motivo = str(exc)
        return [
            ev.nuevo_evento(
                ev.ERROR_CONFIGURACION,
                None,
                lecturas[:1],
                ahora=ahora,
                inicio=inicio,
                fin=time.perf_counter(),
                version_firmware=version_firmware,
                metadata={"regla_aplicada": None, **metadata, "motivo": motivo},
            )
        ]
    if isinstance(plan, _TipoB):
        return _evaluar_tipo_b(
            plan, lecturas, umbrales, estado, nombres, ahora, version_firmware, metadata
        )
    return _evaluar_tipo_a(
        plan, regla, lecturas, estado, nombres, ahora, version_firmware, metadata
    )


def _evaluar_tipo_b(
    plan: _TipoB,
    lecturas: Sequence[Reading],
    umbrales: Sequence[Umbral],
    estado: EstadoCompuestas,
    nombres: Mapping[str, str],
    ahora: datetime,
    version_firmware: str,
    metadata: dict[str, Any],
) -> list[EventoEdge]:
    formula = plan.formula
    requeridas = {nombres.get(c, c): c for c in formula.variables}
    opcionales = {nombres.get(c, c): c for c in formula.opcionales}
    eventos = []
    evaluados: set[str] = set()  # una evaluación por nodo y captura
    # id() de las lecturas que ya entraron en un evento de esta regla: si dos
    # nodos aportan las variables en la misma captura, sale un solo evento.
    usadas_en_eventos: set[int] = set()
    for disparo in lecturas:
        if (
            disparo.variable not in requeridas
            or disparo.serial in evaluados
            or id(disparo) in usadas_en_eventos
        ):
            continue
        inicio = time.perf_counter()
        usadas = _correlacionar(disparo, requeridas, plan.ventana_s, estado)
        if usadas is None:
            continue  # falta alguna variable dentro de la ventana: no dispara, no es error
        evaluados.add(disparo.serial)
        extras = _correlacionar(disparo, opcionales, plan.ventana_s, estado, todas=False) or {}
        severidad, derivados, escalaron = formula.evaluar(
            {**extras, **usadas}, plan.parametros, lambda r: severidad_simple(r, umbrales)
        )
        if severidad is None:
            continue
        usadas_en_eventos.update(id(r) for r in usadas.values())
        # La que disparó va primera: el broker toma de ella el `sensor_id` raíz.
        involucradas = [disparo]
        involucradas += [usadas[c] for c in formula.variables if usadas[c] is not disparo]
        involucradas += [extras[c] for c in escalaron]
        eventos.append(
            ev.nuevo_evento(
                ev.DESVIACION_COMPUESTA,
                severidad,
                involucradas,
                ahora=ahora,
                inicio=inicio,
                fin=time.perf_counter(),
                version_firmware=version_firmware,
                metadata={
                    "regla_aplicada": formula.codigo,
                    **metadata,
                    "valores_derivados": derivados,
                    "ventana_temporal_ms": _span_ms(involucradas),
                },
            )
        )
    return eventos


def _correlacionar(
    disparo: Reading,
    variables: Mapping[str, str],
    ventana_s: float,
    estado: EstadoCompuestas,
    *,
    todas: bool = True,
) -> dict[str, Reading] | None:
    """Lecturas (por nombre canónico) de `variables` a ≤ `ventana_s` del disparo.

    `todas=True`: None si falta alguna.
    """
    encontradas: dict[str, Reading] = {}
    for variable, canonica in variables.items():
        if variable == disparo.variable:
            encontradas[canonica] = disparo
            continue
        reading = estado.ventana.ultima(
            variable, disparo.timestamp_captura, ventana_s, serial=disparo.serial
        )
        if reading is not None and _es_numero(reading.valor_crudo):
            encontradas[canonica] = reading
        elif todas:
            return None
    return encontradas


def _evaluar_tipo_a(
    plan: _TipoA,
    regla: Regla,
    lecturas: Sequence[Reading],
    estado: EstadoCompuestas,
    nombres: Mapping[str, str],
    ahora: datetime,
    version_firmware: str,
    metadata: dict[str, Any],
) -> list[EventoEdge]:
    variable = next((nombres[c] for c in plan.claves if c in nombres), plan.claves[0])
    ventana_s = plan.ventana_min * 60 if plan.ventana_min is not None else math.inf
    eventos = []
    for reading in lecturas:
        if reading.variable != variable or not _es_numero(reading.valor_crudo):
            continue
        inicio = time.perf_counter()
        valor = reading.valor_crudo
        fuera = (plan.minimo is not None and valor < plan.minimo) or (
            plan.maximo is not None and valor > plan.maximo
        )
        clave = (regla.id_regla_alerta, regla.version_regla, reading.serial)
        if not fuera or not estado.confirmaciones.registrar(
            clave, reading.timestamp_captura, ventana_s, plan.lecturas
        ):
            continue
        eventos.append(
            ev.nuevo_evento(
                ev.DESVIACION_COMPUESTA,
                plan.severidad,
                [reading],
                ahora=ahora,
                inicio=inicio,
                fin=time.perf_counter(),
                version_firmware=version_firmware,
                metadata={
                    "regla_aplicada": REGLA_VENTANA,
                    **metadata,
                    "lectura_confirmacion_max": plan.lecturas,
                    "ventana_confirmacion_min": plan.ventana_min,
                },
            )
        )
    return eventos


def _interpretar(cuerpo: Any) -> _TipoA | _TipoB:
    if not isinstance(cuerpo, dict) or not cuerpo:
        raise ReglaInvalida("regla debe ser un objeto no vacío")
    nombre = cuerpo.get("nombre")
    if not isinstance(nombre, str) or not nombre.strip():
        # Va en `nombre_regla` de cada evento: sin él no hay trazabilidad (TC-M03-199).
        raise ReglaInvalida("nombre debe ser un texto no vacío")
    try:
        formula = buscar_formula(cuerpo.get("nombre"))
        if formula is not None:
            return _interpretar_tipo_b(cuerpo, formula)
        return _interpretar_tipo_a(cuerpo)
    except ReglaInvalida:
        raise
    except ValueError as exc:
        raise ReglaInvalida(str(exc)) from exc


def _interpretar_tipo_b(cuerpo: dict[str, Any], formula: Formula) -> _TipoB:
    cantidad = len(formula.variables)
    if not MIN_VARIABLES_POR_REGLA <= cantidad <= MAX_VARIABLES_POR_REGLA:
        raise ReglaInvalida(
            f"{formula.codigo} correlaciona {cantidad} variable(s) "
            f"(mínimo {MIN_VARIABLES_POR_REGLA}, máximo {MAX_VARIABLES_POR_REGLA})"
        )
    ventana = cuerpo.get("ventana_correlacion_s")
    ventana_s = VENTANA_MAX_S if ventana is None else numero(ventana, "ventana_correlacion_s")
    if not 0 < ventana_s <= VENTANA_MAX_S:
        raise ReglaInvalida(f"ventana_correlacion_s debe estar entre 0 y {VENTANA_MAX_S:g} s")
    return _TipoB(formula, formula.parametros(cuerpo.get("umbrales_severidad")), ventana_s)


def _interpretar_tipo_a(cuerpo: dict[str, Any]) -> _TipoA:
    tipo_sensor = cuerpo.get("tipo_sensor")
    categoria = cuerpo.get("categoria_variable")
    if not isinstance(tipo_sensor, str) or not tipo_sensor.strip():
        raise ReglaInvalida("tipo_sensor es obligatorio en una regla de ventana de confirmación")
    if categoria is not None and not isinstance(categoria, str):
        raise ReglaInvalida("categoria_variable debe ser texto")
    tipo = tipo_sensor.strip().upper()
    claves = (f"{tipo}_{categoria.strip().upper()}", tipo) if categoria else (tipo,)

    minimo = _opcional(cuerpo, "umbral_min")
    maximo = _opcional(cuerpo, "umbral_max")
    if minimo is None and maximo is None:
        raise ReglaInvalida("la regla necesita umbral_min o umbral_max")
    if minimo is not None and maximo is not None and minimo >= maximo:
        raise ReglaInvalida("umbral_min debe ser menor que umbral_max")

    lecturas = _opcional(cuerpo, "lectura_confirmacion_max")
    lecturas = 1 if lecturas is None else lecturas
    if lecturas != int(lecturas) or not 1 <= lecturas <= MAX_LECTURAS_CONFIRMACION:
        raise ReglaInvalida(
            f"lectura_confirmacion_max debe ser un entero entre 1 y {MAX_LECTURAS_CONFIRMACION}"
        )
    ventana_min = _opcional(cuerpo, "ventana_confirmacion_min")
    if ventana_min is not None and ventana_min < 0:
        raise ReglaInvalida("ventana_confirmacion_min no puede ser negativa")
    if ventana_min == 0:
        # La columna es NOT NULL en `modulo3.reglas_alertas`: el backend manda
        # 0 en una regla sin ventana. Solo tiene sentido con una lectura.
        ventana_min = None
    if lecturas > 1 and ventana_min is None:
        raise ReglaInvalida("ventana_confirmacion_min > 0 es obligatoria con más de una lectura")

    severidades = cuerpo.get("umbrales_severidad")
    if severidades is not None and not isinstance(severidades, dict):
        raise ReglaInvalida("umbrales_severidad debe ser un objeto")
    severidad = (severidades or {}).get("severidad", ev.MODERADO)
    if severidad not in SEVERIDADES:
        raise ReglaInvalida(f"umbrales_severidad.severidad debe ser una de {SEVERIDADES}")
    return _TipoA(claves, minimo, maximo, int(lecturas), ventana_min, severidad)


def _opcional(cuerpo: dict[str, Any], campo: str) -> float | None:
    valor = cuerpo.get(campo)
    return None if valor is None else numero(valor, campo)


def _span_ms(lecturas: Sequence[Reading]) -> int:
    tiempos = [r.timestamp_captura for r in lecturas]
    return round((max(tiempos) - min(tiempos)).total_seconds() * 1000)


def _igual(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, int | float) and not isinstance(valor, bool) and math.isfinite(valor)
