"""Monitor del enlace LoRa para pruebas de campo (Fase 3), sin MQTT.

Muestra cada trama recibida con RSSI/SNR y, al salir (Ctrl+C), un resumen
por nodo: recibidas, perdidas por `seq`, % de pérdida y señal promedio. Sirve
para medir alcance y elegir SF antes de fijar la config del sitio.

En la Raspberry, con el servicio detenido (la radio es una sola):
    sudo systemctl stop edge-agent
    sudo systemd-run --pipe --wait -p EnvironmentFile=/etc/sgpmp/edge-agent.env \\
        /opt/sgpmp-edge/venv/bin/python -m edge_agent.lora.monitor
Solo usa las variables EDGE_LORA_*.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from edge_agent.config import ConfigError, parse_lora_settings
from edge_agent.lora import protocol as p
from edge_agent.lora.sx1276 import SX1276, Packet, RadioConfig, gpio_reset, open_spi


@dataclass
class NodeStats:
    recibidas: int = 0
    perdidas: int = 0
    duplicadas: int = 0
    last_seq: int | None = None
    rssi: list[float] = field(default_factory=list)
    snr: list[float] = field(default_factory=list)

    def track(self, seq: int, packet: Packet) -> str:
        note = ""
        if self.last_seq is not None:
            gap = (seq - self.last_seq - 1) % 256
            if seq == self.last_seq:
                self.duplicadas += 1
                return "DUPLICADA"
            if gap and gap < 128:
                self.perdidas += gap
                note = f"(+{gap} perdidas)"
        self.last_seq = seq
        self.recibidas += 1
        self.rssi.append(packet.rssi_dbm)
        self.snr.append(packet.snr_db)
        return note


def format_line(packet: Packet, stats: dict[int, NodeStats], net_id: int) -> str:
    stamp = datetime.now().strftime("%H:%M:%S")
    signal = f"RSSI {packet.rssi_dbm:6.1f} dBm  SNR {packet.snr_db:5.1f} dB"
    try:
        frame, message = p.parse(packet.payload)
    except p.FrameError as exc:
        return f"{stamp}  {signal}  INVÁLIDA: {exc}  {packet.payload.hex()}"
    if frame.net_id != net_id:
        return f"{stamp}  {signal}  otra red (net_id=0x{frame.net_id:02x})"
    note = stats.setdefault(frame.node_id, NodeStats()).track(frame.seq, packet)
    return (
        f"{stamp}  {signal}  nodo 0x{frame.node_id:04x} seq {frame.seq:3d} "
        f"{p.MsgType(frame.msg_type).name:<10} {message} {note}"
    )


def summary(stats: dict[int, NodeStats]) -> str:
    lines = ["", "nodo     recibidas  perdidas  %pérdida  RSSI prom  SNR prom"]
    for node_id, s in sorted(stats.items()):
        total = s.recibidas + s.perdidas
        loss = 100 * s.perdidas / total if total else 0.0
        rssi = sum(s.rssi) / len(s.rssi) if s.rssi else float("nan")
        snr = sum(s.snr) / len(s.snr) if s.snr else float("nan")
        lines.append(
            f"0x{node_id:04x}  {s.recibidas:9d}  {s.perdidas:8d}  {loss:7.1f}%  "
            f"{rssi:9.1f}  {snr:8.1f}"
        )
    return "\n".join(lines)


def main() -> int:
    try:
        lora = parse_lora_settings(os.environ)
    except ConfigError as exc:
        print(f"Configuración LoRa inválida: {exc}", file=sys.stderr)
        return 2
    reset = gpio_reset(lora.reset_gpio) if lora.reset_gpio is not None else None
    radio = SX1276(open_spi(lora.spi_bus, lora.spi_device), reset=reset)
    radio.init(
        RadioConfig(
            freq_hz=lora.freq_hz,
            tx_power_dbm=lora.tx_power_dbm,
            sf=lora.sf,
            bw_hz=lora.bw_hz,
            cr=lora.cr,
            sync_word=lora.sync_word,
        )
    )
    radio.receive()
    print(
        f"Escuchando {lora.freq_hz / 1e6:.3f} MHz SF{lora.sf} BW{lora.bw_hz // 1000}k "
        f"net_id=0x{lora.net_id:02x} — Ctrl+C para terminar"
    )
    stats: dict[int, NodeStats] = {}
    try:
        while True:
            packet = radio.poll()
            if packet is None:
                time.sleep(0.01)
                continue
            print(format_line(packet, stats, lora.net_id), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        radio.standby()
        print(summary(stats))
        print(f"errores de CRC de radio: {radio.crc_errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
