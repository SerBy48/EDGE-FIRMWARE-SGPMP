import pytest
from lora_fakes import FakeSx1276Spi

from edge_agent.lora import sx1276 as r
from edge_agent.lora.sx1276 import SX1276, RadioConfig, RadioError

CFG = RadioConfig(freq_hz=915_000_000, tx_power_dbm=14)


def make(**kwargs):
    spi = FakeSx1276Spi(**kwargs)
    radio = SX1276(spi, sleep=lambda s: None)
    return spi, radio


def test_init_programa_registros():
    spi, radio = make()
    radio.init(CFG)

    frf = (spi.regs[r.REG_FRF_MSB] << 16) | (spi.regs[r.REG_FRF_MID] << 8) | spi.regs[r.REG_FRF_LSB]
    assert frf == 0xE4C000  # 915 MHz
    assert spi.regs[r.REG_MODEM_CONFIG_1] == 0x72  # BW125, CR4/5, header explícito
    assert spi.regs[r.REG_MODEM_CONFIG_2] == 0x94  # SF9 + CRC
    assert spi.regs[r.REG_MODEM_CONFIG_3] == 0x04  # sin LDRO a SF9/125k
    assert spi.regs[r.REG_SYNC_WORD] == 0x12
    assert spi.regs[r.REG_PA_CONFIG] == 0x80 | 12
    assert spi.regs[r.REG_OP_MODE] == r.MODE_LONG_RANGE | r.MODE_STDBY


def test_sf12_activa_low_data_rate_optimize():
    spi, radio = make()
    radio.init(RadioConfig(freq_hz=915_000_000, tx_power_dbm=20, sf=12))
    assert spi.regs[r.REG_MODEM_CONFIG_3] == 0x0C
    assert spi.regs[r.REG_PA_DAC] == 0x87


def test_chip_ausente():
    _, radio = make(version=0x00)
    with pytest.raises(RadioError, match="RegVersion"):
        radio.init(CFG)


@pytest.mark.parametrize(
    "cfg",
    [
        RadioConfig(freq_hz=433_000_000, tx_power_dbm=14),
        RadioConfig(freq_hz=915_000_000, tx_power_dbm=30),
        RadioConfig(freq_hz=915_000_000, tx_power_dbm=14, sf=6),
    ],
)
def test_config_invalida(cfg):
    _, radio = make()
    with pytest.raises(RadioError):
        radio.init(cfg)


def test_recepcion_con_rssi_y_snr():
    spi, radio = make()
    radio.init(CFG)
    radio.receive()
    assert spi.mode == r.MODE_RX_CONTINUOUS
    assert radio.poll() is None

    spi.inject(b"\x01\x02\x03", rssi_raw=60, snr_raw=-8)
    packet = radio.poll()
    assert packet.payload == b"\x01\x02\x03"
    assert packet.snr_db == -2.0
    assert packet.rssi_dbm == -157 + 60 - 2.0
    assert radio.poll() is None  # IRQ limpiada


def test_error_de_crc_se_descarta():
    spi, radio = make()
    radio.init(CFG)
    radio.receive()
    spi.inject(b"xx", crc_error=True)
    assert radio.poll() is None
    assert radio.crc_errors == 1


def test_transmision():
    spi, radio = make()
    radio.init(CFG)
    radio.transmit(b"hola")
    assert spi.transmitted == [b"hola"]


def test_transmision_sin_txdone_da_timeout():
    spi, _ = make(tx_completes=False)
    t = [0.0]

    def fake_sleep(s):
        t[0] += s

    radio = SX1276(spi, sleep=fake_sleep, clock=lambda: t[0])
    radio.init(CFG)
    with pytest.raises(RadioError, match="TxDone"):
        radio.transmit(b"x", timeout_s=0.1)
