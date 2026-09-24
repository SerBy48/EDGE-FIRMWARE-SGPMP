// Vectores de docs/PROTOCOLO_LORA.md §9 — los mismos que
// raspberry/tests/test_lora_protocol.py. Correr con: pio test -e native
#include <string.h>
#include <unity.h>

#include "sgpmp_protocol.h"

using namespace sgpmp;

namespace {

size_t from_hex(const char* hex, uint8_t* out) {
  size_t n = strlen(hex) / 2;
  for (size_t i = 0; i < n; ++i) {
    auto nib = [](char c) -> uint8_t {
      return (c >= '0' && c <= '9') ? c - '0' : c - 'a' + 10;
    };
    out[i] = static_cast<uint8_t>((nib(hex[2 * i]) << 4) | nib(hex[2 * i + 1]));
  }
  return n;
}

void assert_frame(const char* expected_hex, const uint8_t* frame, size_t len) {
  uint8_t expected[MAX_FRAME_LEN];
  size_t n = from_hex(expected_hex, expected);
  TEST_ASSERT_EQUAL_UINT(n, len);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, frame, n);
}

Header header(uint8_t seq) { return Header{0x2A, 0x0102, 0, seq}; }

}  // namespace

void setUp() {}
void tearDown() {}

void test_crc_referencia() {
  const uint8_t data[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  TEST_ASSERT_EQUAL_HEX16(0x29B1, crc16_ccitt(data, sizeof(data)));
}

void test_telemetria() {
  Measurement ms[] = {{1, 23.5f}, {2, -4.25f}};
  uint8_t out[MAX_FRAME_LEN];
  size_t len = encode_telemetria(header(7), 90, ms, 2, out, sizeof(out));
  assert_frame("012a01020107005a020141bc000002c08800005494", out, len);
}

void test_estado() {
  Estado e{87, 0x0305, 10, 15, 1, 2, FLAG_REINICIO};
  uint8_t out[MAX_FRAME_LEN];
  size_t len = encode_estado(header(8), e, out, sizeof(out));
  assert_frame("012a01020208570305000a000f010201ec76", out, len);
}

void test_estado_sin_bateria() {
  Estado e{BATERIA_DESCONOCIDA, 0, 10, 15, 0, 1, 0};
  uint8_t out[MAX_FRAME_LEN];
  size_t len = encode_estado(header(9), e, out, sizeof(out));
  assert_frame("012a01020209ff0000000a000f000100b4f5", out, len);
}

void test_ack_config() {
  uint8_t out[MAX_FRAME_LEN];
  size_t len = encode_ack_config(header(10), AckConfig{0x0306, RESULTADO_OK}, out, sizeof(out));
  assert_frame("012a0102030a0306005feb", out, len);
}

void test_decode_config() {
  uint8_t frame[MAX_FRAME_LEN];
  size_t len = from_hex("012a0102040303060005001e4a6c", frame);
  Header h;
  const uint8_t* payload;
  size_t payload_len;
  TEST_ASSERT_EQUAL(DECODE_OK, decode_frame(frame, len, h, &payload, &payload_len));
  TEST_ASSERT_EQUAL_HEX8(0x2A, h.net_id);
  TEST_ASSERT_EQUAL_HEX16(0x0102, h.node_id);
  TEST_ASSERT_EQUAL(CONFIG, h.msg_type);
  TEST_ASSERT_EQUAL(3, h.seq);

  ConfigDownlink c;
  TEST_ASSERT_EQUAL(DECODE_OK, decode_config(payload, payload_len, c));
  TEST_ASSERT_EQUAL_HEX16(0x0306, c.cfg_version);
  TEST_ASSERT_EQUAL(5, c.frecuencia_captura_min);
  TEST_ASSERT_EQUAL(30, c.intervalo_transmision_min);

  // Y el encoder produce la misma trama.
  uint8_t out[MAX_FRAME_LEN];
  size_t out_len = encode_config(header(3), c, out, sizeof(out));
  assert_frame("012a0102040303060005001e4a6c", out, out_len);
}

void test_crc_corrupto() {
  uint8_t frame[MAX_FRAME_LEN];
  size_t len = from_hex("012a0102040303060005001e4a6c", frame);
  frame[7] ^= 0x01;
  Header h;
  const uint8_t* payload;
  size_t payload_len;
  TEST_ASSERT_EQUAL(DECODE_ERR_CRC, decode_frame(frame, len, h, &payload, &payload_len));
}

void test_limites() {
  Measurement ms[MAX_MEASUREMENTS + 1] = {};
  uint8_t out[MAX_FRAME_LEN];
  TEST_ASSERT_TRUE(encode_telemetria(header(0), 0, ms, MAX_MEASUREMENTS, out, sizeof(out)) <= 51);
  TEST_ASSERT_EQUAL(0, encode_telemetria(header(0), 0, ms, MAX_MEASUREMENTS + 1, out, sizeof(out)));
  TEST_ASSERT_EQUAL(0, encode_estado(header(0), Estado{}, out, 10));  // buffer chico
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_crc_referencia);
  RUN_TEST(test_telemetria);
  RUN_TEST(test_estado);
  RUN_TEST(test_estado_sin_bateria);
  RUN_TEST(test_ack_config);
  RUN_TEST(test_decode_config);
  RUN_TEST(test_crc_corrupto);
  RUN_TEST(test_limites);
  return UNITY_END();
}
