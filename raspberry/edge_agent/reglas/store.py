"""Persistencia local de las reglas de alerta RF-55 que llegan con `REGLA_COMPUESTA`.

Espeja a `UmbralStore` (`config_store.py`): un archivo propio, `reglas.json`,
junto a `config.json` y `umbrales.json`, con la misma escritura atómica. Es un
archivo nuevo: una versión anterior del agente (rollback del updater) no lo
lee y sigue funcionando con el resto del estado intacto.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from edge_agent.config_store import _escribir, _leer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Regla:
    """Regla de `modulo3.reglas_alertas` tal como la propaga el broker."""

    id_regla_alerta: int
    version_regla: int
    # Cuerpo libre que arma el backend (`payload_regla_compuesta()`); el broker
    # no lo interpreta, lo valida el evaluador.
    regla: dict[str, Any] = field(default_factory=dict)
    es_activa: bool = True
    comando_id: str | int | None = None


class ReglaStore:
    """Reglas RF-55 vigentes en este Edge, indexadas por `id_regla_alerta`.

    Solo guarda reglas activas: `es_activa=False` la retira. Con el Edge
    desconectado sigue rigiendo la última versión que llegó.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._reglas: dict[int, Regla] = _leer(
            path, "reglas", lambda data: {int(i): Regla(**r) for i, r in data.items()}
        )

    def get(self, id_regla_alerta: int) -> Regla | None:
        return self._reglas.get(id_regla_alerta)

    def activas(self) -> list[Regla]:
        return list(self._reglas.values())

    def set(self, regla: Regla) -> bool:
        """Guarda en disco antes de retornar: el ACK solo sale después de esto.

        Devuelve si hubo cambio. Una versión menor o igual a la vigente llegó
        tarde (cola persistente + reintentos) y se descarta.
        """
        vigente = self._reglas.get(regla.id_regla_alerta)
        if vigente is not None and regla.version_regla <= vigente.version_regla:
            return False
        reglas = dict(self._reglas)
        if regla.es_activa:
            reglas[regla.id_regla_alerta] = regla
        elif vigente is None:
            return False  # retirar una regla que no está: nada que hacer
        else:
            del reglas[regla.id_regla_alerta]
        _escribir(self._path, {"reglas": {str(i): asdict(r) for i, r in reglas.items()}})
        self._reglas = reglas
        return True
