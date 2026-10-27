"""Perfil de hardware mínimo de RF-55.2 (reglas compuestas).

RF-55.1 (umbrales simples) es obligatorio y corre siempre. RF-55.2 es opcional
según el perfil: si el equipo no llega al mínimo se deshabilita, se avisa en
WARNING con el recurso concreto y el agente arranca igual. Nunca aborta.

El resultado viaja en el heartbeat como una entrada más de
`sensores_operativos` (D11, BRK-07), el campo que el broker ya acepta: no se
abre un canal nuevo.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CPU_MIN_MHZ = 160.0
RAM_MIN_KB = 256.0
DISCO_MIN_MB = 1.0

SENSOR_RF55_2 = "rf55_2"

_CPUFREQ = Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
_CPUINFO = Path("/proc/cpuinfo")
_MEMINFO = Path("/proc/meminfo")


@dataclass(frozen=True)
class Perfil:
    """Recursos medidos; None si no se pudieron medir (ej. desarrollo en Windows)."""

    cpu_mhz: float | None
    ram_libre_kb: float | None
    disco_libre_mb: float | None


@dataclass(frozen=True)
class Habilitacion:
    rf55_2: bool
    motivos: tuple[str, ...]  # por qué quedó deshabilitado (vacío si está activo)

    def sensor_operativo(self) -> dict[str, object]:
        return {"sensor": SENSOR_RF55_2, "operativo": self.rf55_2}


def medir(directorio_estado: Path) -> Perfil:
    return Perfil(
        cpu_mhz=_cpu_mhz(),
        ram_libre_kb=_meminfo_kb("MemAvailable"),
        disco_libre_mb=_disco_libre_mb(directorio_estado),
    )


def habilitar(perfil: Perfil, *, forzar_deshabilitado: bool = False) -> Habilitacion:
    """Decide si RF-55.2 corre. Un recurso que no se pudo medir no lo deshabilita:
    se registra y se asume suficiente (deshabilitar a ciegas sería peor)."""
    if forzar_deshabilitado:
        return Habilitacion(False, ("deshabilitado por EDGE_RF55_2_DESHABILITAR",))
    recursos = (
        ("CPU", perfil.cpu_mhz, CPU_MIN_MHZ, "MHz"),
        ("RAM disponible", perfil.ram_libre_kb, RAM_MIN_KB, "KB"),
        ("almacenamiento libre", perfil.disco_libre_mb, DISCO_MIN_MB, "MB"),
    )
    motivos = []
    for nombre, valor, minimo, unidad in recursos:
        if valor is None:
            logger.info("RF-55.2: no se pudo medir %s; se asume suficiente", nombre)
        elif valor < minimo:
            motivos.append(f"{nombre} {valor:g} {unidad} < {minimo:g} {unidad}")
    return Habilitacion(not motivos, tuple(motivos))


def verificar(directorio_estado: Path, *, forzar_deshabilitado: bool = False) -> Habilitacion:
    """Mide y decide al arrancar. Cualquier falla de la medición deja RF-55.2 activo."""
    try:
        resultado = habilitar(medir(directorio_estado), forzar_deshabilitado=forzar_deshabilitado)
    except Exception:
        logger.exception("RF-55.2: falló la verificación del perfil de hardware")
        resultado = habilitar(Perfil(None, None, None), forzar_deshabilitado=forzar_deshabilitado)
    for motivo in resultado.motivos:
        logger.warning("RF-55.2 deshabilitado (RF-55.1 sigue activo): %s", motivo)
    if resultado.rf55_2:
        logger.info("RF-55.2 activo: el perfil de hardware cumple el mínimo")
    return resultado


def _cpu_mhz() -> float | None:
    # En la Raspberry (ARM) /proc/cpuinfo no trae "cpu MHz"; cpufreq sí (en kHz).
    try:
        return int(_CPUFREQ.read_text(encoding="ascii").strip()) / 1000
    except (OSError, ValueError):
        pass
    try:
        for linea in _CPUINFO.read_text(encoding="ascii", errors="ignore").splitlines():
            clave, _, valor = linea.partition(":")
            if clave.strip() == "cpu MHz":
                return float(valor)
    except (OSError, ValueError):
        pass
    return None


def _meminfo_kb(campo: str) -> float | None:
    try:
        for linea in _MEMINFO.read_text(encoding="ascii").splitlines():
            clave, _, valor = linea.partition(":")
            if clave == campo:
                return float(valor.split()[0])  # "MemAvailable:  123456 kB"
    except (OSError, ValueError, IndexError):
        pass
    return None


def _disco_libre_mb(directorio: Path) -> float | None:
    # El directorio de estado puede no existir aún en el primer arranque.
    for candidato in (directorio, *directorio.parents):
        try:
            return shutil.disk_usage(candidato).free / (1024 * 1024)
        except OSError:
            continue
    return None
