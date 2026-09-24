"""Driver mínimo del SX1276/RFM95 en modo LoRa, por SPI (datasheet Semtech rev. 7).

Las interrupciones se consultan por registro (polling de RegIrqFlags) en vez
de usar el pin DIO0: así no depende de librerías GPIO, que cambian entre
modelos de Raspberry. El reset por GPIO es opcional (callable inyectado).

Solo lo usa el hilo del gateway (`gateway.py`): no es thread-safe.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

REG_FIFO = 0x00
REG_OP_MODE = 0x01
REG_FRF_MSB = 0x06
REG_FRF_MID = 0x07
REG_FRF_LSB = 0x08
REG_PA_CONFIG = 0x09
REG_OCP = 0x0B
REG_LNA = 0x0C
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_TX_BASE_ADDR = 0x0E
REG_FIFO_RX_BASE_ADDR = 0x0F
REG_FIFO_RX_CURRENT_ADDR = 0x10
REG_IRQ_FLAGS = 0x12
REG_RX_NB_BYTES = 0x13
REG_PKT_SNR_VALUE = 0x19
REG_PKT_RSSI_VALUE = 0x1A
REG_MODEM_CONFIG_1 = 0x1D
REG_MODEM_CONFIG_2 = 0x1E
REG_PREAMBLE_MSB = 0x20
REG_PREAMBLE_LSB = 0x21
REG_PAYLOAD_LENGTH = 0x22
REG_MODEM_CONFIG_3 = 0x26
REG_DETECTION_OPTIMIZE = 0x31
REG_DETECTION_THRESHOLD = 0x37
REG_SYNC_WORD = 0x39
REG_VERSION = 0x42
REG_PA_DAC = 0x4D

MODE_LONG_RANGE = 0x80
MODE_SLEEP = 0x00
MODE_STDBY = 0x01
MODE_TX = 0x03
MODE_RX_CONTINUOUS = 0x05

IRQ_TX_DONE = 0x08
IRQ_PAYLOAD_CRC_ERROR = 0x20
IRQ_RX_DONE = 0x40

CHIP_VERSION = 0x12
FXOSC_HZ = 32_000_000
RSSI_OFFSET_HF = -157  # puerto HF (banda 862–1020 MHz)

BANDWIDTHS_HZ = {
    7_800: 0,
    10_400: 1,
    15_600: 2,
    20_800: 3,
    31_250: 4,
    41_700: 5,
    62_500: 6,
    125_000: 7,
    250_000: 8,
    500_000: 9,
}


class RadioError(RuntimeError):
    pass


class SpiDevice(Protocol):
    """Interfaz de `spidev.SpiDev` que usa el driver."""

    def xfer2(self, data: list[int]) -> list[int]: ...


@dataclass(frozen=True)
class RadioConfig:
    freq_hz: int
    tx_power_dbm: int
    sf: int = 9
    bw_hz: int = 125_000
    cr: int = 5  # 4/5
    sync_word: int = 0x12
    preamble: int = 8

    def validate(self) -> None:
        if not 862_000_000 <= self.freq_hz <= 1_020_000_000:
            raise RadioError(f"frecuencia fuera de la banda HF del SX1276: {self.freq_hz}")
        if not 7 <= self.sf <= 12:
            raise RadioError(f"SF no soportado: {self.sf} (7–12)")
        if self.bw_hz not in BANDWIDTHS_HZ:
            raise RadioError(f"ancho de banda no soportado: {self.bw_hz}")
        if not 5 <= self.cr <= 8:
            raise RadioError(f"coding rate no soportado: 4/{self.cr}")
        if not 2 <= self.tx_power_dbm <= 20:
            raise RadioError(f"potencia fuera de rango PA_BOOST: {self.tx_power_dbm} dBm")


@dataclass(frozen=True)
class Packet:
    payload: bytes
    rssi_dbm: float
    snr_db: float


class SX1276:
    def __init__(
        self,
        spi: SpiDevice,
        reset: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spi = spi
        self._reset = reset
        self._sleep = sleep
        self._clock = clock
        self.crc_errors = 0

    # --- registros ------------------------------------------------------

    def read(self, reg: int) -> int:
        return self._spi.xfer2([reg & 0x7F, 0x00])[1]

    def write(self, reg: int, value: int) -> None:
        self._spi.xfer2([reg | 0x80, value & 0xFF])

    def _read_burst(self, reg: int, length: int) -> bytes:
        return bytes(self._spi.xfer2([reg & 0x7F] + [0x00] * length)[1:])

    def _write_burst(self, reg: int, data: bytes) -> None:
        self._spi.xfer2([reg | 0x80, *data])

    def _mode(self, mode: int) -> None:
        self.write(REG_OP_MODE, MODE_LONG_RANGE | mode)

    # --- operación ------------------------------------------------------

    def init(self, cfg: RadioConfig) -> None:
        cfg.validate()
        if self._reset is not None:
            self._reset()
        version = self.read(REG_VERSION)
        if version != CHIP_VERSION:
            raise RadioError(
                f"SX1276 no responde por SPI (RegVersion=0x{version:02x}, "
                f"esperado 0x{CHIP_VERSION:02x}): revisar cableado y que SPI esté habilitado"
            )
        # El bit LongRangeMode solo se puede cambiar en SLEEP.
        self.write(REG_OP_MODE, MODE_SLEEP)
        self._mode(MODE_SLEEP)

        frf = round(cfg.freq_hz * (1 << 19) / FXOSC_HZ)
        self.write(REG_FRF_MSB, frf >> 16)
        self.write(REG_FRF_MID, frf >> 8)
        self.write(REG_FRF_LSB, frf)

        self.write(REG_FIFO_TX_BASE_ADDR, 0x00)
        self.write(REG_FIFO_RX_BASE_ADDR, 0x00)
        self.write(REG_LNA, 0x23)  # ganancia máxima + boost HF

        self.write(REG_MODEM_CONFIG_1, (BANDWIDTHS_HZ[cfg.bw_hz] << 4) | ((cfg.cr - 4) << 1))
        self.write(REG_MODEM_CONFIG_2, (cfg.sf << 4) | 0x04)  # CRC de payload activado
        symbol_ms = (1 << cfg.sf) / cfg.bw_hz * 1000
        ldro = 0x08 if symbol_ms > 16 else 0x00
        self.write(REG_MODEM_CONFIG_3, ldro | 0x04)  # AGC automático
        self.write(REG_PREAMBLE_MSB, cfg.preamble >> 8)
        self.write(REG_PREAMBLE_LSB, cfg.preamble)
        self.write(REG_SYNC_WORD, cfg.sync_word)
        self.write(REG_DETECTION_OPTIMIZE, 0xC3)  # SF7–SF12
        self.write(REG_DETECTION_THRESHOLD, 0x0A)
        self._set_tx_power(cfg.tx_power_dbm)
        self._mode(MODE_STDBY)

    def _set_tx_power(self, dbm: int) -> None:
        # Módulos tipo RFM95 solo tienen la salida PA_BOOST cableada.
        if dbm > 17:
            self.write(REG_PA_DAC, 0x87)  # +20 dBm
            self.write(REG_PA_CONFIG, 0x80 | (dbm - 5))
            self.write(REG_OCP, 0x20 | 17)  # 140 mA
        else:
            self.write(REG_PA_DAC, 0x84)
            self.write(REG_PA_CONFIG, 0x80 | (dbm - 2))
            self.write(REG_OCP, 0x20 | 11)  # 100 mA

    def receive(self) -> None:
        """Entra en RX continuo: las tramas quedan en la FIFO hasta `poll()`."""
        self.write(REG_FIFO_ADDR_PTR, 0x00)
        self.write(REG_IRQ_FLAGS, 0xFF)
        self._mode(MODE_RX_CONTINUOUS)

    def poll(self) -> Packet | None:
        flags = self.read(REG_IRQ_FLAGS)
        if not flags & IRQ_RX_DONE:
            return None
        self.write(REG_IRQ_FLAGS, 0xFF)
        if flags & IRQ_PAYLOAD_CRC_ERROR:
            self.crc_errors += 1
            return None
        length = self.read(REG_RX_NB_BYTES)
        self.write(REG_FIFO_ADDR_PTR, self.read(REG_FIFO_RX_CURRENT_ADDR))
        payload = self._read_burst(REG_FIFO, length)

        raw_snr = self.read(REG_PKT_SNR_VALUE)
        snr = (raw_snr - 256 if raw_snr > 127 else raw_snr) / 4
        rssi = RSSI_OFFSET_HF + self.read(REG_PKT_RSSI_VALUE)
        if snr < 0:
            rssi += snr
        return Packet(payload, float(rssi), snr)

    def transmit(self, data: bytes, timeout_s: float = 2.0) -> None:
        """Transmite y bloquea hasta TxDone. Después hay que volver a `receive()`."""
        if not 0 < len(data) <= 255:
            raise RadioError(f"largo de trama inválido: {len(data)}")
        self._mode(MODE_STDBY)
        self.write(REG_FIFO_ADDR_PTR, 0x00)
        self._write_burst(REG_FIFO, data)
        self.write(REG_PAYLOAD_LENGTH, len(data))
        self.write(REG_IRQ_FLAGS, 0xFF)
        self._mode(MODE_TX)

        deadline = self._clock() + timeout_s
        while not self.read(REG_IRQ_FLAGS) & IRQ_TX_DONE:
            if self._clock() > deadline:
                self._mode(MODE_STDBY)
                raise RadioError("timeout esperando TxDone")
            self._sleep(0.002)
        self.write(REG_IRQ_FLAGS, 0xFF)

    def standby(self) -> None:
        self._mode(MODE_STDBY)


def open_spi(bus: int, device: int, speed_hz: int = 5_000_000) -> SpiDevice:
    import spidev  # solo existe en Linux; dependencia opcional [lora]

    spi = spidev.SpiDev()
    spi.open(bus, device)
    spi.max_speed_hz = speed_hz
    spi.mode = 0
    return spi


def gpio_reset(bcm_pin: int) -> Callable[[], None]:
    from gpiozero import DigitalOutputDevice  # dependencia opcional [lora]

    pin = DigitalOutputDevice(bcm_pin, initial_value=True)

    def reset() -> None:
        pin.off()
        time.sleep(0.01)
        pin.on()
        time.sleep(0.01)

    return reset
