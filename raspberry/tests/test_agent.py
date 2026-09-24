import json

from conftest import SERIAL, Harness

from edge_agent.mqtt_client import Connected, Message, PubAck

COMMAND_TOPIC = f"sgpmp/{SERIAL}/command"


def command(h: Harness, **fields) -> None:
    h.agent.handle_event(Message(COMMAND_TOPIC, json.dumps(fields).encode()), h.now)


def ack_all(h: Harness) -> None:
    for mid in list(h.agent._inflight):
        h.agent.handle_event(PubAck(mid), h.now)


def test_comando_publica_ack_inmediato_y_persiste(harness):
    command(harness, frecuencia_captura=5, intervalo_transmision=30)

    acks = harness.link.on("status")
    assert len(acks) == 1
    assert acks[0]["tipo_mensaje"] == "ACK_CONFIGURACION"
    assert acks[0]["resultado"] == "OK"
    assert harness.store.get(SERIAL).frecuencia_captura_min == 5

    # El ACK queda en el buffer hasta el PUBACK.
    assert harness.buffer.total() == 1
    ack_all(harness)
    assert harness.buffer.total() == 0


def test_comando_de_otro_serial_se_ignora(harness):
    harness.agent.handle_event(
        Message("sgpmp/OTRO/command", b'{"frecuencia_captura":5,"intervalo_transmision":5}'),
        harness.now,
    )
    assert harness.link.published == []


def test_ack_se_guarda_si_no_hay_conexion_y_sale_al_reconectar(tmp_path):
    h = Harness(tmp_path, connected=False)
    command(h, frecuencia_captura=5, intervalo_transmision=30, comando_id="c-1")
    assert h.link.published == []

    h.now += 600  # mucho más que los 30 s del broker: igual se envía
    h.link.connected = True
    h.agent.handle_event(Connected(session_present=True), h.now)
    acks = h.link.on("status")
    assert [a["comando_id"] for a in acks] == ["c-1"]
    h.buffer.close()


def test_heartbeat_efectivo_respeta_restriccion_12(harness):
    # HEARTBEAT_INTERVAL=300 s, frecuencia_captura=10 min → min(300, 300)
    assert harness.agent.heartbeat_interval_s(SERIAL) == 300
    command(harness, frecuencia_captura=5, intervalo_transmision=15)
    assert harness.agent.heartbeat_interval_s(SERIAL) == 150  # 5 min / 2
    command(harness, frecuencia_captura=30, intervalo_transmision=30, intervalo_heartbeat=2)
    assert harness.agent.heartbeat_interval_s(SERIAL) == 120  # el del comando manda


def test_heartbeat_periodico_con_campos_del_broker(harness):
    harness.advance(0)
    harness.link.take()
    harness.advance(299)
    assert harness.link.on("heartbeat") == []
    harness.advance(1)

    (hb,) = harness.link.on("heartbeat")
    assert hb["tipo_mensaje"] == "HEARTBEAT"
    assert hb["estado_local_buffer"] == "INACTIVO"
    assert hb["datos_pendientes_buffer"] == 0
    assert hb["reloj_sincronizado"] is True
    assert "version_firmware" in hb
    assert "estado_buffer_local" not in hb


def test_telemetria_espera_la_ventana_de_transmision(harness):
    harness.advance(0)  # captura t=0, transmisión a los 15 min
    assert harness.link.on("telemetry") == []

    harness.advance(15 * 60)
    tel = harness.link.on("telemetry")
    assert len(tel) == 2  # capturas de t=0 y t=15 min (el reloj saltó)
    assert tel[0]["variable"] == "temperatura_ambiente"
    assert tel[0]["origen"] == "TIEMPO_REAL"
    assert "event_id" in tel[0]["metadatos"]


def test_acortar_intervalo_adelanta_lo_ya_capturado(harness):
    harness.advance(0)  # captura t=0 con transmisión a los 15 min
    command(harness, frecuencia_captura=1, intervalo_transmision=1)
    harness.link.take()

    harness.advance(60)
    assert len(harness.link.on("telemetry")) == 2  # la de t=0 no espera la ventana vieja


def test_ventana_de_transmision_no_reporta_buffer_activo(harness):
    # Heartbeat (5 min) y transmisión (15 min) coinciden a los 15 min.
    for _ in range(3):
        harness.advance(300)
        ack_all(harness)
    estados = [hb["estado_local_buffer"] for hb in harness.link.on("heartbeat")]
    assert set(estados) == {"INACTIVO"}


def test_reconexion_heartbeat_activo_vaciado_e_inactivo(tmp_path):
    h = Harness(tmp_path, connected=False)
    for _ in range(4):
        h.advance(15 * 60)
    assert h.link.published == []  # ni telemetría ni heartbeats mientras está caído
    pendientes = h.buffer.total()
    assert pendientes > 0

    h.link.connected = True
    h.agent.handle_event(Connected(session_present=True), h.now)
    first_hb = h.link.on("heartbeat")[0]
    assert first_hb["estado_local_buffer"] == "ACTIVO"
    assert first_hb["datos_pendientes_buffer"] > 0

    tel = h.link.on("telemetry")
    assert len(tel) == pendientes
    assert tel[0]["origen"] == "BUFFER_LOCAL"

    h.link.take()
    h.advance(1)
    assert h.link.on("heartbeat") == []  # aún sin PUBACK: la sincronización no terminó
    ack_all(h)
    h.advance(1)
    (hb,) = h.link.on("heartbeat")
    assert hb["estado_local_buffer"] == "INACTIVO"
    h.buffer.close()


def test_config_sobrevive_reinicio_del_agente(tmp_path):
    h = Harness(tmp_path)
    command(h, frecuencia_captura=5, intervalo_transmision=30, comando_id="c-9")
    h.buffer.close()

    h2 = Harness(tmp_path)
    assert h2.store.get(SERIAL).comando_id == "c-9"
    # El ACK sin PUBACK de la corrida anterior se reenvía.
    h2.agent.handle_event(Connected(session_present=False), h2.now)
    assert [a["comando_id"] for a in h2.link.on("status")] == ["c-9"]
    h2.buffer.close()
