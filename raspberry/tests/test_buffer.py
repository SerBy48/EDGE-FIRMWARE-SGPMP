from edge_agent.buffer import KIND_ACK, KIND_TELEMETRY, Buffer


def put(buf: Buffer, kind=KIND_TELEMETRY, due_at=0.0, now=0.0, serial="A") -> int:
    return buf.enqueue(
        serial=serial, kind=kind, topic="t", payload={"k": 1}, due_at=due_at, now=now
    )


def test_persiste_entre_aperturas(tmp_path):
    buf = Buffer(tmp_path / "b.db", 10)
    put(buf)
    buf.close()

    buf = Buffer(tmp_path / "b.db", 10)
    assert buf.total() == 1
    buf.close()


def test_due_respeta_hora_y_exclusion(tmp_path):
    buf = Buffer(tmp_path / "b.db", 10)
    first = put(buf, due_at=10)
    second = put(buf, due_at=10)
    put(buf, due_at=100)

    assert [r.id for r in buf.due(now=50, limit=10)] == [first, second]
    assert [r.id for r in buf.due(now=50, limit=10, exclude={first})] == [second]
    buf.close()


def test_estados(tmp_path):
    buf = Buffer(tmp_path / "b.db", 3)
    assert buf.state("A", now=0) == "INACTIVO"

    row = put(buf, due_at=100)
    assert buf.state("A", now=50) == "INACTIVO"  # todavía no le toca salir
    assert buf.state("A", now=150) == "ACTIVO"
    assert buf.state("A", now=150, exclude={row}) == "INACTIVO"
    assert buf.state("B", now=150) == "INACTIVO"

    put(buf)
    put(buf)
    assert buf.state("A", now=150) == "LLENO"
    buf.close()


def test_lleno_descarta_telemetria_vieja_nunca_acks(tmp_path):
    buf = Buffer(tmp_path / "b.db", 2)
    ack = put(buf, kind=KIND_ACK, now=0)
    put(buf, now=1)
    newest = put(buf, now=2)

    assert {r.id for r in buf.due(now=10, limit=10)} == {ack, newest}

    put(buf, kind=KIND_ACK, now=3)
    put(buf, kind=KIND_ACK, now=4)
    # Solo quedan ACKs: se tolera el exceso antes que perder uno.
    assert buf.total() == 3
    assert all(r.kind == KIND_ACK for r in buf.due(now=10, limit=10))
    buf.close()
