"""Estado en memoria de RF-55.2: ventana de correlación y conteo de confirmaciones.

No se persiste: tras un reinicio arranca vacío, y es correcto (una
correlación a medias es peor que ninguna). Todo está acotado: claves por
(serial, variable) o (regla, serial), y un máximo de elementos por clave.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edge_agent.sources import Reading

# Límite duro de RF-55.2 para la correlación temporal entre variables.
VENTANA_MAX_S = 30.0
_LECTURAS_POR_VARIABLE = 16


class Ventana:
    """Últimas lecturas por (serial, variable) de los últimos `VENTANA_MAX_S` segundos."""

    def __init__(self, max_s: float = VENTANA_MAX_S) -> None:
        self._max_s = max_s
        self._lecturas: dict[tuple[str, str], deque[Reading]] = {}

    def agregar(self, reading: Reading, ahora: datetime) -> None:
        """Purga en cada inserción: nunca crece sin límite."""
        limite = ahora - timedelta(seconds=self._max_s)
        for clave, cola in list(self._lecturas.items()):
            vigentes = [r for r in cola if r.timestamp_captura >= limite]
            if vigentes:
                self._lecturas[clave] = deque(vigentes, maxlen=_LECTURAS_POR_VARIABLE)
            else:
                del self._lecturas[clave]
        if reading.timestamp_captura < limite:
            return
        clave = (reading.serial, reading.variable)
        self._lecturas.setdefault(clave, deque(maxlen=_LECTURAS_POR_VARIABLE)).append(reading)

    def ultima(
        self, variable: str, referencia: datetime, max_s: float, *, serial: str
    ) -> Reading | None:
        """La lectura más reciente de `variable` a ≤ `max_s` de `referencia`.

        Prefiere el mismo `serial` (las variables de un mismo nodo); si no, la
        de cualquier nodo de este Edge (ej. temperatura corporal de un collar).
        """
        claves = [(serial, variable)]
        claves += [c for c in self._lecturas if c[1] == variable and c[0] != serial]
        for clave in claves:
            for r in reversed(self._lecturas.get(clave, ())):
                if abs((r.timestamp_captura - referencia).total_seconds()) <= max_s:
                    return r
        return None

    def __len__(self) -> int:
        return sum(len(cola) for cola in self._lecturas.values())


class Confirmaciones:
    """Desviaciones recientes por (id_regla, versión, serial) de las reglas Tipo A."""

    def __init__(self) -> None:
        self._desvios: dict[tuple[int, int, str], deque[datetime]] = {}

    def registrar(
        self, clave: tuple[int, int, str], cuando: datetime, ventana_s: float, n: int
    ) -> bool:
        """Anota una desviación; True si con ella hay `n` dentro de la ventana.

        Al confirmar se reinicia la cuenta: la siguiente alerta necesita otras
        `n` desviaciones, no una por cada lectura que sigue fuera de rango.
        """
        cola = self._desvios.setdefault(clave, deque(maxlen=n))
        cola.append(cuando)
        while cola and (cuando - cola[0]).total_seconds() > ventana_s:
            cola.popleft()
        if len(cola) >= n:
            cola.clear()
            return True
        return False

    def retener(self, reglas: Iterable[tuple[int, int]]) -> None:
        """Olvida los conteos de reglas retiradas o reemplazadas por otra versión."""
        vigentes = set(reglas)
        for clave in list(self._desvios):
            if clave[:2] not in vigentes:
                del self._desvios[clave]


@dataclass
class EstadoCompuestas:
    ventana: Ventana = field(default_factory=Ventana)
    confirmaciones: Confirmaciones = field(default_factory=Confirmaciones)
