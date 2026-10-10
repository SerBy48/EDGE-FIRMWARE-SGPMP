import json
from datetime import UTC, datetime, timedelta

from edge_agent.commands import handle_command
from edge_agent.config_store import DeviceConfig

CURRENT = DeviceConfig(frecuencia_captura_min=10, intervalo_transmision_min=15)


def cmd(**fields) -> bytes:
    return json.dumps(fields).encode()


def test_contrato_actual_del_broker():
    r = handle_command(cmd(frecuencia_captura=5, intervalo_transmision=30), CURRENT)
    assert r.changed
    assert r.config == DeviceConfig(5, 30)
    assert r.ack["tipo_mensaje"] == "ACK_CONFIGURACION"
    assert r.ack["resultado"] == "OK"
    assert "id_comando" not in r.ack
    assert "event_id" in r.ack


def test_contrato_extendido_hace_eco():
    r = handle_command(
        cmd(frecuencia_captura=5, intervalo_transmision=30, comando_id="c-1", config_version=7),
        CURRENT,
    )
    assert r.ack["id_comando"] == "c-1"  # el broker solo lee `id_comando`
    assert r.ack["config_version_aplicada"] == 7
    assert r.config.config_version == 7


def test_duplicado_se_reconfirma_sin_reaplicar():
    current = DeviceConfig(5, 30, comando_id="c-1", config_version=7)
    r = handle_command(
        cmd(frecuencia_captura=99, intervalo_transmision=99, comando_id="c-1"), current
    )
    assert not r.changed
    assert r.config == current
    assert r.ack["resultado"] == "OK"
    assert r.ack["config_version_aplicada"] == 7


def test_version_vieja_se_descarta():
    current = DeviceConfig(5, 30, comando_id="c-2", config_version=8)
    r = handle_command(
        cmd(frecuencia_captura=10, intervalo_transmision=10, comando_id="c-1", config_version=7),
        current,
    )
    assert not r.changed
    assert r.ack["config_version_aplicada"] == 8


def test_intervalo_heartbeat_opcional():
    r = handle_command(
        cmd(frecuencia_captura=10, intervalo_transmision=15, intervalo_heartbeat=2.5), CURRENT
    )
    assert r.config.intervalo_heartbeat_min == 2.5


def test_valores_invalidos_responden_error():
    for bad in (
        cmd(frecuencia_captura=0, intervalo_transmision=15),
        cmd(frecuencia_captura="10", intervalo_transmision=15),
        cmd(frecuencia_captura=True, intervalo_transmision=15),
        cmd(intervalo_transmision=15),
        cmd(frecuencia_captura=10, intervalo_transmision=15, intervalo_heartbeat=-1),
        b"no-json",
        b"[1, 2]",
    ):
        r = handle_command(bad, CURRENT)
        assert r.ack["resultado"] == "ERROR", bad
        assert r.ack["motivo"]
        assert not r.changed
        assert r.config == CURRENT


# --- TC-M09-252: anti-replay con id_comando / emitido_en del broker ----------

AHORA = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)


def emitido(hace_s: float) -> str:
    return (AHORA - timedelta(seconds=hace_s)).isoformat()


def broker(hace_s: float = 1, id_comando: str = "9f1c", **extra) -> bytes:
    """Comando tal como lo publica BROKER-MQTT-SGPMP."""
    campos = {"frecuencia_captura": 5, "intervalo_transmision": 30, **extra}
    return cmd(id_comando=id_comando, emitido_en=emitido(hace_s), **campos)


def con_reloj(raw: bytes, current: DeviceConfig = CURRENT):
    return handle_command(raw, current, ahora=AHORA.timestamp(), antiguedad_max_s=120)


def test_el_ack_devuelve_el_id_comando_del_broker():
    r = con_reloj(broker(id_comando="9f1c"))
    assert r.changed
    assert r.ack["resultado"] == "OK"
    assert r.ack["id_comando"] == "9f1c"
    assert r.config.comando_id == "9f1c"  # recordado para no re-aplicarlo


def test_comando_vencido_se_rechaza_si_el_reloj_esta_sincronizado():
    r = con_reloj(broker(hace_s=600))
    assert not r.changed
    assert r.config == CURRENT
    assert r.ack["resultado"] == "ERROR"
    assert r.ack["id_comando"] == "9f1c"
    assert "vencido" in r.ack["motivo"]


def test_comando_reciente_se_aplica():
    assert con_reloj(broker(hace_s=30)).changed


def test_sin_reloj_sincronizado_no_se_juzga_la_antiguedad():
    # Sin NTP basta con no re-aplicar un id ya procesado (contrato del broker).
    r = handle_command(broker(hace_s=600), CURRENT, ahora=None, antiguedad_max_s=120)
    assert r.changed


def test_reentrega_de_un_id_ya_procesado_se_reconfirma_aunque_sea_vieja():
    # QoS 1 tras una reconexión: no se re-aplica, pero se vuelve a confirmar.
    current = DeviceConfig(5, 30, comando_id="9f1c")
    r = con_reloj(broker(hace_s=600, frecuencia_captura=99), current)
    assert not r.changed
    assert r.ack["resultado"] == "OK"


def test_emitido_en_sin_zona_horaria_no_se_puede_juzgar():
    raw = cmd(
        id_comando="x",
        emitido_en="2020-01-01T00:00:00",
        frecuencia_captura=5,
        intervalo_transmision=30,
    )
    assert con_reloj(raw).changed


def test_camara_aplica_solo_fps():
    r = handle_command(cmd(fps=15, id_comando="c-9"), CURRENT)
    assert r.ack["resultado"] == "OK"
    assert r.ack["id_comando"] == "c-9"
    assert r.changed
    assert r.config.fps == 15
    # los tiempos de sensor no se tocan
    assert (r.config.frecuencia_captura_min, r.config.intervalo_transmision_min) == (10, 15)


def test_camara_rechaza_fps_fuera_de_rango_o_mezclado():
    for fields in ({"fps": 0}, {"fps": 61}, {"fps": "15"}, {"fps": 15, "frecuencia_captura": 5}):
        r = handle_command(cmd(**fields), CURRENT)
        assert r.ack["resultado"] == "ERROR", fields
        assert not r.changed
