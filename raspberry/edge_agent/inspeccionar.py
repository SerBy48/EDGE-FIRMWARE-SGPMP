"""Inspección de solo lectura del buffer RF-54 y del historial local (QA, campo).

Abre las bases en modo `ro`: se puede correr con el servicio en marcha y no
modifica nada. Uso en la Raspberry:

    sudo -u sgpmp-edge /opt/sgpmp-edge/venv/bin/python -m edge_agent.inspeccionar buffer
    ... -m edge_agent.inspeccionar buffer --tipo evento --json
    ... -m edge_agent.inspeccionar historial --limite 50 --clasificacion NORMAL

`--dir` apunta a otro directorio de estado (por defecto, el de EDGE_BUFFER_PATH).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edge_agent import historial
from edge_agent.reglas.evento import iso_z

_BUFFER_DEFECTO = "/var/lib/sgpmp-edge/buffer.db"


def filas_buffer(
    db: sqlite3.Connection, *, tipo: str | None = None, serial: str | None = None
) -> list[dict[str, Any]]:
    """Filas pendientes en el orden en que se van a enviar (`due_at`, `id`)."""
    filtros, params = [], []
    if tipo is not None:
        filtros.append("kind = ?")
        params.append(tipo)
    if serial is not None:
        filtros.append("serial = ?")
        params.append(serial)
    where = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    filas = db.execute(
        f"SELECT id, serial, kind, topic, payload, due_at, created_at FROM outbox {where} "
        "ORDER BY due_at, id",
        params,
    ).fetchall()
    resultado = []
    for id_, serial_, kind, topic, payload, due_at, created_at in filas:
        datos = json.loads(payload)
        fila: dict[str, Any] = {
            "id": id_,
            "tipo": kind,
            "serial": serial_,
            "topic": topic,
            "encolado_en": _iso(created_at),
            "enviar_desde": _iso(due_at),
        }
        if kind == "evento":
            fila.update(
                clasificacion=datos.get("clasificacion_rf55"),
                severidad=datos.get("severidad"),
                id_evento=datos.get("metadata_edge", {}).get("id_evento"),
                timestamp_captura=datos.get("timestamp_captura"),
                estado_conectividad=datos.get("estado_conectividad"),
            )
        elif kind == "telemetry":
            fila.update(variable=datos.get("variable"), valor=datos.get("valor_crudo"))
        resultado.append(fila)
    return resultado


def abrir_solo_lectura(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge_agent.inspeccionar", description=__doc__)
    parser.add_argument("--dir", type=Path, help="directorio de buffer.db e historial.db")
    parser.add_argument("--json", action="store_true", help="salida JSON (una lista)")
    sub = parser.add_subparsers(dest="que", required=True)
    p_buffer = sub.add_parser("buffer", help="cola local pendiente de envío (RF-54)")
    p_buffer.add_argument("--tipo", choices=("evento", "telemetry", "ack"))
    p_buffer.add_argument("--serial")
    p_hist = sub.add_parser("historial", help="clasificaciones recientes (log local)")
    p_hist.add_argument("--limite", type=int, default=100)
    p_hist.add_argument("--serial")
    p_hist.add_argument("--clasificacion")
    args = parser.parse_args(argv)

    directorio = args.dir or Path(os.environ.get("EDGE_BUFFER_PATH", _BUFFER_DEFECTO)).parent
    try:
        if args.que == "buffer":
            with closing(abrir_solo_lectura(directorio / "buffer.db")) as db:
                filas = filas_buffer(db, tipo=args.tipo, serial=args.serial)
        else:
            with closing(abrir_solo_lectura(directorio / "historial.db")) as db:
                filas = historial.leer(
                    db, args.limite, serial=args.serial, clasificacion=args.clasificacion
                )
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(filas, ensure_ascii=False, indent=2))
    else:
        for fila in filas:
            print(_linea(fila))
        print(f"-- {len(filas)} fila(s)", file=sys.stderr)
    return 0


def _linea(fila: dict[str, Any]) -> str:
    claves = (
        "id", "tipo", "registrado_en", "encolado_en", "serial", "clasificacion", "severidad",
        "ruta", "estado", "origen", "timestamp_captura", "variable", "valor", "id_evento",
    )  # fmt: skip
    return "  ".join(f"{k}={fila[k]}" for k in claves if fila.get(k) is not None)


def _iso(epoch: float) -> str:
    return iso_z(datetime.fromtimestamp(epoch, UTC))


if __name__ == "__main__":
    raise SystemExit(main())
