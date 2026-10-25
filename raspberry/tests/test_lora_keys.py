"""Identidad por serial y claves derivadas (docs/PROTOCOLO_LORA.md §11)."""

import pytest

from edge_agent.lora import keys
from edge_agent.lora.estado import LoraEstado

SERIAL = "TC-M09-G64-1789321890010"
SECRETO = bytes(range(32))


def test_vectores_de_derivacion():
    assert keys.node_id_de(SERIAL) == 0xD35D
    assert keys.clave_de(SECRETO, SERIAL).hex() == "cdfa72517a03fb69f7fa44563afd3b44"
    assert keys.huella(keys.clave_de(SECRETO, SERIAL)) == "219b1a1d"


def test_huella_es_el_key_check_value_de_aes():
    # AES-128 con clave 00..0f sobre un bloque en cero = c6a13b37...: el
    # firmware calcula lo mismo (key_check_value) y muestra igual huella.
    assert keys.huella(bytes(range(16))) == "c6a13b37"


def test_claves_independientes_por_nodo_y_por_sitio():
    otro_sitio = bytes(32)
    assert keys.clave_de(SECRETO, SERIAL) != keys.clave_de(SECRETO, SERIAL + "1")
    assert keys.clave_de(SECRETO, SERIAL) != keys.clave_de(otro_sitio, SERIAL)


@pytest.mark.parametrize("raw", ["", "abc", "00" * 31, "zz" * 32])
def test_secreto_invalido(raw):
    with pytest.raises(ValueError):
        keys.parse_secreto(raw)


def test_secrets_ini_trae_lo_que_compila_el_nodo():
    ini = keys.secrets_ini(SECRETO, SERIAL)
    assert "[nodo]" in ini
    assert f"'-DNODE_SERIAL=\"{SERIAL}\"'" in ini
    assert "-DNODE_ID=0xd35d" in ini
    assert "'-DLORA_KEY_HEX=\"cdfa72517a03fb69f7fa44563afd3b44\"'" in ini
    assert "219b1a1d" in ini


def test_lora_key_cli(monkeypatch, capsys):
    monkeypatch.setenv("EDGE_LORA_SECRETO_SITIO", SECRETO.hex())
    monkeypatch.setenv("EDGE_SERIALS", "EDGE-1")
    assert keys.main([SERIAL]) == 0
    out = capsys.readouterr()
    assert "-DNODE_ID=0xd35d" in out.out
    assert "no está en EDGE_SERIALS" in out.err

    monkeypatch.delenv("EDGE_LORA_SECRETO_SITIO")
    assert keys.main([SERIAL]) == 2
    assert keys.main([]) == 2


def test_estado_sobrevive_al_reinicio(tmp_path):
    path = tmp_path / "lora.json"
    estado = LoraEstado(path)
    assert estado.ultimo_fcnt("A") is None and not estado.migrado("A")
    estado.aceptar_fcnt("A", 41)
    assert estado.siguiente_bajada("A") == 1
    assert estado.siguiente_bajada("A") == 2

    reiniciado = LoraEstado(path)
    assert reiniciado.ultimo_fcnt("A") == 41
    assert reiniciado.migrado("A")
    assert reiniciado.siguiente_bajada("A") == 3  # nunca repite un nonce de bajada


def test_estado_ilegible_arranca_vacio(tmp_path):
    path = tmp_path / "lora.json"
    path.write_text("{no es json", encoding="utf-8")
    assert LoraEstado(path).ultimo_fcnt("A") is None


def test_secrets_ini_solo_ascii():
    # Se copia de la consola de la Pi al PC: un acento en otra codificación
    # deja a PlatformIO sin poder leerlo.
    keys.secrets_ini(SECRETO, SERIAL).encode("ascii")
