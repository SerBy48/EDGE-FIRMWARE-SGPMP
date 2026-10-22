#!/usr/bin/env python3
"""Diagnóstico del módulo SX1276 cableado a la Raspberry (INSTALACION_RASPBERRY.md §3).

No necesita el edge_agent instalado y nunca transmite: solo lee y escribe
registros y escucha. Pasos:
  1. Abre /dev/spidevB.D (SPI habilitado).
  2. Resetea el módulo por GPIO (opcional).
  3. Lee RegVersion: 0x12 = SX1276/77/78/79 respondiendo.
  4. Escribe y relee registros: verifica MOSI y SCK, no solo MISO.
  5. Entra en RX a la frecuencia indicada y mide el ruido de fondo (RSSI).

Uso en la Pi (con edge-agent detenido si usa EDGE_SOURCE=lora):
    python3 lora_check.py                   # SPI0 CE0, reset GPIO25, 915 MHz
    python3 lora_check.py --no-reset        # si RESET no está conectado
Dependencias: python3-spidev y, para el reset, python3-gpiozero (apt).
"""

from __future__ import annotations

import argparse
import sys
import time

REG_OP_MODE = 0x01
REG_FRF_MSB = 0x06
REG_FRF_MID = 0x07
REG_FRF_LSB = 0x08
REG_RSSI_VALUE = 0x1B
REG_SYNC_WORD = 0x39
REG_VERSION = 0x42

MODE_LONG_RANGE = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_RX_CONTINUOUS = 0x05

CHIP_VERSION = 0x12
FXOSC_HZ = 32_000_000
RSSI_OFFSET_HF = -157  # banda alta (> 779 MHz), datasheet §5.5.5

OK = "\033[32mOK\033[0m"
FAIL = "\033[31mFALLA\033[0m"


def fail(msg: str) -> None:
    print(f"[{FAIL}] {msg}")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--bus", type=int, default=0)
    ap.add_argument("--device", type=int, default=0, help="0 = CE0 (GPIO8)")
    ap.add_argument("--reset-gpio", type=int, default=25, help="GPIO BCM de RESET")
    ap.add_argument("--no-reset", action="store_true", help="no tocar el pin RESET")
    ap.add_argument("--freq-hz", type=int, default=915_000_000)
    ap.add_argument("--seconds", type=float, default=5.0, help="duración de la medición de ruido")
    args = ap.parse_args()

    # 1. SPI
    try:
        import spidev
    except ImportError:
        fail("falta spidev: sudo apt install python3-spidev")
    path = f"/dev/spidev{args.bus}.{args.device}"
    spi = spidev.SpiDev()
    try:
        spi.open(args.bus, args.device)
    except FileNotFoundError:
        fail(f"no existe {path}: habilitar SPI (sudo raspi-config nonint do_spi 0) y reiniciar")
    except PermissionError:
        fail(f"sin permiso sobre {path}: agregar el usuario al grupo spi o usar sudo")
    spi.max_speed_hz = 1_000_000  # lento a propósito: tolera cables dupont largos
    spi.mode = 0
    print(f"[{OK}] {path} abierto")

    def read(reg: int) -> int:
        return spi.xfer2([reg & 0x7F, 0x00])[1]

    def write(reg: int, value: int) -> None:
        spi.xfer2([reg | 0x80, value & 0xFF])

    # 2. Reset
    if not args.no_reset:
        try:
            from gpiozero import DigitalOutputDevice
        except ImportError:
            fail("falta gpiozero: sudo apt install python3-gpiozero (o usar --no-reset)")
        rst = DigitalOutputDevice(args.reset_gpio, initial_value=True)
        rst.off()
        time.sleep(0.01)
        rst.on()
        time.sleep(0.01)
        print(f"[{OK}] reset por GPIO{args.reset_gpio}")

    # 3. Versión
    versions = {read(REG_VERSION) for _ in range(5)}
    if versions != {CHIP_VERSION}:
        seen = ", ".join(f"0x{v:02x}" for v in sorted(versions))
        if versions == {0x00}:
            hint = "MISO a GND, módulo sin alimentación o NSS mal cableado"
        elif versions == {0xFF}:
            hint = "MISO al aire o módulo sin alimentación (3.3 V y GND)"
        elif len(versions) > 1:
            hint = "lecturas inestables: cables flojos o demasiado largos"
        else:
            hint = "otro chip en el bus o SCK/MOSI cruzados"
        fail(f"RegVersion = {seen} (esperado 0x12): {hint}")
    print(f"[{OK}] RegVersion = 0x12 (SX1276/77/78/79 responde por SPI)")

    # 4. Escritura y relectura (MOSI/SCK)
    write(REG_OP_MODE, MODE_SLEEP)  # LongRangeMode solo cambia en SLEEP
    write(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP)
    frf = round(args.freq_hz * (1 << 19) / FXOSC_HZ)
    writes = {
        REG_FRF_MSB: (frf >> 16) & 0xFF,
        REG_FRF_MID: (frf >> 8) & 0xFF,
        REG_FRF_LSB: frf & 0xFF,
        REG_SYNC_WORD: 0x12,
    }
    for reg, value in writes.items():
        write(reg, value)
    writes[REG_OP_MODE] = MODE_LONG_RANGE | MODE_SLEEP
    readback = {reg: read(reg) for reg in writes}
    bad = {reg: (value, readback[reg]) for reg, value in writes.items() if readback[reg] != value}
    if bad:
        detail = ", ".join(
            f"reg 0x{r:02x}: escrito 0x{w:02x} leído 0x{got:02x}" for r, (w, got) in bad.items()
        )
        fail(f"la escritura no se conserva ({detail}): revisar MOSI y SCK")
    print(f"[{OK}] escritura/relectura de registros (modo LoRa, {args.freq_hz / 1e6:.3f} MHz)")

    # 5. Ruido de fondo en RX
    write(REG_OP_MODE, MODE_LONG_RANGE | MODE_STDBY)
    write(REG_OP_MODE, MODE_LONG_RANGE | MODE_RX_CONTINUOUS)
    time.sleep(0.05)
    samples = []
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        samples.append(RSSI_OFFSET_HF + read(REG_RSSI_VALUE))
        time.sleep(0.05)
    write(REG_OP_MODE, MODE_LONG_RANGE | MODE_SLEEP)
    spi.close()

    avg = sum(samples) / len(samples)
    print(
        f"[{OK}] RSSI de fondo en {args.seconds:.0f} s: "
        f"mín {min(samples)} / prom {avg:.1f} / máx {max(samples)} dBm"
    )
    print(
        "       Referencia: con antena y sin transmisores cerca suele quedar entre\n"
        "       -125 y -95 dBm. Si nunca cambia o queda pegado en -157, el receptor\n"
        "       no está midiendo; picos aislados son otros equipos de la banda."
    )
    print("Radio lista. El módulo quedó en SLEEP.")


if __name__ == "__main__":
    main()
