"""Consultas al sistema operativo de la Raspberry."""

from __future__ import annotations

import subprocess


def ntp_sincronizado() -> bool:
    """`reloj_sincronizado` del heartbeat, según systemd-timesyncd/chrony.

    Fuera de un Linux con systemd (ej. desarrollo en Windows) devuelve False.
    """
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == "yes"
