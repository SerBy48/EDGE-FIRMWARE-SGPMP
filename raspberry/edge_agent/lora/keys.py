"""Identidad y claves de los nodos LoRa v2 (docs/PROTOCOLO_LORA.md §11).

Cada nodo se identifica con su serial de la plataforma. De ese serial y del
secreto del sitio (EDGE_LORA_SECRETO_SITIO, que genera install.sh) se derivan
su `node_id` de radio y su clave AES; nadie genera ni copia claves a mano:

    node_id    = primeros 2 bytes de SHA-256(serial)       (sin 0x0000 ni 0xFFFF)
    clave_nodo = HKDF-SHA256(secreto, sal="SGPMP-LoRa-v2", info=serial) -> 16 B

El serial es público (va en los topics MQTT): solo identifica, no protege.

Como comando entrega el `secrets.ini` con el que se flashea un nodo:

    sudo /opt/sgpmp-edge/bin/lora-key <serial>
"""

from __future__ import annotations

import hashlib
import os
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SAL = b"SGPMP-LoRa-v2"
CLAVE_LEN = 16
SECRETO_LEN = 32
_NODE_ID_RESERVADOS = (0x0000, 0xFFFF)  # 0x0000 = la propia Raspberry


def node_id_de(serial: str) -> int:
    digest = hashlib.sha256(serial.encode("utf-8")).digest()
    for i in range(0, len(digest), 2):
        node_id = int.from_bytes(digest[i : i + 2], "big")
        if node_id not in _NODE_ID_RESERVADOS:
            return node_id
    raise ValueError(f"sin node_id válido para {serial!r}")  # 2^-256: no ocurre


def clave_de(secreto: bytes, serial: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=CLAVE_LEN, salt=SAL, info=serial.encode("utf-8"))
    return hkdf.derive(secreto)


def huella(clave: bytes) -> str:
    """Key check value: 4 bytes de cifrar un bloque en cero. Compara claves sin mostrarlas."""
    cifrador = Cipher(algorithms.AES(clave), modes.ECB()).encryptor()
    return (cifrador.update(bytes(16)) + cifrador.finalize())[:4].hex()


def parse_secreto(raw: str) -> bytes:
    """EDGE_LORA_SECRETO_SITIO: 64 caracteres hexadecimales (256 bits)."""
    try:
        secreto = bytes.fromhex(raw.strip())
    except ValueError as exc:
        raise ValueError("EDGE_LORA_SECRETO_SITIO debe ser hexadecimal") from exc
    if len(secreto) != SECRETO_LEN:
        raise ValueError(
            f"EDGE_LORA_SECRETO_SITIO debe tener {2 * SECRETO_LEN} caracteres hex, "
            f"tiene {2 * len(secreto)}"
        )
    return secreto


def secrets_ini(secreto: bytes, serial: str) -> str:
    """El archivo esp32/secrets.ini de un nodo (PlatformIO lo incluye con extra_configs).

    Solo ASCII: se copia de la consola de la Pi a un editor del PC, y un acento
    en otra codificación deja a PlatformIO sin poder leer el archivo.
    """
    clave = clave_de(secreto, serial)
    return (
        f"; Nodo {serial}: generado en la Raspberry con lora-key.\n"
        "; Contiene la clave del nodo: NO subir al repositorio (va en .gitignore).\n"
        f"; Huella de la clave: {huella(clave)} (el nodo la muestra al arrancar)\n"
        "[nodo]\n"
        "build_flags =\n"
        f"    '-DNODE_SERIAL=\"{serial}\"'\n"
        f"    -DNODE_ID=0x{node_id_de(serial):04x}\n"
        f"    '-DLORA_KEY_HEX=\"{clave.hex()}\"'\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0].startswith("-"):
        print("uso: lora-key <serial del nodo>", file=sys.stderr)
        return 2
    serial = args[0].strip()
    try:
        secreto = parse_secreto(os.environ.get("EDGE_LORA_SECRETO_SITIO", ""))
    except ValueError as exc:
        print(
            f"{exc}. ¿Se corrió con la configuración de la Raspberry (lora-key)?", file=sys.stderr
        )
        return 2
    seriales = {s.strip() for s in os.environ.get("EDGE_SERIALS", "").split(",") if s.strip()}
    if serial not in seriales:
        print(
            f"Aviso: {serial} no está en EDGE_SERIALS; agregarlo para que la Raspberry "
            "acepte sus tramas.",
            file=sys.stderr,
        )
    print(secrets_ini(secreto, serial), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
