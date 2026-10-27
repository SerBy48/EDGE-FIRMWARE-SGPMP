"""Buffer local persistente (outbox) para telemetría y ACKs.

Todo lo que debe llegar al broker pasa por acá antes de publicarse, y la fila
se borra recién con el PUBACK. Así nada se pierde si se cae la red o se
reinicia la Raspberry. Los heartbeats NO pasan por el buffer (plan, 2.1).

`estado_local_buffer` (nombre del broker/BD, RF-60):
- INACTIVO: nada pendiente de transmitir.
- ACTIVO:   hay filas cuya hora de transmisión ya pasó y no tienen PUBACK.
- LLENO:    se alcanzó `max_rows`; se descarta la telemetría más vieja.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KIND_TELEMETRY = "telemetry"
KIND_ACK = "ack"

ESTADO_INACTIVO = "INACTIVO"
ESTADO_ACTIVO = "ACTIVO"
ESTADO_LLENO = "LLENO"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    serial     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    topic      TEXT NOT NULL,
    payload    TEXT NOT NULL,
    due_at     REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbox_due ON outbox (due_at, id);
CREATE INDEX IF NOT EXISTS ix_outbox_serial_due ON outbox (serial, due_at);
"""


@dataclass(frozen=True)
class OutboxRow:
    id: int
    serial: str
    kind: str
    topic: str
    payload: dict[str, Any]
    due_at: float


class Buffer:
    def __init__(self, path: Path, max_rows: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._max_rows = max_rows
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        # FULL: en campo los cortes de luz son esperables y un ACK perdido
        # deja la config aplicada pero "no confirmada". El volumen es bajo.
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def enqueue(
        self,
        *,
        serial: str,
        kind: str,
        topic: str,
        payload: dict[str, Any],
        due_at: float,
        now: float,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO outbox (serial, kind, topic, payload, due_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (serial, kind, topic, json.dumps(payload), due_at, now),
        )
        self._evict_if_full()
        return int(cur.lastrowid)

    def due(self, now: float, limit: int, exclude: Collection[int] = ()) -> list[OutboxRow]:
        rows = self._db.execute(
            "SELECT id, serial, kind, topic, payload, due_at FROM outbox "
            "WHERE due_at <= ? ORDER BY due_at, id LIMIT ?",
            (now, limit + len(exclude)),
        ).fetchall()
        result = [
            OutboxRow(
                id=r[0], serial=r[1], kind=r[2], topic=r[3], payload=json.loads(r[4]), due_at=r[5]
            )
            for r in rows
            if r[0] not in exclude
        ]
        return result[:limit]

    def delete(self, row_id: int) -> None:
        self._db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))

    def advance_due(self, serial: str, due_at: float) -> None:
        """Adelanta a `due_at` la telemetría de `serial` programada más tarde.

        Se usa cuando un comando acorta `intervalo_transmision`: lo ya
        capturado no debe esperar la ventana vieja.
        """
        self._db.execute(
            "UPDATE outbox SET due_at = ? WHERE serial = ? AND kind = ? AND due_at > ?",
            (due_at, serial, KIND_TELEMETRY, due_at),
        )

    def pending_count(self, serial: str, now: float, exclude: Collection[int] = ()) -> int:
        """Filas vencidas de `serial`; `exclude` omite las que ya van en camino."""
        ids = list(exclude)
        placeholders = ",".join("?" * len(ids))
        not_in = f" AND id NOT IN ({placeholders})" if ids else ""
        (count,) = self._db.execute(
            f"SELECT COUNT(*) FROM outbox WHERE serial = ? AND due_at <= ?{not_in}",
            (serial, now, *ids),
        ).fetchone()
        return int(count)

    def total(self) -> int:
        (count,) = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()
        return int(count)

    def state(self, serial: str, now: float, exclude: Collection[int] = ()) -> str:
        if self.total() >= self._max_rows:
            return ESTADO_LLENO
        return ESTADO_ACTIVO if self.pending_count(serial, now, exclude) else ESTADO_INACTIVO

    def _evict_if_full(self) -> None:
        excess = self.total() - self._max_rows
        if excess <= 0:
            return
        # Nunca se descarta un ACK: si solo quedan ACKs, se tolera el exceso.
        cur = self._db.execute(
            "DELETE FROM outbox WHERE id IN ("
            "  SELECT id FROM outbox WHERE kind = ? ORDER BY created_at, id LIMIT ?"
            ")",
            (KIND_TELEMETRY, excess),
        )
        if cur.rowcount:
            logger.warning("Buffer lleno: se descartaron %d lecturas antiguas", cur.rowcount)
