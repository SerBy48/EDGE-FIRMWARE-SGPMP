// Protocolo LoRa v1 SGPMP — especificación en docs/PROTOCOLO_LORA.md.
//
// Misma codificación que raspberry/edge_agent/lora/protocol.py; ambos lados
// se prueban contra los vectores de la sección 9 del documento. C++ puro,
// sin dependencias de Arduino, para poder probarlo en el PC (env native).
//
// Trama: ver(1) net_id(1) node_id(2) msg_type(1) seq(1) payload(N) crc16(2)
// Enteros big-endian; floats IEEE-754 de 32 bits big-endian.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace sgpmp {

constexpr uint8_t PROTOCOL_VERSION = 1;
constexpr size_t HEADER_LEN = 6;
constexpr size_t CRC_LEN = 2;
constexpr size_t MAX_FRAME_LEN = 51;
constexpr size_t MAX_PAYLOAD_LEN = MAX_FRAME_LEN - HEADER_LEN - CRC_LEN;
constexpr size_t MAX_MEASUREMENTS = (MAX_PAYLOAD_LEN - 3) / 5;
constexpr uint8_t BATERIA_DESCONOCIDA = 0xFF;

enum MsgType : uint8_t {
  TELEMETRIA = 0x01,  // ESP32 -> Raspberry
  ESTADO = 0x02,      // ESP32 -> Raspberry (último frame de cada ráfaga)
  ACK_CONFIG = 0x03,  // ESP32 -> Raspberry
  CONFIG = 0x04,      // Raspberry -> ESP32
};

enum Resultado : uint8_t {
  RESULTADO_OK = 0,
  RESULTADO_INVALIDA = 1,
};

enum FlagsEstado : uint8_t {
  FLAG_REINICIO = 0x01,
  FLAG_ERROR_SENSOR = 0x02,
};

struct Header {
  uint8_t net_id;
  uint16_t node_id;
  uint8_t msg_type;
  uint8_t seq;
};

struct Measurement {
  uint8_t code;
  float value;
};

struct Estado {
  uint8_t bateria_pct;  // BATERIA_DESCONOCIDA si no hay medición
  uint16_t cfg_version;
  uint16_t frecuencia_captura_min;
  uint16_t intervalo_transmision_min;
  uint8_t fw_major;
  uint8_t fw_minor;
  uint8_t flags;
};

struct AckConfig {
  uint16_t cfg_version;
  uint8_t resultado;
};

struct ConfigDownlink {
  uint16_t cfg_version;
  uint16_t frecuencia_captura_min;
  uint16_t intervalo_transmision_min;
};

enum DecodeResult {
  DECODE_OK = 0,
  DECODE_ERR_LEN,
  DECODE_ERR_CRC,
  DECODE_ERR_VERSION,
  DECODE_ERR_PAYLOAD,
};

// CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF). crc16("123456789") = 0x29B1.
uint16_t crc16_ccitt(const uint8_t* data, size_t len);

// Las funciones encode_* devuelven el largo total de la trama, o 0 si no cabe
// en `out_len` o los datos son inválidos.
size_t encode_telemetria(const Header& h, uint16_t age_s, const Measurement* ms,
                         size_t count, uint8_t* out, size_t out_len);
size_t encode_estado(const Header& h, const Estado& e, uint8_t* out, size_t out_len);
size_t encode_ack_config(const Header& h, const AckConfig& a, uint8_t* out, size_t out_len);
size_t encode_config(const Header& h, const ConfigDownlink& c, uint8_t* out, size_t out_len);

// Valida largo, CRC y versión. `payload` apunta dentro de `frame`.
DecodeResult decode_frame(const uint8_t* frame, size_t len, Header& h,
                          const uint8_t** payload, size_t* payload_len);
DecodeResult decode_config(const uint8_t* payload, size_t len, ConfigDownlink& out);

}  // namespace sgpmp
