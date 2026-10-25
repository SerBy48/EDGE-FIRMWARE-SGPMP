"""Vectores de docs/PROTOCOLO_LORA.md §9 — los mismos que prueba el firmware ESP32."""

import struct

import pytest

from edge_agent.lora.protocol import (
    MAX_MEASUREMENTS,
    V2_MAX_MEASUREMENTS,
    AckConfig,
    ConfigDownlink,
    Direccion,
    Estado,
    FlagsEstado,
    FrameError,
    Measurement,
    MicInvalido,
    MsgType,
    ResultadoConfig,
    Sensor,
    Telemetria,
    build,
    build_v2,
    crc16_ccitt,
    parse,
    parse_v2,
    peek_v2,
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
    "estado_sensores": (
        "012a0102020b570305000a000f0102020b0221c3",
        11,
        Estado(
            87,
            0x0305,
            10,
            15,
            1,
            2,
            FlagsEstado.ERROR_SENSOR,
            Sensor.DHT22 | Sensor.DS18B20 | Sensor.MAX30102,
            Sensor.DS18B20,
        ),
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


@pytest.mark.parametrize("extra", [b"\x01", b"\x01\x02\x03"])
def test_estado_con_largo_invalido(extra):
    from edge_agent.lora.protocol import Frame, encode_frame

    payload = bytes.fromhex(VECTORS["estado"][0])[6:-2] + extra  # 10 B + basura
    with pytest.raises(FrameError, match="ESTADO"):
        parse(encode_frame(Frame(1, 2, MsgType.ESTADO, 0, payload)))


def test_estado_sin_sensores_es_el_formato_anterior():
    # Un nodo con firmware anterior manda 10 B: sigue siendo válido.
    _, estado = parse(bytes.fromhex(VECTORS["estado"][0]))
    assert estado.sensores_detectados is None and estado.sensores_fallando is None


def test_describe_sensores():
    assert Sensor.describe(Sensor.DHT22 | Sensor.MPU6050) == "DHT22, MPU6050"
    assert Sensor.describe(0) == "ninguno"
    assert Sensor.describe(0x80) == "0x80"


def test_maximo_de_mediciones_cabe_en_una_trama():
    many = Telemetria(0, tuple(Measurement(i, float(i)) for i in range(MAX_MEASUREMENTS)))
    assert len(build(1, 1, 0, many)) <= 51
    with pytest.raises(FrameError):
        build(1, 1, 0, Telemetria(0, many.measurements + (Measurement(99, 0.0),)))


def test_tipo_desconocido():
    from edge_agent.lora.protocol import Frame, encode_frame

    with pytest.raises(FrameError, match="desconocido"):
        parse(encode_frame(Frame(1, 2, 0x7F, 0, b"")))


# --- Protocolo v2 (§11): los mismos vectores prueba el firmware en C++ ------------

CLAVE = bytes(range(16))
SUBIDA, BAJADA = Direccion.SUBIDA, Direccion.BAJADA

VECTORES_V2 = {
    "telemetria_v2": (
        "022a01020100000007d95d5574c1362408057c2687fd84fd84aa",
        SUBIDA,
        7,
        Telemetria(90, (Measurement(1, 23.5), Measurement(2, -4.25))),
    ),
    "estado_v2": (
        "022a010202000000082000fe13969694b8bb08f5caf3a1dfe0",
        SUBIDA,
        8,
        Estado(87, 0x0305, 10, 15, 1, 2, FlagsEstado.ERROR_SENSOR, 0x0B, 0x02),
    ),
    "ack_config_v2": (
        "022a0102030000000a3f1f08341d63ba",
        SUBIDA,
        10,
        AckConfig(0x0306, ResultadoConfig.OK),
    ),
    "config_v2": (
        "022a010204000000039486de94d9b2fe3f8b17",
        BAJADA,
        3,
        ConfigDownlink(0x0306, 5, 30),
    ),
}


@pytest.mark.parametrize("name", VECTORES_V2)
def test_vectores_v2_ida_y_vuelta(name):
    hexa, direccion, fcnt, message = VECTORES_V2[name]
    assert build_v2(CLAVE, direccion, 0x2A, 0x0102, fcnt, message).hex() == hexa

    frame, decoded = parse_v2(CLAVE, direccion, bytes.fromhex(hexa))
    assert (frame.net_id, frame.node_id, frame.fcnt) == (0x2A, 0x0102, fcnt)
    assert decoded == message


@pytest.mark.parametrize("pos", [0 + 1, 4, 5 + 3, 9, -1])  # net_id, msg_type, fcnt, payload, MIC
def test_v2_cualquier_byte_alterado_se_rechaza(pos):
    data = bytearray.fromhex(VECTORES_V2["telemetria_v2"][0])
    data[pos] ^= 0x01
    with pytest.raises(MicInvalido):
        parse_v2(CLAVE, SUBIDA, bytes(data))


def test_v2_clave_o_sentido_equivocado_se_rechaza():
    data = bytes.fromhex(VECTORES_V2["config_v2"][0])
    with pytest.raises(MicInvalido):
        parse_v2(bytes(16), BAJADA, data)
    with pytest.raises(MicInvalido):
        parse_v2(CLAVE, SUBIDA, data)  # un CONFIG reinyectado como subida


def test_v2_payload_cifrado():
    # Los valores medidos no aparecen en claro en la trama.
    data = bytes.fromhex(VECTORES_V2["telemetria_v2"][0])
    assert struct.pack(">f", 23.5) not in data


def test_v2_peek_lee_la_cabecera_sin_clave():
    frame = peek_v2(bytes.fromhex(VECTORES_V2["estado_v2"][0]))
    assert (frame.node_id, frame.msg_type, frame.fcnt) == (0x0102, MsgType.ESTADO, 8)


def test_v2_maximo_de_mediciones():
    many = Telemetria(0, tuple(Measurement(i, float(i)) for i in range(V2_MAX_MEASUREMENTS)))
    assert len(build_v2(CLAVE, SUBIDA, 1, 1, 0, many)) <= 51
    with pytest.raises(FrameError):
        build_v2(CLAVE, SUBIDA, 1, 1, 0, Telemetria(0, many.measurements + (Measurement(9, 0.0),)))


@pytest.mark.parametrize("raw", [bytes([2]) * 12, bytes([2]) * 52])
def test_v2_largo_invalido(raw):
    with pytest.raises(FrameError):
        peek_v2(raw)
