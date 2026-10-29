"""Rendimiento del Edge ante una ráfaga (TC-M03-102) y latencia (TC-M03-082)."""

import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import Harness
from test_agent import ack_all

from edge_agent import agent as agent_mod
from edge_agent.buffer import transaccion
from edge_agent.mqtt_client import PubAck

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import medir_rendimiento  # noqa: E402

SERIALS = ("IOT-A", "IOT-B", "IOT-C")


def commits(db: sqlite3.Connection) -> list[str]:
    """Registra cada COMMIT de la conexión (uno por fsync con `synchronous=FULL`)."""
    vistos: list[str] = []
    db.set_trace_callback(lambda sql: vistos.append(sql) if sql.startswith("COMMIT") else None)
    return vistos


# --- Transacciones -------------------------------------------------------------------


def test_transaccion_confirma_todo_junto_o_nada(tmp_path):
    db = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    db.execute("CREATE TABLE t (x)")
    with transaccion(db):
        db.execute("INSERT INTO t VALUES (1)")
        with transaccion(db):  # anidada: no abre otra
            db.execute("INSERT INTO t VALUES (2)")
    with pytest.raises(RuntimeError), transaccion(db):
        db.execute("INSERT INTO t VALUES (3)")
        raise RuntimeError
    assert [x for (x,) in db.execute("SELECT x FROM t")] == [1, 2]
    db.close()


def test_una_captura_escribe_buffer_e_historial_con_un_commit_cada_uno(tmp_path):
    h = Harness(tmp_path, historial=True)
    h.agent.capturar_ahora(h.settings.serials[0], h.now)  # arranque (schemas, etc.)
    del_buffer, del_historial = commits(h.buffer._db), commits(h.historial._db)
    h.advance(10 * 60)  # telemetría + evento de configuración + historial
    assert h.buffer.total() > 1
    assert len(del_buffer) == 1
    assert len(del_historial) == 1
    h.buffer.close()


def test_una_racha_de_puback_se_confirma_con_un_commit(tmp_path):
    h = Harness(tmp_path)
    for _ in range(5):
        h.advance(15 * 60)
    mids = list(h.agent._inflight)  # lo mismo que hace `ack_all`, pero en un solo lote
    assert len(mids) > 1
    vistos = commits(h.buffer._db)
    h.agent.handle_events([PubAck(mid) for mid in mids])
    assert len(vistos) == 1
    assert h.buffer.total() == 0
    h.buffer.close()


# --- Presupuesto por vuelta del loop -------------------------------------------------


def test_la_rafaga_se_reparte_en_vueltas_sin_perder_capturas(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "PRESUPUESTO_CAPTURAS_S", 0.0)  # una captura por vuelta
    h = Harness(tmp_path, serials=SERIALS)
    capturadas: list[str] = []
    original = h.agent._capture
    monkeypatch.setattr(
        h.agent, "_capture", lambda s, d, n: (capturadas.append(s), original(s, d, n))
    )

    h.agent.tick(h.now)
    assert capturadas == ["IOT-A"]
    assert h.agent.capturas_pendientes
    h.agent.tick(h.now)
    h.agent.tick(h.now)
    assert capturadas == list(SERIALS)
    assert not h.agent.capturas_pendientes
    # Las atrasadas mantienen su agenda: la siguiente captura de todas va a la vez.
    proximas = {h.agent._schedules[s].next_capture for s in SERIALS}
    assert len(proximas) == 1
    ack_all(h)
    h.buffer.close()


def test_con_capturas_pendientes_el_loop_no_espera_a_la_red(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "PRESUPUESTO_CAPTURAS_S", 0.0)
    h = Harness(tmp_path, serials=SERIALS)
    vueltas = []
    stop = threading.Event()

    def on_loop():
        vueltas.append(h.agent.capturas_pendientes)
        if len(vueltas) == 4:
            stop.set()

    inicio = time.perf_counter()
    h.agent.run(queue.Queue(), stop, on_loop)
    # Solo esperan a la red la primera vuelta y la última (sin atrasadas):
    # ~1 s. Esperando 0.5 s en cada una serían ~2 s.
    assert time.perf_counter() - inicio < 1.5
    assert vueltas == [False, True, True, False]
    h.buffer.close()


# --- Banco de medición (scripts/medir_rendimiento.py) ---------------------------------


def test_banco_rafaga_reducida_cumple_los_criterios():
    medicion = medir_rendimiento.rafaga(nodos=3, capturas=4)
    assert medicion.ok, medicion.criterios
    assert medicion.resultados["telemetria_registrada"] == 2 * 3 * 4 * 4
    assert medicion.resultados["eventos"] > 0


def test_banco_latencia_reducida_cumple_los_criterios():
    medicion = medir_rendimiento.latencia(capturas=20)
    assert medicion.ok, medicion.criterios
    assert medicion.resultados["eventos"] > 0
