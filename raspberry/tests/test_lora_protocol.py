"""Vectores de docs/PROTOCOLO_LORA.md §9 — los mismos que prueba el firmware ESP32."""

import pytest

from edge_agent.lora.protocol import (
    MAX_MEASUREMENTS,
    AckConfig,
    ConfigDownlink,
    Estado,
    FlagsEstado,
    FrameError,
    Measurement,
    MsgType,
    ResultadoConfig,
    Telemetria,
    build,
    crc16_ccitt,
    parse,
)

VECTORS = {
    "telemetria": (
        "012a01020107005a020141bc000002c08800005494",
        7,
        Telemetria(90, (Measurement(1, 23.5), Measurement(2, -4.25))),
    ),
    "estado": (
        "012a01020208570305000a000f010201ec76",
        8,
        Estado(87, 0x0305, 10, 15, 1, 2, FlagsEstado.REINICIO),
    ),
    "estado_sin_bateria": (
        "012a01020209ff0000000a000f000100b4f5",
        9,
        Estado(None, 0, 10, 15, 0, 1, 0),
    ),
    "ack_config": ("012a0102030a0306005feb", 10, AckConfig(0x0306, ResultadoConfig.OK)),
    "config": ("012a0102040303060005001e4a6c", 3, ConfigDownlink(0x0306, 5, 30)),
}


def test_crc_valor_de_referencia():
    assert crc16_ccitt(b"123456789") == 0x29B1


@pytest.mark.parametrize("name", VECTORS)
def test_vectores_ida_y_vuelta(name):
    hexa, seq, message = VECTORS[name]
    assert build(0x2A, 0x0102, seq, message).hex() == hexa

    frame, decoded = parse(bytes.fromhex(hexa))
    assert (frame.net_id, frame.node_id, frame.seq) == (0x2A, 0x0102, seq)
    assert decoded == message


def test_crc_corrupto_se_rechaza():
    data = bytearray.fromhex(VECTORS["config"][0])
    data[7] ^= 0x01
    with pytest.raises(FrameError, match="CRC"):
        parse(bytes(data))


@pytest.mark.parametrize(
    "raw",
    [
        b"\x01\x2a",  # corta
        bytes(60),  # larga
    ],
)
def test_largo_invalido(raw):
    with pytest.raises(FrameError):
        parse(raw)


def test_version_desconocida():
    from edge_agent.lora.protocol import Frame, encode_frame

    data = bytearray(encode_frame(Frame(1, 2, MsgType.CONFIG, 0, bytes(6))))
    data[0] = 2
    # Recalcular CRC para que falle por versión y no por CRC.
    body = bytes(data[:-2])
    data[-2:] = crc16_ccitt(body).to_bytes(2, "big")
    with pytest.raises(FrameError, match="versión"):
        parse(bytes(data))


def test_telemetria_con_largo_incoherente():
    from edge_agent.lora.protocol import Frame, encode_frame

    payload = bytes.fromhex("005a02") + bytes(5)  # dice 2 mediciones, trae 1
    with pytest.raises(FrameError):
        parse(encode_frame(Frame(1, 2, MsgType.TELEMETRIA, 0, payload)))


def test_maximo_de_mediciones_cabe_en_una_trama():
    many = Telemetria(0, tuple(Measurement(i, float(i)) for i in range(MAX_MEASUREMENTS)))
    assert len(build(1, 1, 0, many)) <= 51
    with pytest.raises(FrameError):
        build(1, 1, 0, Telemetria(0, many.measurements + (Measurement(99, 0.0),)))


def test_tipo_desconocido():
    from edge_agent.lora.protocol import Frame, encode_frame

    with pytest.raises(FrameError, match="desconocido"):
        parse(encode_frame(Frame(1, 2, 0x7F, 0, b"")))
