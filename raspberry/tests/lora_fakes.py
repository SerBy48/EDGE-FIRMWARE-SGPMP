"""SX1276 simulado a nivel de registros SPI, para probar driver y gateway sin hardware."""

from __future__ import annotations

from edge_agent.lora import sx1276 as r


class FakeSx1276Spi:
    def __init__(self, version: int = r.CHIP_VERSION, tx_completes: bool = True) -> None:
        self.regs = bytearray(0x80)
        self.regs[r.REG_VERSION] = version
        self.fifo = bytearray(256)
        self.transmitted: list[bytes] = []
        self.tx_completes = tx_completes

    @property
    def mode(self) -> int:
        return self.regs[r.REG_OP_MODE] & 0x07

    def xfer2(self, data: list[int]) -> list[int]:
        reg = data[0] & 0x7F
        if data[0] & 0x80:
            self._write(reg, data[1:])
            return [0] * len(data)
        if reg == r.REG_FIFO:
            ptr = self.regs[r.REG_FIFO_ADDR_PTR]
            n = len(data) - 1
            out = [self.fifo[(ptr + i) % 256] for i in range(n)]
            self.regs[r.REG_FIFO_ADDR_PTR] = (ptr + n) % 256
            return [0, *out]
        return [0, self.regs[reg]]

    def _write(self, reg: int, values: list[int]) -> None:
        if reg == r.REG_FIFO:
            ptr = self.regs[r.REG_FIFO_ADDR_PTR]
            for i, v in enumerate(values):
                self.fifo[(ptr + i) % 256] = v
            self.regs[r.REG_FIFO_ADDR_PTR] = (ptr + len(values)) % 256
            return
        value = values[0]
        if reg == r.REG_IRQ_FLAGS:
            self.regs[reg] &= ~value & 0xFF  # escribir 1 limpia el flag
            return
        self.regs[reg] = value
        if reg == r.REG_OP_MODE and value & 0x07 == r.MODE_TX:
            base = self.regs[r.REG_FIFO_TX_BASE_ADDR]
            length = self.regs[r.REG_PAYLOAD_LENGTH]
            self.transmitted.append(bytes(self.fifo[base : base + length]))
            if self.tx_completes:
                self.regs[r.REG_IRQ_FLAGS] |= r.IRQ_TX_DONE
                self.regs[r.REG_OP_MODE] = r.MODE_LONG_RANGE | r.MODE_STDBY

    def inject(
        self, payload: bytes, rssi_raw: int = 100, snr_raw: int = 32, crc_error: bool = False
    ) -> None:
        base = self.regs[r.REG_FIFO_RX_BASE_ADDR]
        self.fifo[base : base + len(payload)] = payload
        self.regs[r.REG_FIFO_RX_CURRENT_ADDR] = base
        self.regs[r.REG_RX_NB_BYTES] = len(payload)
        self.regs[r.REG_PKT_RSSI_VALUE] = rssi_raw
        self.regs[r.REG_PKT_SNR_VALUE] = snr_raw & 0xFF
        self.regs[r.REG_IRQ_FLAGS] |= r.IRQ_RX_DONE | (r.IRQ_PAYLOAD_CRC_ERROR if crc_error else 0)
