"""Codificación de tramas LoRa v1 — especificación en docs/PROTOCOLO_LORA.md.

La misma codificación está en C++ en esp32/lib/sgpmp_protocol; ambos lados
se prueban contra los mismos vectores (sección 9 del documento). Cualquier
cambio acá debe replicarse allá y subir PROTOCOL_VERSION.

Trama: ver(1) net_id(1) node_id(2) msg_type(1) seq(1) payload(N) crc16(2)
Todos los enteros en big-endian; floats IEEE-754 de 32 bits big-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

PROTOCOL_VERSION = 1
HEADER_LEN = 6
CRC_LEN = 2
# 51 B cabe en SF7–SF9/125 kHz dentro de 400 ms de tiempo en aire.
MAX_FRAME_LEN = 51
MAX_PAYLOAD_LEN = MAX_FRAME_LEN - HEADER_LEN - CRC_LEN
MAX_MEASUREMENTS = (MAX_PAYLOAD_LEN - 3) // 5
BATERIA_DESCONOCIDA = 0xFF
GATEWAY_NODE_ID = 0x0000


class MsgType(IntEnum):
    TELEMETRIA = 0x01  # ESP32 → Raspberry
    ESTADO = 0x02  # ESP32 → Raspberry (último frame de cada ráfaga)
    ACK_CONFIG = 0x03  # ESP32 → Raspberry
    CONFIG = 0x04  # Raspberry → ESP32 (en la ventana RX tras un ESTADO)


class ResultadoConfig(IntEnum):
    OK = 0
    INVALIDA = 1


class FlagsEstado:
    REINICIO = 0x01  # primer ESTADO tras encendido/reset (no tras deep sleep)
    ERROR_SENSOR = 0x02  # alguna lectura falló en el ciclo


class FrameError(ValueError):
    """Trama inválida: se descarta sin afectar al resto del gateway."""


@dataclass(frozen=True)
class Frame:
    net_id: int
    node_id: int
    msg_type: int
    seq: int
    payload: bytes


@dataclass(frozen=True)
class Measurement:
    code: int
    value: float


@dataclass(frozen=True)
class Telemetria:
    age_s: int  # segundos entre la captura y la transmisión de esta trama
    measurements: tuple[Measurement, ...]


@dataclass(frozen=True)
class Estado:
    bateria_pct: int | None
    cfg_version: int
    frecuencia_captura_min: int
    intervalo_transmision_min: int
    fw_major: int
    fw_minor: int
    flags: int


@dataclass(frozen=True)
class AckConfig:
    cfg_version: int
    resultado: ResultadoConfig


@dataclass(frozen=True)
class ConfigDownlink:
    cfg_version: int
    frecuencia_captura_min: int
    intervalo_transmision_min: int


Message = Telemetria | Estado | AckConfig | ConfigDownlink


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, sin reflexión ni xorout."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def encode_frame(frame: Frame) -> bytes:
    if len(frame.payload) > MAX_PAYLOAD_LEN:
        raise FrameError(f"payload de {len(frame.payload)} B excede {MAX_PAYLOAD_LEN} B")
    body = (
        struct.pack(
            ">BBHBB",
            PROTOCOL_VERSION,
            frame.net_id,
            frame.node_id,
            frame.msg_type,
            frame.seq & 0xFF,
        )
        + frame.payload
    )
    return body + struct.pack(">H", crc16_ccitt(body))


def decode_frame(data: bytes) -> Frame:
    if len(data) < HEADER_LEN + CRC_LEN:
        raise FrameError(f"trama corta ({len(data)} B)")
    if len(data) > MAX_FRAME_LEN:
        raise FrameError(f"trama larga ({len(data)} B)")
    body, (crc,) = data[:-CRC_LEN], struct.unpack(">H", data[-CRC_LEN:])
    if crc16_ccitt(body) != crc:
        raise FrameError("CRC inválido")
    version, net_id, node_id, msg_type, seq = struct.unpack(">BBHBB", body[:HEADER_LEN])
    if version != PROTOCOL_VERSION:
        raise FrameError(f"versión de protocolo no soportada: {version}")
    return Frame(net_id, node_id, msg_type, seq, bytes(body[HEADER_LEN:]))


def encode_message(message: Message) -> tuple[MsgType, bytes]:
    match message:
        case Telemetria(age_s=age, measurements=ms):
            if len(ms) > MAX_MEASUREMENTS:
                raise FrameError(f"máximo {MAX_MEASUREMENTS} mediciones por trama")
            payload = struct.pack(">HB", min(age, 0xFFFF), len(ms))
            payload += b"".join(struct.pack(">Bf", m.code, m.value) for m in ms)
            return MsgType.TELEMETRIA, payload
        case Estado():
            bateria = BATERIA_DESCONOCIDA if message.bateria_pct is None else message.bateria_pct
            return MsgType.ESTADO, struct.pack(
                ">BHHHBBB",
                bateria,
                message.cfg_version,
                message.frecuencia_captura_min,
                message.intervalo_transmision_min,
                message.fw_major,
                message.fw_minor,
                message.flags,
            )
        case AckConfig():
            return MsgType.ACK_CONFIG, struct.pack(">HB", message.cfg_version, message.resultado)
        case ConfigDownlink():
            return MsgType.CONFIG, struct.pack(
                ">HHH",
                message.cfg_version,
                message.frecuencia_captura_min,
                message.intervalo_transmision_min,
            )
    raise FrameError(f"mensaje desconocido: {message!r}")


def decode_message(msg_type: int, payload: bytes) -> Message:
    try:
        match msg_type:
            case MsgType.TELEMETRIA:
                age, count = struct.unpack(">HB", payload[:3])
                if len(payload) != 3 + 5 * count:
                    raise FrameError("largo de telemetría no coincide con la cantidad")
                ms = tuple(
                    Measurement(*struct.unpack(">Bf", payload[3 + 5 * i : 8 + 5 * i]))
                    for i in range(count)
                )
                return Telemetria(age, ms)
            case MsgType.ESTADO:
                bat, ver, frec, interv, major, minor, flags = struct.unpack(">BHHHBBB", payload)
                return Estado(
                    None if bat == BATERIA_DESCONOCIDA else bat,
                    ver,
                    frec,
                    interv,
                    major,
                    minor,
                    flags,
                )
            case MsgType.ACK_CONFIG:
                ver, resultado = struct.unpack(">HB", payload)
                return AckConfig(ver, ResultadoConfig(resultado))
            case MsgType.CONFIG:
                return ConfigDownlink(*struct.unpack(">HHH", payload))
    except (struct.error, ValueError) as exc:
        if isinstance(exc, FrameError):
            raise
        raise FrameError(f"payload inválido para tipo 0x{msg_type:02x}: {exc}") from exc
    raise FrameError(f"tipo de mensaje desconocido: 0x{msg_type:02x}")


def build(net_id: int, node_id: int, seq: int, message: Message) -> bytes:
    msg_type, payload = encode_message(message)
    return encode_frame(Frame(net_id, node_id, msg_type, seq, payload))


def parse(data: bytes) -> tuple[Frame, Message]:
    frame = decode_frame(data)
    return frame, decode_message(frame.msg_type, frame.payload)
