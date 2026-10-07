"""RF-17 (INC-M09-104-G29): umbral ambiental por el topic `command` y su ACK_UMBRAL."""

import json

from conftest import SERIAL, T0

from edge_agent.commands import handle_umbral
from edge_agent.config_store import UmbralStore
from edge_agent.mqtt_client import Message

COMMAND_TOPIC = f"sgpmp/{SERIAL}/command"
V1 = "2026-10-05T17:59:58+00:00"
V2 = "2026-10-06T08:00:00+00:00"


def umbral(**overrides) -> dict:
    """Comando tal como lo publica el broker (`dispatch.py::_cuerpo_comando`)."""
    data = {
        "id_comando": "c-1",
        "emitido_en": "2025-06-15T15:06:40+00:00",  # = T0
        "tipo_comando": "UMBRAL_AMBIENTAL",
        "id_umbral_ambiental": 7,
        "version": V1,
        "variable": "Temperatura del agua",
        "unidad": "°C",
        "valor_min": 18.0,
        "valor_max": 32.0,
        "niveles": [
            {"nivel": "normal", "limite_inferior": 18.0, "limite_superior": 24.0},
            {"nivel": "precaucion", "limite_inferior": 24.0, "limite_superior": 28.0},
            {"nivel": "critico", "limite_inferior": 28.0, "limite_superior": 32.0},
        ],
    }
    data.update(overrides)
    return data


def send(h, data: dict) -> dict:
    h.agent.handle_event(Message(COMMAND_TOPIC, json.dumps(data).encode()), h.now)
    (ack,) = h.link.on("status")[-1:]
    return ack


def test_guarda_el_umbral_y_confirma_con_ack_umbral(harness):
    ack = send(harness, umbral())

    assert ack["tipo_mensaje"] == "ACK_UMBRAL"
    assert ack["resultado"] == "OK"
    assert ack["id_comando"] == "c-1"
    guardado = harness.umbrales.get(7)
    assert guardado.variable == "Temperatura del agua"
    assert (guardado.valor_min, guardado.valor_max, guardado.version) == (18.0, 32.0, V1)
    assert [n["nivel"] for n in guardado.niveles] == ["normal", "precaucion", "critico"]
    # No toca la configuración RF-23 del serial.
    assert harness.store.get(SERIAL).frecuencia_captura_min == 10


def test_el_umbral_sobrevive_al_reinicio(harness):
    send(harness, umbral())
    assert UmbralStore(harness.tmp_path / "umbrales.json").get(7).unidad == "°C"


def test_reentrega_del_mismo_comando_se_confirma_sin_reaplicar(harness):
    send(harness, umbral(valor_max=32.0))
    ack = send(harness, umbral(valor_max=99.0))  # mismo id_comando: reentrega QoS 1
    assert ack["resultado"] == "OK"
    assert harness.umbrales.get(7).valor_max == 32.0


def test_version_mas_vieja_se_confirma_sin_pisar_la_vigente(harness):
    send(harness, umbral(id_comando="c-2", version=V2, valor_max=30.0))
    for vieja in (V1, None):  # null = nunca editado = la más vieja
        ack = send(harness, umbral(id_comando=f"c-{vieja}", version=vieja, valor_max=40.0))
        assert ack["resultado"] == "OK"
        assert harness.umbrales.get(7).valor_max == 30.0


def test_version_nueva_reemplaza_a_la_creacion_sin_version(harness):
    send(harness, umbral(version=None))
    send(harness, umbral(id_comando="c-2", version=V1, valor_max=30.0))
    assert harness.umbrales.get(7).valor_max == 30.0


def test_payload_invalido_responde_error_sin_guardar(harness):
    invalidos = [
        {"variable": ""},
        {"id_umbral_ambiental": 0},
        {"valor_min": 32.0, "valor_max": 18.0},
        {"valor_min": "18"},
        {"niveles": []},
        {"niveles": [{"nivel": "alto", "limite_inferior": 1, "limite_superior": 2}]},
        {"version": "2026-10-05T17:59:58"},  # sin zona: no se puede ordenar
    ]
    for i, campos in enumerate(invalidos):
        ack = send(harness, umbral(id_comando=f"e-{i}", **campos))
        assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_UMBRAL", "ERROR"), campos
        assert ack["motivo"]
    assert harness.umbrales.get(7) is None


def test_comando_vencido_se_rechaza():
    resultado = handle_umbral(
        umbral(emitido_en="2025-06-15T14:00:00+00:00"), None, ahora=T0, antiguedad_max_s=300
    )
    assert resultado.umbral is None
    assert resultado.ack["resultado"] == "ERROR"


def test_si_no_se_puede_persistir_no_confirma(harness, monkeypatch):
    def falla(_):
        raise OSError("disco lleno")

    monkeypatch.setattr(harness.umbrales, "set", falla)
    ack = send(harness, umbral())
    assert (ack["tipo_mensaje"], ack["resultado"]) == ("ACK_UMBRAL", "ERROR")


def test_archivo_danado_se_aparta_y_arranca_vacio(tmp_path):
    ruta = tmp_path / "umbrales.json"
    ruta.write_text("{no es json", encoding="utf-8")
    assert UmbralStore(ruta).get(7) is None
    assert (tmp_path / "umbrales.json.corrupt").exists()
