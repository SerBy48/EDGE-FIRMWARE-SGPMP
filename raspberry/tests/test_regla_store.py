"""RF-55: almacén local de reglas (`reglas.json`)."""

import json
import os

import pytest

from edge_agent.reglas.store import Regla, ReglaStore

CUERPO = {"nombre": "Estres termico por THI", "tipo_sensor": "TEMPERATURA", "umbral_min": "18.0"}


def regla(version: int = 1, **overrides) -> Regla:
    return Regla(**{"id_regla_alerta": 12, "version_regla": version, "regla": CUERPO, **overrides})


def test_alta_y_sobrevive_reinicio(tmp_path):
    store = ReglaStore(tmp_path / "reglas.json")

    assert store.set(regla(comando_id="c-1")) is True

    recargada = ReglaStore(tmp_path / "reglas.json")
    assert recargada.get(12) == regla(comando_id="c-1")
    assert recargada.activas() == [regla(comando_id="c-1")]


def test_actualiza_con_version_mayor(tmp_path):
    store = ReglaStore(tmp_path / "reglas.json")
    store.set(regla(1))

    assert store.set(regla(2, regla={**CUERPO, "umbral_min": "20.0"})) is True

    assert store.get(12).version_regla == 2
    assert ReglaStore(tmp_path / "reglas.json").get(12).regla["umbral_min"] == "20.0"


@pytest.mark.parametrize("version", [1, 2])
def test_descarta_version_menor_o_igual(tmp_path, version):
    store = ReglaStore(tmp_path / "reglas.json")
    store.set(regla(2))

    assert store.set(regla(version, regla={"nombre": "otra"})) is False

    assert store.get(12).regla == CUERPO
    assert ReglaStore(tmp_path / "reglas.json").get(12).regla == CUERPO


def test_es_activa_false_retira_la_regla(tmp_path):
    store = ReglaStore(tmp_path / "reglas.json")
    store.set(regla(1))
    store.set(Regla(id_regla_alerta=13, version_regla=1, regla=CUERPO))

    assert store.set(regla(2, es_activa=False)) is True

    assert store.get(12) is None
    assert [r.id_regla_alerta for r in store.activas()] == [13]
    assert ReglaStore(tmp_path / "reglas.json").get(12) is None


def test_retirar_regla_desconocida_no_escribe(tmp_path):
    store = ReglaStore(tmp_path / "reglas.json")

    assert store.set(regla(1, es_activa=False)) is False

    assert not (tmp_path / "reglas.json").exists()


def test_archivo_ausente_arranca_vacio(tmp_path):
    store = ReglaStore(tmp_path / "no-existe" / "reglas.json")

    assert store.activas() == []
    assert store.get(12) is None


@pytest.mark.parametrize(
    "contenido", ["{no es json", '{"otra": {}}', '{"reglas": {"12": {"x": 1}}}']
)
def test_archivo_corrupto_arranca_vacio_y_lo_aparta(tmp_path, caplog, contenido):
    path = tmp_path / "reglas.json"
    path.write_text(contenido, encoding="utf-8")

    store = ReglaStore(path)

    assert store.activas() == []
    assert (tmp_path / "reglas.json.corrupt").exists()
    assert "Estado local ilegible" in caplog.text
    # Sigue operativo: la siguiente regla se guarda normalmente.
    assert store.set(regla(1)) is True


def test_escritura_atomica_no_deja_el_archivo_a_medias(tmp_path, monkeypatch):
    path = tmp_path / "reglas.json"
    store = ReglaStore(path)
    store.set(regla(1))
    original = path.read_text(encoding="utf-8")

    def corte_de_luz(src, dst):
        raise OSError("corte de luz antes del rename")

    monkeypatch.setattr(os, "replace", corte_de_luz)
    with pytest.raises(OSError):
        store.set(regla(2, regla={"nombre": "nueva"}))

    # El archivo vigente sigue entero y es JSON válido; la memoria no cambió.
    assert path.read_text(encoding="utf-8") == original
    assert json.loads(original)["reglas"]["12"]["version_regla"] == 1
    assert store.get(12).version_regla == 1


def test_no_toca_config_ni_umbrales(tmp_path):
    (tmp_path / "config.json").write_text('{"serials": {}}', encoding="utf-8")
    (tmp_path / "umbrales.json").write_text('{"umbrales": {}}', encoding="utf-8")

    ReglaStore(tmp_path / "reglas.json").set(regla(1))

    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"serials": {}}'
    assert (tmp_path / "umbrales.json").read_text(encoding="utf-8") == '{"umbrales": {}}'
