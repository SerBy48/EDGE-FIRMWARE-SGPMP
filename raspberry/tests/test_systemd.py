import socket

import pytest

from edge_agent.systemd import SystemdNotifier


def test_sin_systemd_es_noop():
    notifier = SystemdNotifier(env={})
    assert not notifier.enabled
    notifier.ready()
    notifier.watchdog()


@pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX") or not hasattr(socket, "SOCK_DGRAM"),
    reason="requiere sockets unix de datagrama (Linux)",
)
def test_envia_ready_y_watchdog_a_la_mitad_del_plazo(tmp_path):
    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(1)
    now = [0.0]
    notifier = SystemdNotifier(
        env={"NOTIFY_SOCKET": path, "WATCHDOG_USEC": "10000000"}, clock=lambda: now[0]
    )

    notifier.ready()
    assert server.recv(64) == b"READY=1"

    notifier.watchdog()
    assert server.recv(64) == b"WATCHDOG=1"
    now[0] = 4.9
    notifier.watchdog()  # antes de la mitad de 10 s: no envía
    now[0] = 5.0
    notifier.watchdog()
    assert server.recv(64) == b"WATCHDOG=1"
    server.settimeout(0.1)
    with pytest.raises(TimeoutError):
        server.recv(64)
    server.close()
