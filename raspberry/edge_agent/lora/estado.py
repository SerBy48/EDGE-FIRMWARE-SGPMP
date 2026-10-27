"""Estado del protocolo LoRa v2 que debe sobrevivir a un reinicio de la Raspberry.

- Último `fcnt` aceptado de cada nodo: sin él, tras un reinicio se aceptaría
  una trama vieja repetida por un atacante.
- `fcnt` de bajada de cada nodo: un CONFIG nunca repite nonce.
- Nodos que ya transmitieron en v2: desde entonces sus tramas v1 se rechazan
  (anti-degradación).

Indexado por serial (estable), no por node_id. Se guarda en disco antes de
aceptar la trama o de transmitir el CONFIG, con la escritura atómica del
estado local (config_store).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from edge_agent.config_store import _escribir, _leer

logger = logging.getLogger(__name__)


class LoraEstado:
    def __init__(self, path: Path | None) -> None:
        """`path=None`: solo en memoria (monitor de enlace y tests)."""
        self._path = path
        self._lock = threading.Lock()
        datos = _leer(path, "lora", lambda d: d) if path is not None else {}
        self._subida: dict[str, int] = {s: int(v) for s, v in datos.get("subida", {}).items()}
        self._bajada: dict[str, int] = {s: int(v) for s, v in datos.get("bajada", {}).items()}
        self._migrados: set[str] = set(datos.get("migrados", []))

    def ultimo_fcnt(self, serial: str) -> int | None:
        with self._lock:
            return self._subida.get(serial)

    def aceptar_fcnt(self, serial: str, fcnt: int) -> None:
        """Registra una trama v2 válida: su fcnt y que el nodo ya migró."""
        with self._lock:
            self._subida[serial] = fcnt
            self._migrados.add(serial)
            self._guardar()

    def siguiente_bajada(self, serial: str) -> int:
        """Reserva el fcnt del próximo downlink y lo persiste antes de usarlo."""
        with self._lock:
            fcnt = self._bajada.get(serial, 0) + 1
            self._bajada[serial] = fcnt
            self._guardar()
            return fcnt

    def migrado(self, serial: str) -> bool:
        with self._lock:
            return serial in self._migrados

    def _guardar(self) -> None:
        if self._path is None:
            return
        _escribir(
            self._path,
            {
                "lora": {
                    "subida": self._subida,
                    "bajada": self._bajada,
                    "migrados": sorted(self._migrados),
                }
            },
        )
