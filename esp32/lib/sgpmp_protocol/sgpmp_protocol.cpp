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

void put_u32(uint8_t* p, uint32_t v) {
  put_u16(p, static_cast<uint16_t>(v >> 16));
  put_u16(p + 2, static_cast<uint16_t>(v));
}

uint32_t get_u32(const uint8_t* p) {
  return (static_cast<uint32_t>(get_u16(p)) << 16) | get_u16(p + 2);
}

// Nonce CCM de 13 bytes: dirección · net_id · node_id · fcnt · 5 bytes en 0.
constexpr size_t kNonceLen = 13;

void nonce_v2(Direccion dir, const HeaderV2& h, uint8_t out[kNonceLen]) {
  memset(out, 0, kNonceLen);
  out[0] = dir;
  out[1] = h.net_id;
  put_u16(out + 2, h.node_id);
  put_u32(out + 4, h.fcnt);
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

size_t telemetria_payload(uint16_t age_s, const Measurement* ms, size_t count, uint8_t* out,
                          size_t out_len) {
  const size_t len = 3 + 5 * count;
  if (count > MAX_MEASUREMENTS || len > out_len) {
    return 0;
  }
  put_u16(out, age_s);
  out[2] = static_cast<uint8_t>(count);
  for (size_t i = 0; i < count; ++i) {
    out[3 + 5 * i] = ms[i].code;
    put_f32(out + 4 + 5 * i, ms[i].value);
  }
  return len;
}

size_t estado_payload(const Estado& e, uint8_t* out, size_t out_len) {
  const size_t len = e.con_sensores ? 12 : 10;
  if (len > out_len) {
    return 0;
  }
  out[0] = e.bateria_pct;
  put_u16(out + 1, e.cfg_version);
  put_u16(out + 3, e.frecuencia_captura_min);
  put_u16(out + 5, e.intervalo_transmision_min);
  out[7] = e.fw_major;
  out[8] = e.fw_minor;
  out[9] = e.flags;
  if (e.con_sensores) {
    out[10] = e.sensores_detectados;
    out[11] = e.sensores_fallando;
  }
  return len;
}

size_t ack_config_payload(const AckConfig& a, uint8_t* out, size_t out_len) {
  if (out_len < 3) {
    return 0;
  }
  put_u16(out, a.cfg_version);
  out[2] = a.resultado;
  return 3;
}

size_t config_payload(const ConfigDownlink& c, uint8_t* out, size_t out_len) {
  if (out_len < 6) {
    return 0;
  }
  put_u16(out, c.cfg_version);
  put_u16(out + 2, c.frecuencia_captura_min);
  put_u16(out + 4, c.intervalo_transmision_min);
  return 6;
}

size_t encode_telemetria(const Header& h, uint16_t age_s, const Measurement* ms, size_t count,
                         uint8_t* out, size_t out_len) {
  uint8_t payload[MAX_PAYLOAD_LEN];
  const size_t len = telemetria_payload(age_s, ms, count, payload, sizeof(payload));
  return len ? encode_frame(with_type(h, TELEMETRIA), payload, len, out, out_len) : 0;
}

size_t encode_estado(const Header& h, const Estado& e, uint8_t* out, size_t out_len) {
  uint8_t payload[MAX_PAYLOAD_LEN];
  const size_t len = estado_payload(e, payload, sizeof(payload));
  return len ? encode_frame(with_type(h, ESTADO), payload, len, out, out_len) : 0;
}

size_t encode_ack_config(const Header& h, const AckConfig& a, uint8_t* out, size_t out_len) {
  uint8_t payload[MAX_PAYLOAD_LEN];
  const size_t len = ack_config_payload(a, payload, sizeof(payload));
  return len ? encode_frame(with_type(h, ACK_CONFIG), payload, len, out, out_len) : 0;
}

size_t encode_config(const Header& h, const ConfigDownlink& c, uint8_t* out, size_t out_len) {
  uint8_t payload[MAX_PAYLOAD_LEN];
  const size_t len = config_payload(c, payload, sizeof(payload));
  return len ? encode_frame(with_type(h, CONFIG), payload, len, out, out_len) : 0;
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

size_t seal_v2(const Aes128& key, Direccion dir, const HeaderV2& h, const uint8_t* payload,
               size_t payload_len, uint8_t* out, size_t out_len) {
  const size_t total = V2_HEADER_LEN + payload_len + MIC_LEN;
  if (payload_len > V2_MAX_PAYLOAD_LEN || total > out_len) {
    return 0;
  }
  out[0] = PROTOCOL_VERSION_V2;
  out[1] = h.net_id;
  put_u16(out + 2, h.node_id);
  out[4] = h.msg_type;
  put_u32(out + 5, h.fcnt);
  uint8_t nonce[kNonceLen];
  nonce_v2(dir, h, nonce);
  if (!ccm_encrypt(key, nonce, kNonceLen, out, V2_HEADER_LEN, payload, payload_len,
                   out + V2_HEADER_LEN, out + V2_HEADER_LEN + payload_len, MIC_LEN)) {
    return 0;
  }
  return total;
}

DecodeResult peek_v2(const uint8_t* frame, size_t len, HeaderV2& h) {
  if (len < V2_HEADER_LEN + MIC_LEN || len > MAX_FRAME_LEN) {
    return DECODE_ERR_LEN;
  }
  if (frame[0] != PROTOCOL_VERSION_V2) {
    return DECODE_ERR_VERSION;
  }
  h.net_id = frame[1];
  h.node_id = get_u16(frame + 2);
  h.msg_type = frame[4];
  h.fcnt = get_u32(frame + 5);
  return DECODE_OK;
}

DecodeResult open_v2(const Aes128& key, Direccion dir, const uint8_t* frame, size_t len,
                     HeaderV2& h, uint8_t* payload, size_t* payload_len) {
  const DecodeResult r = peek_v2(frame, len, h);
  if (r != DECODE_OK) {
    return r;
  }
  const size_t n = len - V2_HEADER_LEN - MIC_LEN;
  uint8_t nonce[kNonceLen];
  nonce_v2(dir, h, nonce);
  if (!ccm_decrypt(key, nonce, kNonceLen, frame, V2_HEADER_LEN, frame + V2_HEADER_LEN, n, payload,
                   frame + V2_HEADER_LEN + n, MIC_LEN)) {
    return DECODE_ERR_MIC;
  }
  *payload_len = n;
  return DECODE_OK;
}

}  // namespace sgpmp
