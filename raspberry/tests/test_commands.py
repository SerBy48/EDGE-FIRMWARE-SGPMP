import json

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
    assert "comando_id" not in r.ack
    assert "event_id" in r.ack


def test_contrato_extendido_hace_eco():
    r = handle_command(
        cmd(frecuencia_captura=5, intervalo_transmision=30, comando_id="c-1", config_version=7),
        CURRENT,
    )
    assert r.ack["comando_id"] == "c-1"
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
