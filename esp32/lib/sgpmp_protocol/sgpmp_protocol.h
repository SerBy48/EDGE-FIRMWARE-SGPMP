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

#include "sgpmp_crypto.h"

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

// Bits de sensores_detectados / sensores_fallando del ESTADO.
enum SensorBit : uint8_t {
  SENSOR_BIT_DHT22 = 0x01,
  SENSOR_BIT_DS18B20 = 0x02,
  SENSOR_BIT_MPU6050 = 0x04,
  SENSOR_BIT_MAX30102 = 0x08,
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
  // Opcional: sin con_sensores se codifica el ESTADO de 10 B anterior.
  bool con_sensores;
  uint8_t sensores_detectados;  // bits de SensorBit
  uint8_t sensores_fallando;
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
  DECODE_ERR_MIC,  // v2: clave equivocada o trama alterada
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

// --- Payloads, comunes a v1 y v2 --------------------------------------------
// Devuelven el largo escrito, o 0 si no cabe en `out_len` o los datos son
// inválidos.
size_t telemetria_payload(uint16_t age_s, const Measurement* ms, size_t count, uint8_t* out,
                          size_t out_len);
size_t estado_payload(const Estado& e, uint8_t* out, size_t out_len);
size_t ack_config_payload(const AckConfig& a, uint8_t* out, size_t out_len);
size_t config_payload(const ConfigDownlink& c, uint8_t* out, size_t out_len);

// --- Protocolo v2: AES-128-CCM (docs/PROTOCOLO_LORA.md §11) -----------------
//
// Trama: ver(1)=2 net_id(1) node_id(2) msg_type(1) fcnt(4) payload(N) mic(4)
// La cabecera va en claro y autenticada; el payload va cifrado.
constexpr uint8_t PROTOCOL_VERSION_V2 = 2;
constexpr size_t V2_HEADER_LEN = 9;
constexpr size_t MIC_LEN = 4;
constexpr size_t V2_MAX_PAYLOAD_LEN = MAX_FRAME_LEN - V2_HEADER_LEN - MIC_LEN;
constexpr size_t V2_MAX_MEASUREMENTS = (V2_MAX_PAYLOAD_LEN - 3) / 5;

// Sentido de la trama: entra en el nonce, así subida y bajada nunca lo
// comparten aunque usen la misma clave.
enum Direccion : uint8_t {
  SUBIDA = 0x00,  // nodo -> Raspberry
  BAJADA = 0x01,  // Raspberry -> nodo
};

struct HeaderV2 {
  uint8_t net_id;
  uint16_t node_id;
  uint8_t msg_type;
  uint32_t fcnt;  // contador de tramas del emisor: nunca se repite con la misma clave
};

// Arma una trama v2. Devuelve su largo, o 0 si el payload no cabe.
size_t seal_v2(const Aes128& key, Direccion dir, const HeaderV2& h, const uint8_t* payload,
               size_t payload_len, uint8_t* out, size_t out_len);

// Lee la cabecera sin descifrar, para elegir la clave y descartar un `fcnt`
// viejo antes de gastar el descifrado.
DecodeResult peek_v2(const uint8_t* frame, size_t len, HeaderV2& h);

// Verifica el MIC y descifra el payload en `payload` (al menos
// V2_MAX_PAYLOAD_LEN bytes). Con un MIC inválido no entrega nada.
DecodeResult open_v2(const Aes128& key, Direccion dir, const uint8_t* frame, size_t len,
                     HeaderV2& h, uint8_t* payload, size_t* payload_len);

}  // namespace sgpmp
