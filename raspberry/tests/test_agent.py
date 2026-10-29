# | Fecha      | Nombre   | Que se hizo                                                    |
# |------------|----------|----------------------------------------------------------------|
# | 2026-10-29 | Arekkazu | TC-DIS-148 RF-54: metricas_buffer en el heartbeat              |
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
    assert [a["id_comando"] for a in acks] == ["c-1"]
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
    # TC-DIS-148: contrato de MetricasBufferDTO del backend (4 llaves exactas).
    assert hb["metricas_buffer"] == {
        "capacidad_total": 1000,
        "capacidad_utilizada": harness.buffer.total(),
        "ultimo_timestamp_pendiente": None,
        "politica_buffer_activa": "POLITICA_SOBRESCRITURA",
    }


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
    assert first_hb["metricas_buffer"]["capacidad_utilizada"] == pendientes
    assert first_hb["metricas_buffer"]["ultimo_timestamp_pendiente"] is not None

    tel = h.link.on("telemetry")
    # Sin umbral, la variable dio además un ERROR_CONFIGURACION (TC-M03-083).
    assert len(tel) + len(h.link.on("evento")) == pendientes
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
    assert [a["id_comando"] for a in h2.link.on("status")] == ["c-9"]
    h2.buffer.close()


class StubSource:
    def __init__(self):
        self.alive = True
        self.applied = []
        self.silence = None

    def read(self, serial, now):
        return []

    def is_alive(self, serial, now, max_silence_s):
        self.silence = max_silence_s
        return self.alive

    def status(self, serial):
        return {"nivel_bateria_pct": 55.0, "calidad_senal_rssi": -90.0, "calidad_senal_snr": None}

    def apply_config(self, serial, config):
        self.applied.append((serial, config.frecuencia_captura_min))


def test_heartbeat_usa_estado_de_la_fuente_y_se_omite_si_el_nodo_calla(tmp_path):
    h = Harness(tmp_path)
    source = StubSource()
    h.agent._source = source

    h.advance(0)
    (hb,) = h.link.on("heartbeat")
    assert hb["nivel_bateria_pct"] == 55.0
    assert hb["calidad_senal_rssi"] == -90.0
    assert "calidad_senal_snr" not in hb
    assert source.silence == 2 * 15 * 60  # dos ciclos de intervalo_transmision

    source.alive = False
    h.link.take()
    h.advance(300)
    assert h.link.on("heartbeat") == []
    h.buffer.close()


def test_config_se_propaga_a_la_fuente_al_arrancar_y_con_cada_comando(tmp_path):
    from edge_agent.agent import Agent

    h = Harness(tmp_path)
    source = StubSource()
    agent = Agent(
        h.settings,
        h.link,
        h.store,
        h.buffer,
        source,
        umbrales=h.umbrales,
        reglas=h.reglas,
        clock=lambda: h.now,
    )
    assert source.applied == [(SERIAL, 10)]

    agent.handle_event(
        Message(COMMAND_TOPIC, b'{"frecuencia_captura":5,"intervalo_transmision":30}'), h.now
    )
    assert source.applied[-1] == (SERIAL, 5)
    h.buffer.close()


def test_comando_vencido_del_broker_responde_error_sin_aplicar(harness):
    # El agente le pasa a handle_command su reloj (sincronizado en el harness).
    from datetime import UTC, datetime

    viejo = datetime.fromtimestamp(harness.now - 3600, UTC).isoformat()
    command(
        harness, id_comando="abc", emitido_en=viejo, frecuencia_captura=5, intervalo_transmision=30
    )

    (ack,) = harness.link.on("status")
    assert ack["resultado"] == "ERROR" and ack["id_comando"] == "abc"
    assert harness.store.get(SERIAL).frecuencia_captura_min != 5


# --- RF-55: eventos Edge ---------------------------------------------------

# La fuente falsa genera 20-30 °C: con estas bandas toda lectura queda fuera de rango.
NIVELES_ESTRICTOS = (
    {"nivel": "normal", "limite_inferior": 0.0, "limite_superior": 10.0},
    {"nivel": "precaucion", "limite_inferior": 10.0, "limite_superior": 15.0},
    {"nivel": "critico", "limite_inferior": 15.0, "limite_superior": 18.0},
)


def con_umbral(h: Harness, niveles=NIVELES_ESTRICTOS) -> None:
    from edge_agent.config_store import Umbral

    h.umbrales.set(
        Umbral(
            id_umbral_ambiental=7,
            version=None,
            variable="temperatura_ambiente",
            unidad="C",
            # Las bandas cubren [valor_min, valor_max] (forma de RF-17).
            valor_min=min(n["limite_inferior"] for n in niveles),
            valor_max=max(n["limite_superior"] for n in niveles),
            niveles=tuple(niveles),
        )
    )


def test_evento_sale_de_inmediato_por_el_topic_evento(harness):
    con_umbral(harness)
    harness.advance(0)

    assert harness.link.on("telemetry") == []  # la lectura espera su ventana
    topic, evento = next((t, p) for t, p in harness.link.published if t.endswith("/evento"))
    assert topic == f"sgpmp/{SERIAL}/evento"
    assert evento["clasificacion_rf55"] == "DESVIACION_SIMPLE"
    assert evento["severidad"] == "CRITICO"
    assert evento["origen"] == "TIEMPO_REAL"
    assert evento["estado_conectividad"] is True
    assert "timestamp_envio" not in evento  # sin campos extra de primer nivel


def test_sin_umbral_un_error_de_configuracion_por_variable(harness):
    """TC-M03-083: sin umbral no se clasifica; el error sale una vez y las lecturas siguen."""
    harness.advance(0)
    harness.advance(15 * 60)
    harness.advance(15 * 60)
    # Sin PUBACK el mismo evento se reenvía: se cuentan los distintos.
    eventos = {e["metadata_edge"]["id_evento"]: e for e in harness.link.on("evento")}
    (evento,) = eventos.values()
    assert evento["clasificacion_rf55"] == "ERROR_CONFIGURACION"
    assert "severidad" not in evento
    meta = evento["metadata_edge"]
    assert (meta["variable"], meta["severidad_invalida"]) == ("temperatura_ambiente", True)
    assert meta["motivo"] == "sin umbral RF-17 para la variable en este Edge"
    assert harness.link.on("telemetry")  # la lectura cruda sigue saliendo


def test_evento_offline_se_persiste_y_sale_al_reconectar_sin_duplicados(tmp_path):
    h = Harness(tmp_path, connected=False)
    con_umbral(h)
    for _ in range(3):
        h.advance(10 * 60)
    assert h.link.published == []

    h.link.connected = True
    h.agent.handle_event(Connected(session_present=True), h.now)
    eventos = h.link.on("evento")
    assert len(eventos) == 3
    assert all(e["origen"] == "BUFFER_LOCAL" for e in eventos[:2])
    assert all(e["estado_conectividad"] is False for e in eventos)
    capturas = [e["timestamp_captura"] for e in eventos]
    assert capturas == sorted(capturas)  # FIFO

    ack_all(h)
    h.link.take()
    h.advance(1)
    assert h.link.on("evento") == []  # con PUBACK no se reenvía
    h.buffer.close()


def test_evento_de_mas_de_72_horas_se_descarta_localmente(tmp_path, caplog):
    h = Harness(tmp_path, connected=False)
    con_umbral(h)
    h.advance(0)
    assert h.buffer.total() == 2  # lectura + evento

    h.now += 73 * 3600
    h.link.connected = True
    h.agent.handle_event(Connected(session_present=True), h.now)

    assert h.link.on("evento") == []
    assert "más de 72 h" in caplog.text
    assert len(h.link.on("telemetry")) == 1  # la telemetría no se toca
    h.buffer.close()


def test_falla_del_evaluador_no_tumba_la_captura(harness, monkeypatch, caplog):
    import edge_agent.agent as agent_mod

    def explota(*args, **kwargs):
        raise RuntimeError("motor de reglas roto")

    monkeypatch.setattr(agent_mod, "evaluar_lote", explota)
    con_umbral(harness)
    harness.advance(0)
    harness.advance(15 * 60)

    assert len(harness.link.on("telemetry")) == 2
    assert harness.link.on("evento") == []
    assert "Falló la evaluación RF-55" in caplog.text


def test_error_de_configuracion_se_reporta_una_sola_vez(harness):
    con_umbral(harness, niveles=({"nivel": "alto", "limite_inferior": 0, "limite_superior": 1},))
    for _ in range(3):
        harness.advance(10 * 60)
        ack_all(harness)  # sin PUBACK el buffer reintentaría el mismo evento

    (evento,) = harness.link.on("evento")
    assert evento["clasificacion_rf55"] == "ERROR_CONFIGURACION"
    assert "severidad" not in evento
