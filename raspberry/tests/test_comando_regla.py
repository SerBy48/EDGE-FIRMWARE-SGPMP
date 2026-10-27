"""RF-55: regla de alerta por el topic `command` (`REGLA_COMPUESTA`) y su ACK_REGLA."""

import json

from conftest import SERIAL, T0

from edge_agent.commands import handle_regla
from edge_agent.mqtt_client import Message
from edge_agent.reglas.store import ReglaStore

COMMAND_TOPIC = f"sgpmp/{SERIAL}/command"
CUERPO = {
    "nombre": "Estres termico por THI",
    "tipo_sensor": "TEMPERATURA",
    "categoria_variable": "AMBIENTAL",
    "umbral_min": "18.0",
    "umbral_max": "30.0",
    "prevalece_sobre_regla_simple": True,
}


def regla(**overrides) -> dict:
    """Comando tal como lo publica el broker (`dispatch.py::_cuerpo_comando`)."""
    data = {
        "id_comando": "r-1",
        "emitido_en": "2025-06-15T15:06:40+00:00",  # = T0
        "tipo_comando": "REGLA_COMPUESTA",
        "id_regla_alerta": 12,
        "version_regla": 1,
        "es_activa": True,
        "regla": CUERPO,
    }
    data.update(overrides)
    return data


def send(h, data: dict) -> dict:
    h.agent.handle_event(Message(COMMAND_TOPIC, json.dumps(data).encode()), h.now)
    (ack,) = h.link.on("status")[-1:]
    return ack


def test_guarda_la_regla_y_confirma_con_ack_regla(harness):
    ack = send(harness, regla())

    assert (ack["tipo_mensaje"], ack["resultado"], ack["id_comando"]) == ("ACK_REGLA", "OK", "r-1")
    guardada = harness.reglas.get(12)
    assert (guardada.version_regla, guardada.regla) == (1, CUERPO)
    assert ReglaStore(harness.tmp_path / "reglas.json").get(12).regla == CUERPO
    # No toca la configuración RF-23 ni los umbrales RF-17.
    assert harness.store.get(SERIAL).frecuencia_captura_min == 10
    assert harness.umbrales.get(12) is None


def test_version_mayor_reemplaza(harness):
    send(harness, regla())
    nueva = {**CUERPO, "umbral_max": "35.0"}
    ack = send(harness, regla(id_comando="r-2", version_regla=2, regla=nueva))
    assert ack["resultado"] == "OK"
    assert harness.reglas.get(12).regla == nueva


def test_reentrega_del_mismo_comando_se_confirma_sin_reaplicar(harness):
    send(harness, regla())
    ack = send(harness, regla(version_regla=5, regla={"nombre": "otra"}))  # reentrega QoS 1
    assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_REGLA", "OK")
    assert harness.reglas.get(12).version_regla == 1


def test_version_menor_o_igual_se_confirma_sin_pisar_la_vigente(harness):
    send(harness, regla(version_regla=3))
    for i, vieja in enumerate((3, 2)):
        ack = send(harness, regla(id_comando=f"v-{i}", version_regla=vieja, regla={"x": 1}))
        assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_REGLA", "OK")
    assert harness.reglas.get(12).regla == CUERPO


def test_es_activa_false_retira_la_regla(harness):
    send(harness, regla())
    ack = send(harness, regla(id_comando="r-2", version_regla=2, es_activa=False))
    assert ack["resultado"] == "OK"
    assert harness.reglas.get(12) is None
    assert ReglaStore(harness.tmp_path / "reglas.json").activas() == []


def test_payload_invalido_responde_error_sin_guardar(harness):
    invalidos = [
        {"id_regla_alerta": 0},
        {"id_regla_alerta": "12"},
        {"id_regla_alerta": True},
        {"version_regla": 0},
        {"version_regla": 1.5},
        {"es_activa": "si"},
        {"es_activa": None},
        {"regla": {}},
        {"regla": ["no", "dict"]},
    ]
    for i, campos in enumerate(invalidos):
        ack = send(harness, regla(id_comando=f"e-{i}", **campos))
        assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_REGLA", "ERROR"), campos
        assert ack["id_comando"] == f"e-{i}"
        assert ack["motivo"]
    assert harness.reglas.activas() == []


def test_comando_vencido_se_rechaza():
    resultado = handle_regla(
        regla(emitido_en="2025-06-15T14:00:00+00:00"), None, ahora=T0, antiguedad_max_s=300
    )
    assert resultado.regla is None
    assert (resultado.ack["tipo_mensaje"], resultado.ack["resultado"]) == ("ACK_REGLA", "ERROR")


def test_si_no_se_puede_persistir_no_confirma(harness, monkeypatch):
    def falla(_):
        raise OSError("disco lleno")

    monkeypatch.setattr(harness.reglas, "set", falla)
    ack = send(harness, regla())
    assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_REGLA", "ERROR")
    assert ack["id_comando"] == "r-1"


def test_tipos_no_soportados_responden_error_con_su_ack(harness):
    casos = {
        "CONFIG_MOTOR_IA": "ACK_CONFIG_MOTOR",
        "TIPO_MODELO_ASIGNADO": "ACK_TIPO_MODELO",
        "INVENTADO": "ACK_CONFIGURACION",
    }
    for tipo, tipo_ack in casos.items():
        ack = send(harness, {"id_comando": f"n-{tipo}", "tipo_comando": tipo})
        assert (ack["tipo_mensaje"], ack["resultado"]) == (tipo_ack, "ERROR")
        assert ack["motivo"] == "tipo de comando no soportado"
    # Un tipo_comando que no es texto no tumba el despacho.
    ack = send(harness, {"id_comando": "n-lista", "tipo_comando": ["x"]})
    assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_CONFIGURACION", "ERROR")
    assert harness.store.get(SERIAL).frecuencia_captura_min == 10


def test_sin_tipo_comando_sigue_siendo_configuracion(harness):
    ack = send(harness, {"id_comando": "c-1", "frecuencia_captura": 5, "intervalo_transmision": 30})
    assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_CONFIGURACION", "OK")
    assert harness.store.get(SERIAL).frecuencia_captura_min == 5
