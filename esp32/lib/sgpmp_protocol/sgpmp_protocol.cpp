#include "sgpmp_protocol.h"

#include <string.h>

namespace sgpmp {

namespace {

void put_u16(uint8_t* p, uint16_t v) {
  p[0] = static_cast<uint8_t>(v >> 8);
  p[1] = static_cast<uint8_t>(v);
}

uint16_t get_u16(const uint8_t* p) {
  return static_cast<uint16_t>((p[0] << 8) | p[1]);
}

void put_f32(uint8_t* p, float value) {
  uint32_t bits;
  memcpy(&bits, &value, sizeof(bits));
  p[0] = static_cast<uint8_t>(bits >> 24);
  p[1] = static_cast<uint8_t>(bits >> 16);
  p[2] = static_cast<uint8_t>(bits >> 8);
  p[3] = static_cast<uint8_t>(bits);
}

size_t encode_frame(const Header& h, const uint8_t* payload, size_t payload_len,
                    uint8_t* out, size_t out_len) {
  const size_t total = HEADER_LEN + payload_len + CRC_LEN;
  if (payload_len > MAX_PAYLOAD_LEN || total > out_len) {
    return 0;
  }
  out[0] = PROTOCOL_VERSION;
  out[1] = h.net_id;
  put_u16(out + 2, h.node_id);
  out[4] = h.msg_type;
  out[5] = h.seq;
  if (payload_len > 0) {
    memcpy(out + HEADER_LEN, payload, payload_len);
  }
  put_u16(out + HEADER_LEN + payload_len, crc16_ccitt(out, HEADER_LEN + payload_len));
  return total;
}

Header with_type(const Header& h, MsgType type) {
  Header copy = h;
  copy.msg_type = type;
  return copy;
}

}  // namespace

uint16_t crc16_ccitt(const uint8_t* data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (int bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000) ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                           : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

size_t encode_telemetria(const Header& h, uint16_t age_s, const Measurement* ms,
                         size_t count, uint8_t* out, size_t out_len) {
  if (count > MAX_MEASUREMENTS) {
    return 0;
  }
  uint8_t payload[MAX_PAYLOAD_LEN];
  put_u16(payload, age_s);
  payload[2] = static_cast<uint8_t>(count);
  for (size_t i = 0; i < count; ++i) {
    payload[3 + 5 * i] = ms[i].code;
    put_f32(payload + 4 + 5 * i, ms[i].value);
  }
  return encode_frame(with_type(h, TELEMETRIA), payload, 3 + 5 * count, out, out_len);
}

size_t encode_estado(const Header& h, const Estado& e, uint8_t* out, size_t out_len) {
  uint8_t payload[10];
  payload[0] = e.bateria_pct;
  put_u16(payload + 1, e.cfg_version);
  put_u16(payload + 3, e.frecuencia_captura_min);
  put_u16(payload + 5, e.intervalo_transmision_min);
  payload[7] = e.fw_major;
  payload[8] = e.fw_minor;
  payload[9] = e.flags;
  return encode_frame(with_type(h, ESTADO), payload, sizeof(payload), out, out_len);
}

size_t encode_ack_config(const Header& h, const AckConfig& a, uint8_t* out, size_t out_len) {
  uint8_t payload[3];
  put_u16(payload, a.cfg_version);
  payload[2] = a.resultado;
  return encode_frame(with_type(h, ACK_CONFIG), payload, sizeof(payload), out, out_len);
}

size_t encode_config(const Header& h, const ConfigDownlink& c, uint8_t* out, size_t out_len) {
  uint8_t payload[6];
  put_u16(payload, c.cfg_version);
  put_u16(payload + 2, c.frecuencia_captura_min);
  put_u16(payload + 4, c.intervalo_transmision_min);
  return encode_frame(with_type(h, CONFIG), payload, sizeof(payload), out, out_len);
}

DecodeResult decode_frame(const uint8_t* frame, size_t len, Header& h,
                          const uint8_t** payload, size_t* payload_len) {
  if (len < HEADER_LEN + CRC_LEN || len > MAX_FRAME_LEN) {
    return DECODE_ERR_LEN;
  }
  if (crc16_ccitt(frame, len - CRC_LEN) != get_u16(frame + len - CRC_LEN)) {
    return DECODE_ERR_CRC;
  }
  if (frame[0] != PROTOCOL_VERSION) {
    return DECODE_ERR_VERSION;
  }
  h.net_id = frame[1];
  h.node_id = get_u16(frame + 2);
  h.msg_type = frame[4];
  h.seq = frame[5];
  *payload = frame + HEADER_LEN;
  *payload_len = len - HEADER_LEN - CRC_LEN;
  return DECODE_OK;
}

DecodeResult decode_config(const uint8_t* payload, size_t len, ConfigDownlink& out) {
  if (len != 6) {
    return DECODE_ERR_PAYLOAD;
  }
  out.cfg_version = get_u16(payload);
  out.frecuencia_captura_min = get_u16(payload + 2);
  out.intervalo_transmision_min = get_u16(payload + 4);
  return DECODE_OK;
}

}  // namespace sgpmp
