#include "platform.h"

#include <Arduino.h>
#include <SPI.h>

#include "config.h"

#if defined(ESP8266)
#include <ESP8266WiFi.h>
extern "C" {
#include <user_interface.h>
}

void platform_begin() {
  // El nodo nunca usa WiFi: además de ahorrar batería, deja limpio el
  // timing de los buses de un hilo (DHT22, DS18B20).
  WiFi.persistent(false);
  WiFi.mode(WIFI_OFF);
  WiFi.forceSleepBegin();
}

bool platform_woke_from_sleep() {
  return ESP.getResetInfoPtr()->reason == REASON_DEEP_SLEEP_AWAKE;
}

// rtcUserMemory trabaja en bloques de 4 bytes: se copia a un buffer alineado.
void platform_rtc_load(void* data, size_t len) {
  static uint32_t buf[PLATFORM_RTC_MAX_LEN / 4];
  const size_t words = (len + 3) / 4;
  ESP.rtcUserMemoryRead(0, buf, words * 4);
  memcpy(data, buf, len);
}

void platform_rtc_save(const void* data, size_t len) {
  static uint32_t buf[PLATFORM_RTC_MAX_LEN / 4];
  const size_t words = (len + 3) / 4;
  memcpy(buf, data, len);
  ESP.rtcUserMemoryWrite(0, buf, words * 4);
}

uint64_t platform_max_sleep_ms() {
  // deepSleepMax() (~3.5 h) depende de la calibración del reloj RTC; se deja
  // un 5 % de margen.
  return ESP.deepSleepMax() / 1000ULL * 95 / 100;
}

void platform_deep_sleep(uint64_t ms) {
  // Requiere D0 (GPIO16) puenteado a RST. La radio WiFi queda apagada
  // también en el siguiente arranque.
  ESP.deepSleep(ms * 1000ULL, WAKE_RF_DISABLED);
  for (;;) {
    yield();
  }
}

void platform_spi_begin() { SPI.begin(); }

#elif defined(ESP32)
#include <esp_sleep.h>

namespace {
RTC_DATA_ATTR uint8_t rtc_buf[PLATFORM_RTC_MAX_LEN];
}

void platform_begin() {}

bool platform_woke_from_sleep() {
  return esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER;
}

void platform_rtc_load(void* data, size_t len) { memcpy(data, rtc_buf, len); }

void platform_rtc_save(const void* data, size_t len) { memcpy(rtc_buf, data, len); }

uint64_t platform_max_sleep_ms() { return UINT64_MAX / 1000ULL; }

void platform_deep_sleep(uint64_t ms) {
  esp_sleep_enable_timer_wakeup(ms * 1000ULL);
  esp_deep_sleep_start();
}

void platform_spi_begin() { SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_SS); }

#else
#error "Plataforma no soportada: ESP8266 o ESP32"
#endif
