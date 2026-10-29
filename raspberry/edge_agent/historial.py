"""Historial local de clasificaciones RF-55 (log persistente del Edge, TC-M03-100).

El buffer (`buffer.py`) es una cola de salida: borra cada fila con su PUBACK.
Este historial es independiente del backend y del buffer: guarda las últimas
`max_filas` clasificaciones (NORMAL incluidas) con la ruta que tomó cada evento
y si llegó al broker, para diagnóstico y auditoría en campo sin red.

- `ruta`:   DIRECTA (había conexión al clasificar: sale en el mismo ciclo),
            BUFFER (sin conexión: queda retenido hasta reconectar),
            LOCAL (NORMAL: no se publica; la lectura viaja como telemetría).
- `estado`: PENDIENTE → ENVIADO (PUBACK) o DESCARTADO (más de 72 h);
            LOCAL para NORMAL.

Un fallo del historial nunca debe frenar la captura ni el envío: el agente lo
usa a través de métodos que no lanzan.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from edge_agent.buffer import transaccion
from edge_agent.reglas.evento import NORMAL, iso_z

if TYPE_CHECKING:
    from edge_agent.sources import Reading

logger = logging.getLogger(__name__)

RUTA_DIRECTA = "DIRECTA"
RUTA_BUFFER = "BUFFER"
RUTA_LOCAL = "LOCAL"

ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_ENVIADO = "ENVIADO"
ESTADO_DESCARTADO = "DESCARTADO"
ESTADO_LOCAL = "LOCAL"

# Clasificación solo local: dato nulo, malformado o fuera de rango físico.
LECTURA_INVALIDA = "LECTURA_INVALIDA"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS historial (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    registrado_en     REAL NOT NULL,
    serial            TEXT NOT NULL,
    clasificacion     TEXT NOT NULL,
    severidad         TEXT,
    regla_aplicada    TEXT,
    id_evento         TEXT,
    timestamp_captura TEXT NOT NULL,
    variables         TEXT NOT NULL,
    ruta              TEXT NOT NULL,
    estado            TEXT NOT NULL,
    origen            TEXT,
    enviado_en        REAL,
    detalle           TEXT
);
CREATE INDEX IF NOT EXISTS ix_historial_evento ON historial (id_evento);
"""


# Campos de `metadata_edge` que se copian a `detalle`.
_DETALLE = ("motivo", "nivel_umbral", "severidad_invalida", "variable", "id_umbral_ambiental")


class Historial:
    def __init__(self, path: Path, max_filas: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._max_filas = max_filas
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def lote(self) -> AbstractContextManager[None]:
        """Las filas del bloque, en una sola transacción (un fsync)."""
        return transaccion(self._db)

    def registrar_evento(self, payload: dict[str, Any], *, ruta: str, ahora: float) -> None:
        """Evento ya encolado en el buffer (payload de `a_payload`).

        `ruta=RUTA_LOCAL`: un ERROR_CONFIGURACION repetido que no se publica;
        queda sin `id_evento` porque nunca llega al backend.
        """
        meta = payload.get("metadata_edge", {})
        local = ruta == RUTA_LOCAL
        variables = payload.get("variables_involucradas", [])
        self._insertar(
            ahora=ahora,
            serial=variables[0]["serial"] if variables else "",
            clasificacion=payload["clasificacion_rf55"],
            severidad=payload.get("severidad"),
            regla_aplicada=meta.get("regla_aplicada"),
            id_evento=None if local else meta.get("id_evento"),
            timestamp_captura=payload["timestamp_captura"],
            variables=[_resumen(v) for v in variables],
            ruta=ruta,
            estado=ESTADO_LOCAL if local else ESTADO_PENDIENTE,
            detalle={k: meta[k] for k in _DETALLE if k in meta} or None,
        )

    def registrar_invalida(self, reading: Reading, *, motivo: str, ahora: float) -> None:
        """Lectura descartada antes de clasificar (TC-M03-081): no genera evento."""
        valor = reading.valor_crudo
        if not (isinstance(valor, int | float) and math.isfinite(valor)) or isinstance(valor, bool):
            valor = repr(valor)  # NaN/None/texto: se guarda tal cual llegó, como texto
        self._insertar(
            ahora=ahora,
            serial=reading.serial,
            clasificacion=LECTURA_INVALIDA,
            severidad=None,
            regla_aplicada=None,
            id_evento=None,
            timestamp_captura=iso_z(reading.timestamp_captura),
            variables=[
                {"tipo_variable": reading.variable, "valor": valor, "unidad": reading.unidad}
            ],
            ruta=RUTA_LOCAL,
            estado=ESTADO_LOCAL,
            detalle={"motivo": motivo},
        )

    def registrar_normal(self, reading: Reading, *, id_umbral: int, ahora: float) -> None:
        """Lectura evaluada contra su umbral y en banda `normal` (no se publica)."""
        self._insertar(
            ahora=ahora,
            serial=reading.serial,
            clasificacion=NORMAL,
            severidad=None,
            regla_aplicada="UMBRAL_AMBIENTAL",
            id_evento=None,
            timestamp_captura=iso_z(reading.timestamp_captura),
            variables=[
                {
                    "tipo_variable": reading.variable,
                    "valor": reading.valor_crudo,
                    "unidad": reading.unidad,
                }
            ],
            ruta=RUTA_LOCAL,
            estado=ESTADO_LOCAL,
            detalle={"id_umbral_ambiental": id_umbral},
        )

    def marcar_enviado(self, id_evento: str, *, origen: str, ahora: float) -> None:
        self._actualizar(id_evento, ESTADO_ENVIADO, origen, ahora)

    def marcar_descartado(self, id_evento: str, *, motivo: str, ahora: float) -> None:
        self._actualizar(id_evento, ESTADO_DESCARTADO, None, ahora, {"motivo": motivo})

    def recientes(
        self, limite: int = 100, *, serial: str | None = None, clasificacion: str | None = None
    ) -> list[dict[str, Any]]:
        return leer(self._db, limite, serial=serial, clasificacion=clasificacion)

    def _insertar(self, *, ahora: float, variables: list[dict[str, Any]], **campos: Any) -> None:
        try:
            detalle = campos.pop("detalle")
            self._db.execute(
                "INSERT INTO historial (registrado_en, serial, clasificacion, severidad, "
                "regla_aplicada, id_evento, timestamp_captura, variables, ruta, estado, detalle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ahora,
                    campos["serial"],
                    campos["clasificacion"],
                    campos["severidad"],
                    campos["regla_aplicada"],
                    campos["id_evento"],
                    campos["timestamp_captura"],
                    json.dumps(variables),
                    campos["ruta"],
                    campos["estado"],
                    json.dumps(detalle) if detalle else None,
                ),
            )
            # Retención: solo las `max_filas` más recientes.
            self._db.execute(
                "DELETE FROM historial WHERE id <= (SELECT MAX(id) FROM historial) - ?",
                (self._max_filas,),
            )
        except sqlite3.Error:
            logger.exception("No se pudo registrar en el historial local")

    def _actualizar(
        self,
        id_evento: str,
        estado: str,
        origen: str | None,
        ahora: float,
        detalle: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._db.execute(
                "UPDATE historial SET estado = ?, origen = COALESCE(?, origen), enviado_en = ?, "
                "detalle = COALESCE(?, detalle) WHERE id_evento = ?",
                (estado, origen, ahora, json.dumps(detalle) if detalle else None, id_evento),
            )
        except sqlite3.Error:
            logger.exception("No se pudo actualizar el historial local (%s)", id_evento)


def leer(
    db: sqlite3.Connection,
    limite: int,
    *,
    serial: str | None = None,
    clasificacion: str | None = None,
) -> list[dict[str, Any]]:
    """Las `limite` filas más recientes, en orden cronológico (la más vieja primero)."""
    filtros, params = [], []
    if serial is not None:
        filtros.append("serial = ?")
        params.append(serial)
    if clasificacion is not None:
        filtros.append("clasificacion = ?")
        params.append(clasificacion)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    filas = db.execute(
        "SELECT id, registrado_en, serial, clasificacion, severidad, regla_aplicada, id_evento, "
        "timestamp_captura, variables, ruta, estado, origen, enviado_en, detalle "
        f"FROM historial {where} ORDER BY id DESC LIMIT ?",
        (*params, limite),
    ).fetchall()
    return [_fila(f) for f in reversed(filas)]


def _fila(f: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": f[0],
        "registrado_en": _iso(f[1]),
        "serial": f[2],
        "clasificacion": f[3],
        "severidad": f[4],
        "regla_aplicada": f[5],
        "id_evento": f[6],
        "timestamp_captura": f[7],
        "variables": json.loads(f[8]),
        "ruta": f[9],
        "estado": f[10],
        "origen": f[11],
        "enviado_en": _iso(f[12]) if f[12] is not None else None,
        "detalle": json.loads(f[13]) if f[13] else None,
    }


def _resumen(variable: dict[str, Any]) -> dict[str, Any]:
    return {k: variable[k] for k in ("tipo_variable", "valor", "unidad") if k in variable}


def _iso(epoch: float) -> str:
    return iso_z(datetime.fromtimestamp(epoch, UTC))
