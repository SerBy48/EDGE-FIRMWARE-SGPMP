"""Notificaciones a systemd (`Type=notify` + `WatchdogSec=`), sin dependencias.

Implementa el protocolo sd_notify: un datagrama al socket de `NOTIFY_SOCKET`.
Fuera de systemd (desarrollo, tests) todas las llamadas son no-op.

El watchdog se alimenta desde el loop principal del agente: si ese loop se
cuelga, systemd deja de recibir `WATCHDOG=1` y reinicia el servicio — un
proceso vivo pero trabado no se detecta con `Restart=always` solo.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class SystemdNotifier:
    def __init__(
        self,
        env: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        env = dict(os.environ) if env is None else env
        self._clock = clock
        self._address = self._parse_address(env.get("NOTIFY_SOCKET", ""))
        usec = env.get("WATCHDOG_USEC", "")
        # Se notifica a la mitad del plazo, como recomienda systemd.
        self._watchdog_every = int(usec) / 1_000_000 / 2 if usec.isdigit() else None
        self._last_ping = float("-inf")

    @property
    def enabled(self) -> bool:
        return self._address is not None

    def ready(self) -> None:
        self._send("READY=1")

    def stopping(self) -> None:
        self._send("STOPPING=1")

    def status(self, text: str) -> None:
        self._send(f"STATUS={text}")

    def watchdog(self) -> None:
        if self._watchdog_every is None:
            return
        now = self._clock()
        if now - self._last_ping >= self._watchdog_every:
            self._send("WATCHDOG=1")
            self._last_ping = now

    def _send(self, message: str) -> None:
        if self._address is None:
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(self._address)
                sock.sendall(message.encode("utf-8"))
        except OSError:
            logger.warning("No se pudo notificar a systemd: %s", message, exc_info=True)

    @staticmethod
    def _parse_address(raw: str) -> str | None:
        if not raw or not hasattr(socket, "AF_UNIX"):
            return None
        # "@nombre" es un socket abstracto de Linux.
        return "\0" + raw[1:] if raw.startswith("@") else raw
