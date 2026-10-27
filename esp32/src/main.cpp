// Nodo sensor SGPMP: ESP8266 o ESP32 + SX1276, a batería (Fase 1).
//
// Todo el trabajo ocurre en setup(): el nodo despierta del deep sleep cada
// frecuencia_captura, captura, y cada intervalo_transmision transmite una
// ráfaga (TELEMETRIA por captura + ESTADO), abre la ventana RX para un
// posible CONFIG y vuelve a dormir. Ver docs/PROTOCOLO_LORA.md §6.
//
// Nunca habla MQTT: solo LoRa con la Raspberry del sitio.
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

constexpr uint32_t kRtcMagic = 0x53475032;  // "SGP2"

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
  uint8_t seq;
  uint8_t n_samples;
  bool reboot_pending;
  Sample samples[MAX_SAMPLES];
};
static_assert(sizeof(RtcState) <= PLATFORM_RTC_MAX_LEN, "bajar MAX_SAMPLES");

RtcState rtc;

uint32_t uptime_s() { return static_cast<uint32_t>((rtc.clock_ms + millis()) / 1000); }

sgpmp::Header next_header() {
  return sgpmp::Header{NET_ID, NODE_ID, 0, rtc.seq++};
}

bool radio_begin() {
  platform_spi_begin();
  LoRa.setSPI(SPI);
  LoRa.setPins(PIN_LORA_SS, PIN_LORA_RST, PIN_LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ_HZ)) {
    return false;
  }
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW_HZ);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setSyncWord(LORA_SYNC_WORD);
  LoRa.setPreambleLength(8);
  LoRa.enableCrc();
  LoRa.setTxPower(LORA_TX_POWER_DBM, PA_OUTPUT_PA_BOOST_PIN);
  return true;
}

void send(const uint8_t* frame, size_t len) {
  if (len == 0) {
    Serial.println("[lora] trama inválida, no se envía");
    return;
  }
  LoRa.beginPacket();
  LoRa.write(frame, len);
  LoRa.endPacket();  // bloquea hasta TxDone
}

// Espera un CONFIG dirigido a este nodo durante la ventana RX.
bool receive_config(sgpmp::ConfigDownlink& out, uint32_t window_ms) {
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
    sgpmp::Header h;
    const uint8_t* payload;
    size_t payload_len;
    if (sgpmp::decode_frame(buf, len, h, &payload, &payload_len) != sgpmp::DECODE_OK) {
      continue;
    }
    if (h.net_id != NET_ID || h.node_id != NODE_ID || h.msg_type != sgpmp::CONFIG) {
      continue;  // tráfico de otro nodo o de otra red
    }
    if (sgpmp::decode_config(payload, payload_len, out) == sgpmp::DECODE_OK) {
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
  if (!radio_begin()) {
    // Las capturas se conservan y se reintentan en el próximo ciclo.
    Serial.println("[lora] el SX1276 no responde");
    return;
  }

  uint8_t frame[sgpmp::MAX_FRAME_LEN];
  uint8_t flags = rtc.reboot_pending ? sgpmp::FLAG_REINICIO : 0;
  for (uint8_t i = 0; i < rtc.n_samples; ++i) {
    const Sample& s = rtc.samples[i];
    if (s.reading.error) {
      flags |= sgpmp::FLAG_ERROR_SENSOR;
    }
    if (s.reading.count == 0) {
      continue;  // todos los sensores fallaron: solo queda el flag
    }
    const uint32_t age = now - s.captured_s;
    send(frame, sgpmp::encode_telemetria(next_header(), age > 0xFFFF ? 0xFFFF : age,
                                         s.reading.m, s.reading.count, frame, sizeof(frame)));
  }

  const sgpmp::Estado estado{battery_percent(),         cfg.cfg_version,
                             cfg.frecuencia_captura_min, cfg.intervalo_transmision_min,
                             FW_MAJOR,                   FW_MINOR,
                             flags};
  send(frame, sgpmp::encode_estado(next_header(), estado, frame, sizeof(frame)));
  Serial.printf("[lora] ráfaga: %u capturas + ESTADO\n", rtc.n_samples);

  rtc.n_samples = 0;
  rtc.reboot_pending = false;
  rtc.last_tx_s = now;

  // Ventana RX: la Raspberry responde con CONFIG si nuestra config no es la deseada.
  sgpmp::ConfigDownlink dl;
  if (receive_config(dl, LORA_RX_WINDOW_MS)) {
    const bool valid = node_config_valid(dl.frecuencia_captura_min, dl.intervalo_transmision_min);
    if (valid) {
      cfg = {dl.cfg_version, dl.frecuencia_captura_min, dl.intervalo_transmision_min};
      node_config_save(cfg);
      Serial.printf("[config] v%u: captura %u min, transmisión %u min\n", cfg.cfg_version,
                    cfg.frecuencia_captura_min, cfg.intervalo_transmision_min);
    }
    const sgpmp::AckConfig ack{dl.cfg_version,
                               valid ? sgpmp::RESULTADO_OK : sgpmp::RESULTADO_INVALIDA};
    send(frame, sgpmp::encode_ack_config(next_header(), ack, frame, sizeof(frame)));
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
  Serial.begin(115200);

  bool cold = !platform_woke_from_sleep();
  if (!cold) {
    platform_rtc_load(&rtc, sizeof(rtc));
    cold = rtc.magic != kRtcMagic;
  }
  if (cold) {
    memset(&rtc, 0, sizeof(rtc));
    rtc.magic = kRtcMagic;
    rtc.reboot_pending = true;
  } else if (rtc.sleep_pending_ms > millis() + 1000) {
    // Tramo intermedio de un sleep largo: no hay captura todavía. Si falta
    // menos de 1 s se captura ya (deepSleep(0) en el ESP8266 no despierta).
    sleep_for(rtc.sleep_pending_ms - millis());
  }
  Serial.printf("\n[boot] nodo 0x%04x fw %d.%d (%s)\n", NODE_ID, FW_MAJOR, FW_MINOR,
                cold ? "encendido" : "deep sleep");

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
