// Nodo sensor SGPMP: ESP8266 o ESP32 + SX1276, a batería (Fase 1).
//
// Todo el trabajo ocurre en setup(): el nodo despierta del deep sleep cada
// frecuencia_captura, captura, y cada intervalo_transmision transmite una
// ráfaga (TELEMETRIA por captura + ESTADO), abre la ventana RX para un
// posible CONFIG y vuelve a dormir. Ver docs/PROTOCOLO_LORA.md §6.
//
// Nunca habla MQTT: solo LoRa con la Raspberry del sitio, con el protocolo v2
// (§11): cada trama va cifrada y autenticada con la clave de este nodo.
#include <Arduino.h>
#include <LoRa.h>
#include <SPI.h>

#include "battery.h"
#include "config.h"
#include "node_config.h"
#include "platform.h"
#include "sensors.h"
#include "sgpmp_protocol.h"

namespace {

constexpr uint32_t kRtcMagic = 0x53475034;  // "SGP4": cambia si cambia RtcState

struct Sample {
  uint32_t captured_s;
  SensorReading reading;
};

// Sobrevive al deep sleep (no a un corte de energía).
struct RtcState {
  uint32_t magic;
  // Reloj del nodo en ms desde el encendido. millis() vuelve a 0 en cada
  // despertar del ESP8266, así que se acumula aquí el tiempo dormido. No es
  // hora real (el nodo no la necesita, ver `age_s`).
  uint64_t clock_ms;
  // Resto de un deep sleep más largo que platform_max_sleep_ms().
  uint64_t sleep_pending_ms;
  uint32_t last_tx_s;
  // Contadores v2: fcnt de la próxima trama, fin del bloque reservado en
  // flash y fcnt del último CONFIG aceptado (ver node_config.h).
  uint32_t fcnt_subida;
  uint32_t fcnt_limite;
  uint32_t fcnt_bajada;
  uint8_t n_samples;
  bool reboot_pending;
  Sample samples[MAX_SAMPLES];
};
static_assert(sizeof(RtcState) <= PLATFORM_RTC_MAX_LEN, "bajar MAX_SAMPLES");

RtcState rtc;
uint8_t key_bytes[sgpmp::AES_KEY_LEN];
bool key_ok = false;

uint32_t uptime_s() { return static_cast<uint32_t>((rtc.clock_ms + millis()) / 1000); }

int hex_value(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// LORA_KEY_HEX: 32 caracteres hexadecimales (secrets.ini de lora-key).
bool parse_key(const char* hex, uint8_t out[sgpmp::AES_KEY_LEN]) {
  if (strlen(hex) != 2 * sgpmp::AES_KEY_LEN) {
    return false;
  }
  for (size_t i = 0; i < sgpmp::AES_KEY_LEN; ++i) {
    const int hi = hex_value(hex[2 * i]), lo = hex_value(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) {
      return false;
    }
    out[i] = static_cast<uint8_t>((hi << 4) | lo);
  }
  return true;
}

// Reserva bloques de FCNT_BLOQUE en flash: tras un corte de energía el nodo
// arranca desde el bloque siguiente y nunca repite un nonce con su clave.
uint32_t next_fcnt() {
  if (rtc.fcnt_subida >= rtc.fcnt_limite) {
    rtc.fcnt_limite = rtc.fcnt_subida + FCNT_BLOQUE;
    NodeCounters counters = node_counters_load();
    counters.fcnt_reservado = rtc.fcnt_limite;
    node_counters_save(counters);
  }
  return rtc.fcnt_subida++;
}

// LoRa.begin() solo devuelve false: se relee RegVersion para que el log
// diga qué revisar, igual que raspberry/scripts/lora_check.py.
constexpr uint8_t kRegOpMode = 0x01;
constexpr uint8_t kRegModemConfig1 = 0x1D;
constexpr uint8_t kRegModemConfig2 = 0x1E;
constexpr uint8_t kRegVersion = 0x42;

uint8_t radio_read(uint8_t reg) {
  SPI.beginTransaction(SPISettings(LORA_SPI_HZ, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_LORA_SS, LOW);
  SPI.transfer(reg & 0x7F);
  const uint8_t value = SPI.transfer(0x00);
  digitalWrite(PIN_LORA_SS, HIGH);
  SPI.endTransaction();
  return value;
}

// La librería divide por el ancho de banda que lee del chip (setLdoFlag): si
// el SPI devuelve basura, divide por cero y el ESP8266 se reinicia. Antes de
// usarla se comprueba que el chip esté en modo LoRa con BW (0-9) y SF (6-12)
// válidos.
bool radio_registers_ok(const char* when) {
  const uint8_t op = radio_read(kRegOpMode);
  const uint8_t mc1 = radio_read(kRegModemConfig1);
  const uint8_t mc2 = radio_read(kRegModemConfig2);
  const uint8_t bw = mc1 >> 4, sf = mc2 >> 4;
  if ((op & 0x80) && bw <= 9 && sf >= 6 && sf <= 12) {
    return true;
  }
  Serial.printf(
      "[lora] el SX1276 responde pero sus registros no son coherentes %s (OpMode=0x%02x "
      "ModemConfig1=0x%02x ModemConfig2=0x%02x): cables SPI flojos o alimentación inestable\n",
      when, op, mc1, mc2);
  return false;
}

void report_radio_failure() {
  const uint8_t version = radio_read(kRegVersion);
  const char* hint = version == 0x00   ? "sin alimentación (VCC 3.3 V y GND), MISO o NSS mal cableados"
                     : version == 0xFF ? "MISO al aire o sin alimentación (3.3 V y GND)"
                                       : "SCK/MOSI cruzados o cables flojos";
  Serial.printf("[lora] el SX1276 no responde (RegVersion=0x%02x, esperado 0x12): %s\n", version,
                hint);
}

bool radio_begin() {
  platform_spi_begin();
  LoRa.setSPI(SPI);
  LoRa.setSPIFrequency(LORA_SPI_HZ);
  LoRa.setPins(PIN_LORA_SS, PIN_LORA_RST, PIN_LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ_HZ)) {
    report_radio_failure();
    return false;
  }
  if (!radio_registers_ok("al iniciarlo")) {
    return false;
  }
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW_HZ);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setSyncWord(LORA_SYNC_WORD);
  LoRa.setPreambleLength(8);
  LoRa.enableCrc();
  LoRa.setTxPower(LORA_TX_POWER_DBM, PA_OUTPUT_PA_BOOST_PIN);
  // Si la escritura no quedó (SPI inestable), la trama saldría con otros
  // parámetros y la Raspberry no la oiría.
  if (!radio_registers_ok("tras configurarlo") ||
      (radio_read(kRegModemConfig2) >> 4) != LORA_SF) {
    Serial.println("[lora] la configuración no quedó escrita en el SX1276: revisar cables SPI");
    return false;
  }
  return true;
}

void send(const sgpmp::Aes128& aes, sgpmp::MsgType type, const uint8_t* payload, size_t len) {
  uint8_t frame[sgpmp::MAX_FRAME_LEN];
  const sgpmp::HeaderV2 h{NET_ID, NODE_ID, type, next_fcnt()};
  const size_t n =
      len > 0 ? sgpmp::seal_v2(aes, sgpmp::SUBIDA, h, payload, len, frame, sizeof(frame)) : 0;
  if (n == 0) {
    Serial.println("[lora] trama inválida, no se envía");
    return;
  }
  LoRa.beginPacket();
  LoRa.write(frame, n);
  LoRa.endPacket();  // bloquea hasta TxDone
}

// Una captura en una o más TELEMETRIA con el mismo `age_s`: con todo el
// catálogo conectado hay más variables de las que caben en una trama.
void send_reading(const sgpmp::Aes128& aes, const SensorReading& r, uint16_t age_s) {
  sgpmp::Measurement ms[sgpmp::V2_MAX_MEASUREMENTS];
  uint8_t payload[sgpmp::V2_MAX_PAYLOAD_LEN];
  size_t count = 0;
  for (uint8_t v = 0; v < VAR_COUNT; ++v) {
    const SensorVar var = static_cast<SensorVar>(v);
    if (!r.has(var)) {
      continue;
    }
    ms[count++] = {sensor_var_code(var), r.values[v]};
    if (count == sgpmp::V2_MAX_MEASUREMENTS) {
      send(aes, sgpmp::TELEMETRIA, payload,
           sgpmp::telemetria_payload(age_s, ms, count, payload, sizeof(payload)));
      count = 0;
    }
  }
  if (count > 0) {
    send(aes, sgpmp::TELEMETRIA, payload,
         sgpmp::telemetria_payload(age_s, ms, count, payload, sizeof(payload)));
  }
}

// Espera un CONFIG dirigido a este nodo durante la ventana RX. Solo acepta
// uno autenticado con su clave y más nuevo que el último aplicado.
bool receive_config(const sgpmp::Aes128& aes, sgpmp::ConfigDownlink& out, uint32_t& fcnt,
                    uint32_t window_ms) {
  const uint32_t deadline = millis() + window_ms;
  uint8_t buf[sgpmp::MAX_FRAME_LEN];
  while (static_cast<int32_t>(deadline - millis()) > 0) {
    int size = LoRa.parsePacket();
    if (size <= 0) {
      yield();  // el watchdog del ESP8266 reinicia si el loop no lo alimenta
      continue;
    }
    size_t len = 0;
    while (LoRa.available() && len < sizeof(buf)) {
      buf[len++] = static_cast<uint8_t>(LoRa.read());
    }
    sgpmp::HeaderV2 h;
    if (sgpmp::peek_v2(buf, len, h) != sgpmp::DECODE_OK) {
      continue;
    }
    if (h.net_id != NET_ID || h.node_id != NODE_ID || h.msg_type != sgpmp::CONFIG) {
      continue;  // tráfico de otro nodo o de otra red
    }
    if (h.fcnt <= rtc.fcnt_bajada) {
      Serial.printf("[lora] CONFIG repetido o viejo (fcnt %lu): se ignora\n",
                    static_cast<unsigned long>(h.fcnt));
      continue;
    }
    uint8_t payload[sgpmp::V2_MAX_PAYLOAD_LEN];
    size_t payload_len;
    if (sgpmp::open_v2(aes, sgpmp::BAJADA, buf, len, h, payload, &payload_len) !=
        sgpmp::DECODE_OK) {
      Serial.println("[lora] CONFIG con MIC inválido: se ignora");
      continue;
    }
    if (sgpmp::decode_config(payload, payload_len, out) == sgpmp::DECODE_OK) {
      fcnt = h.fcnt;
      return true;
    }
  }
  return false;
}

void capture(uint32_t now) {
  if (rtc.n_samples >= MAX_SAMPLES) {
    // No debería pasar (se transmite al llenarse); si la radio falló varias
    // veces seguidas, se descarta la captura más vieja.
    memmove(&rtc.samples[0], &rtc.samples[1], sizeof(Sample) * (MAX_SAMPLES - 1));
    rtc.n_samples = MAX_SAMPLES - 1;
  }
  Sample& s = rtc.samples[rtc.n_samples++];
  s.captured_s = now;
  sensors_read(s.reading);
}

void transmit_burst(NodeConfig& cfg, uint32_t now) {
  if (!key_ok) {
    Serial.println("[lora] sin clave (falta esp32/secrets.ini al compilar): no se transmite. "
                   "Generarlo en la Raspberry con lora-key <serial> y volver a flashear");
    return;
  }
  if (!radio_begin()) {
    // Las capturas se conservan y se reintentan en el próximo ciclo.
    return;
  }

  const sgpmp::Aes128 aes(key_bytes);
  uint8_t payload[sgpmp::V2_MAX_PAYLOAD_LEN];
  uint8_t flags = rtc.reboot_pending ? sgpmp::FLAG_REINICIO : 0;
  for (uint8_t i = 0; i < rtc.n_samples; ++i) {
    const Sample& s = rtc.samples[i];
    if (s.reading.failing) {
      flags |= sgpmp::FLAG_ERROR_SENSOR;
    }
    const uint32_t age = now - s.captured_s;
    send_reading(aes, s.reading, age > 0xFFFF ? 0xFFFF : age);
  }

  // Los sensores del ESTADO son los de la captura más reciente: el estado actual.
  const SensorReading& last = rtc.samples[rtc.n_samples - 1].reading;
  const sgpmp::Estado estado{battery_percent(),
                             cfg.cfg_version,
                             cfg.frecuencia_captura_min,
                             cfg.intervalo_transmision_min,
                             FW_MAJOR,
                             FW_MINOR,
                             flags,
                             true,
                             last.detected,
                             last.failing};
  send(aes, sgpmp::ESTADO, payload, sgpmp::estado_payload(estado, payload, sizeof(payload)));
  Serial.printf("[lora] ráfaga: %u capturas + ESTADO\n", rtc.n_samples);

  rtc.n_samples = 0;
  rtc.reboot_pending = false;
  rtc.last_tx_s = now;

  // Ventana RX: la Raspberry responde con CONFIG si nuestra config no es la deseada.
  sgpmp::ConfigDownlink dl;
  uint32_t dl_fcnt;
  if (receive_config(aes, dl, dl_fcnt, LORA_RX_WINDOW_MS)) {
    // Se registra antes de aplicarlo: el mismo CONFIG repetido no vuelve a pasar.
    rtc.fcnt_bajada = dl_fcnt;
    NodeCounters counters = node_counters_load();
    counters.fcnt_bajada = dl_fcnt;
    node_counters_save(counters);
    const bool valid = node_config_valid(dl.frecuencia_captura_min, dl.intervalo_transmision_min);
    if (valid) {
      cfg = {dl.cfg_version, dl.frecuencia_captura_min, dl.intervalo_transmision_min};
      node_config_save(cfg);
      Serial.printf("[config] v%u: captura %u min, transmisión %u min\n", cfg.cfg_version,
                    cfg.frecuencia_captura_min, cfg.intervalo_transmision_min);
    }
    const sgpmp::AckConfig ack{dl.cfg_version,
                               valid ? sgpmp::RESULTADO_OK : sgpmp::RESULTADO_INVALIDA};
    send(aes, sgpmp::ACK_CONFIG, payload,
         sgpmp::ack_config_payload(ack, payload, sizeof(payload)));
  }
  LoRa.sleep();
}

// Duerme `ms` en tramos de a lo sumo platform_max_sleep_ms(): el ESP8266 no
// llega a las 24 h que admite frecuencia_captura.
[[noreturn]] void sleep_for(uint64_t ms) {
  const uint64_t max_ms = platform_max_sleep_ms();
  const uint64_t chunk = ms > max_ms ? max_ms : ms;
  // Un resto menor a 1 s no justifica otro despertar.
  rtc.sleep_pending_ms = ms - chunk > 1000 ? ms - chunk : 0;
  rtc.clock_ms += millis() + chunk;
  platform_rtc_save(&rtc, sizeof(rtc));
  Serial.printf("[sleep] %lu s\n", static_cast<unsigned long>(chunk / 1000));
  Serial.flush();
  platform_deep_sleep(chunk);
}

[[noreturn]] void sleep_until_next_capture(const NodeConfig& cfg) {
  const uint64_t period_ms = static_cast<uint64_t>(cfg.frecuencia_captura_min) * 60000ULL;
  const uint64_t awake_ms = millis();
  sleep_for(period_ms > awake_ms + 1000 ? period_ms - awake_ms : 1000);
}

}  // namespace

void setup() {
  platform_begin();
  Serial.begin(SERIAL_BAUD);

  bool cold = !platform_woke_from_sleep();
  if (!cold) {
    platform_rtc_load(&rtc, sizeof(rtc));
    cold = rtc.magic != kRtcMagic;
  }
  if (cold) {
    memset(&rtc, 0, sizeof(rtc));
    rtc.magic = kRtcMagic;
    rtc.reboot_pending = true;
    // Tras un corte, los contadores siguen desde lo guardado en flash; el
    // primer envío reserva un bloque nuevo.
    const NodeCounters counters = node_counters_load();
    rtc.fcnt_subida = rtc.fcnt_limite = counters.fcnt_reservado;
    rtc.fcnt_bajada = counters.fcnt_bajada;
  } else if (rtc.sleep_pending_ms > millis() + 1000) {
    // Tramo intermedio de un sleep largo: no hay captura todavía. Si falta
    // menos de 1 s se captura ya (deepSleep(0) en el ESP8266 no despierta).
    sleep_for(rtc.sleep_pending_ms - millis());
  }
  key_ok = NODE_ID != 0 && parse_key(LORA_KEY_HEX, key_bytes);
  Serial.printf("\n[boot] nodo %s (0x%04x) fw %d.%d (%s)\n", NODE_SERIAL, NODE_ID, FW_MAJOR,
                FW_MINOR, cold ? "encendido" : "deep sleep");
  if (key_ok) {
    // La misma huella aparece en el log de la Raspberry: así se comprueba la clave.
    Serial.printf("[lora] huella de clave %08lx\n",
                  static_cast<unsigned long>(sgpmp::key_check_value(key_bytes)));
  }

  NodeConfig cfg = node_config_load();
  sensors_begin(cold);
  const uint32_t now = uptime_s();
  capture(now);

  // Al encender se transmite enseguida: la Raspberry conoce al nodo y le
  // puede enviar la config vigente sin esperar un intervalo completo.
  const bool due = cold || rtc.n_samples >= MAX_SAMPLES ||
                   now - rtc.last_tx_s >= static_cast<uint32_t>(cfg.intervalo_transmision_min) * 60;
  if (due) {
    transmit_burst(cfg, now);
  }
  sleep_until_next_capture(cfg);
}

void loop() {}  // nunca se llega: setup() termina en deep sleep
