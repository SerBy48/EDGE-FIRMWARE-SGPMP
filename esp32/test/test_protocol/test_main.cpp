// Vectores de docs/PROTOCOLO_LORA.md §9 y §11 — los mismos que
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

void test_estado_sensores() {
  Estado e{87,    0x0305, 10, 15, 1, 2, FLAG_ERROR_SENSOR,
           true,  SENSOR_BIT_DHT22 | SENSOR_BIT_DS18B20 | SENSOR_BIT_MAX30102,
           SENSOR_BIT_DS18B20};
  uint8_t out[MAX_FRAME_LEN];
  size_t len = encode_estado(header(11), e, out, sizeof(out));
  assert_frame("012a0102020b570305000a000f0102020b0221c3", out, len);
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

// --- Protocolo v2 (§11): AES-CCM y los mismos vectores que Python ---------------

const uint8_t kClave[16] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};

HeaderV2 header_v2(MsgType type, uint32_t fcnt) { return HeaderV2{0x2A, 0x0102, type, fcnt}; }

void test_aes_fips197() {
  // FIPS-197, apéndice C.1.
  uint8_t in[16], expected[16], out[16];
  from_hex("00112233445566778899aabbccddeeff", in);
  from_hex("69c4e0d86a7b0430d8cdb78070b4c55a", expected);
  Aes128(kClave).encrypt_block(in, out);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, out, 16);
}

void check_ccm(const char* key_hex, const char* nonce_hex, const char* aad_hex,
               const char* plain_hex, const char* expected_hex, size_t tag_len) {
  uint8_t key[16], nonce[13], aad[32], plain[32], expected[48], out[32], tag[16], back[32];
  from_hex(key_hex, key);
  const size_t nonce_len = from_hex(nonce_hex, nonce);
  const size_t aad_len = from_hex(aad_hex, aad);
  const size_t len = from_hex(plain_hex, plain);
  from_hex(expected_hex, expected);
  const Aes128 aes(key);
  TEST_ASSERT_TRUE(ccm_encrypt(aes, nonce, nonce_len, aad, aad_len, plain, len, out, tag, tag_len));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, out, len);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected + len, tag, tag_len);
  TEST_ASSERT_TRUE(ccm_decrypt(aes, nonce, nonce_len, aad, aad_len, out, len, back, tag, tag_len));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(plain, back, len);
  tag[0] ^= 0x01;
  TEST_ASSERT_FALSE(ccm_decrypt(aes, nonce, nonce_len, aad, aad_len, out, len, back, tag, tag_len));
}

void test_ccm_nist_ejemplo_1() {
  // NIST SP 800-38C, apéndice C, ejemplo 1 (etiqueta de 4 B, como el MIC v2).
  check_ccm("404142434445464748494a4b4c4d4e4f", "10111213141516", "0001020304050607", "20212223",
            "7162015b4dac255d", 4);
}

void test_ccm_nist_ejemplo_2() {
  check_ccm("404142434445464748494a4b4c4d4e4f", "1011121314151617",
            "000102030405060708090a0b0c0d0e0f", "202122232425262728292a2b2c2d2e2f",
            "d2a1f0e051ea5f62081a7792073d593d1fc64fbfaccd", 6);
}

void test_key_check_value() {
  // La huella que muestran el nodo y la Raspberry (keys.huella en Python).
  TEST_ASSERT_EQUAL_HEX32(0xc6a13b37, key_check_value(kClave));
}

void check_v2(const char* expected_hex, Direccion dir, const HeaderV2& h, const uint8_t* payload,
              size_t payload_len) {
  const Aes128 aes(kClave);
  uint8_t out[MAX_FRAME_LEN];
  const size_t len = seal_v2(aes, dir, h, payload, payload_len, out, sizeof(out));
  assert_frame(expected_hex, out, len);

  HeaderV2 back;
  uint8_t plain[V2_MAX_PAYLOAD_LEN];
  size_t plain_len;
  TEST_ASSERT_EQUAL(DECODE_OK, open_v2(aes, dir, out, len, back, plain, &plain_len));
  TEST_ASSERT_EQUAL_UINT32(h.fcnt, back.fcnt);
  TEST_ASSERT_EQUAL_UINT8(h.msg_type, back.msg_type);
  TEST_ASSERT_EQUAL_UINT(payload_len, plain_len);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(payload, plain, payload_len);
}

void test_telemetria_v2() {
  Measurement ms[] = {{1, 23.5f}, {2, -4.25f}};
  uint8_t payload[V2_MAX_PAYLOAD_LEN];
  const size_t n = telemetria_payload(90, ms, 2, payload, sizeof(payload));
  check_v2("022a01020100000007d95d5574c1362408057c2687fd84fd84aa", SUBIDA,
           header_v2(TELEMETRIA, 7), payload, n);
}

void test_estado_v2() {
  const Estado e{87,   0x0305, 10, 15, 1, 2, FLAG_ERROR_SENSOR,
                 true, 0x0B,   0x02};
  uint8_t payload[V2_MAX_PAYLOAD_LEN];
  const size_t n = estado_payload(e, payload, sizeof(payload));
  check_v2("022a010202000000082000fe13969694b8bb08f5caf3a1dfe0", SUBIDA, header_v2(ESTADO, 8),
           payload, n);
}

void test_ack_config_v2() {
  uint8_t payload[V2_MAX_PAYLOAD_LEN];
  const size_t n = ack_config_payload(AckConfig{0x0306, RESULTADO_OK}, payload, sizeof(payload));
  check_v2("022a0102030000000a3f1f08341d63ba", SUBIDA, header_v2(ACK_CONFIG, 10), payload, n);
}

void test_config_v2_desde_la_raspberry() {
  // El nodo abre el CONFIG que sella la Raspberry (Python) y lee sus valores.
  uint8_t frame[MAX_FRAME_LEN];
  const size_t len = from_hex("022a010204000000039486de94d9b2fe3f8b17", frame);
  HeaderV2 h;
  uint8_t payload[V2_MAX_PAYLOAD_LEN];
  size_t payload_len;
  TEST_ASSERT_EQUAL(DECODE_OK, open_v2(Aes128(kClave), BAJADA, frame, len, h, payload,
                                       &payload_len));
  TEST_ASSERT_EQUAL_UINT32(3, h.fcnt);
  ConfigDownlink c;
  TEST_ASSERT_EQUAL(DECODE_OK, decode_config(payload, payload_len, c));
  TEST_ASSERT_EQUAL_UINT16(0x0306, c.cfg_version);
  TEST_ASSERT_EQUAL_UINT16(5, c.frecuencia_captura_min);
  TEST_ASSERT_EQUAL_UINT16(30, c.intervalo_transmision_min);
}

void test_v2_rechazos() {
  uint8_t frame[MAX_FRAME_LEN];
  const size_t len = from_hex("022a010204000000039486de94d9b2fe3f8b17", frame);
  HeaderV2 h;
  uint8_t payload[V2_MAX_PAYLOAD_LEN];
  size_t payload_len;
  const uint8_t otra[16] = {};
  // Otra clave, el sentido equivocado, o cualquier byte alterado: MIC inválido.
  TEST_ASSERT_EQUAL(DECODE_ERR_MIC, open_v2(Aes128(otra), BAJADA, frame, len, h, payload,
                                            &payload_len));
  TEST_ASSERT_EQUAL(DECODE_ERR_MIC, open_v2(Aes128(kClave), SUBIDA, frame, len, h, payload,
                                            &payload_len));
  const size_t alterados[] = {1, 5, 10, len - 1};  // net_id, fcnt, payload, MIC
  for (size_t i : alterados) {
    frame[i] ^= 0x01;
    TEST_ASSERT_EQUAL(DECODE_ERR_MIC, open_v2(Aes128(kClave), BAJADA, frame, len, h, payload,
                                              &payload_len));
    frame[i] ^= 0x01;
  }
  TEST_ASSERT_EQUAL(DECODE_ERR_LEN, peek_v2(frame, 12, h));
  frame[0] = 1;
  TEST_ASSERT_EQUAL(DECODE_ERR_VERSION, peek_v2(frame, len, h));
}

void test_limites_v2() {
  Measurement ms[V2_MAX_MEASUREMENTS + 1] = {};
  uint8_t payload[V2_MAX_PAYLOAD_LEN], out[MAX_FRAME_LEN];
  const size_t n = telemetria_payload(0, ms, V2_MAX_MEASUREMENTS, payload, sizeof(payload));
  TEST_ASSERT_EQUAL(V2_MAX_PAYLOAD_LEN, n);
  TEST_ASSERT_EQUAL(MAX_FRAME_LEN, seal_v2(Aes128(kClave), SUBIDA, header_v2(TELEMETRIA, 0),
                                           payload, n, out, sizeof(out)));
  TEST_ASSERT_EQUAL(0, telemetria_payload(0, ms, V2_MAX_MEASUREMENTS + 1, payload,
                                          sizeof(payload)));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_crc_referencia);
  RUN_TEST(test_telemetria);
  RUN_TEST(test_estado);
  RUN_TEST(test_estado_sin_bateria);
  RUN_TEST(test_estado_sensores);
  RUN_TEST(test_ack_config);
  RUN_TEST(test_decode_config);
  RUN_TEST(test_crc_corrupto);
  RUN_TEST(test_limites);
  RUN_TEST(test_aes_fips197);
  RUN_TEST(test_ccm_nist_ejemplo_1);
  RUN_TEST(test_ccm_nist_ejemplo_2);
  RUN_TEST(test_key_check_value);
  RUN_TEST(test_telemetria_v2);
  RUN_TEST(test_estado_v2);
  RUN_TEST(test_ack_config_v2);
  RUN_TEST(test_config_v2_desde_la_raspberry);
  RUN_TEST(test_v2_rechazos);
  RUN_TEST(test_limites_v2);
  return UNITY_END();
}
