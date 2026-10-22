#include "sensors.h"

#include <Arduino.h>

#include "config.h"

#if !defined(SENSOR_SIMULADO) && !defined(SENSOR_DHT22) && !defined(SENSOR_DS18B20)
#error "Definir al menos un sensor (SENSOR_DHT22, SENSOR_DS18B20 o SENSOR_SIMULADO) en platformio.ini"
#endif
#if defined(SENSOR_SIMULADO) && (defined(SENSOR_DHT22) || defined(SENSOR_DS18B20))
#error "SENSOR_SIMULADO usa los mismos codes que los sensores reales: no combinarlos"
#endif

#if defined(SENSOR_DHT22)
#include <DHT.h>
#endif
#if defined(SENSOR_DS18B20)
#include <DallasTemperature.h>
#include <OneWire.h>
#endif

namespace {

#if defined(SENSOR_DHT22)
// El datasheet pide 1-2 s tras alimentarlo antes de la primera lectura.
constexpr uint32_t kDhtWarmupMs = 2000;
DHT dht(PIN_DHT22, DHT22);
bool dht_cold = false;
#endif

#if defined(SENSOR_DS18B20)
OneWire one_wire(PIN_DS18B20);
DallasTemperature ds18b20(&one_wire);
uint32_t ds_requested_ms = 0;
// 85 C es el valor de reset del scratchpad: aparece si la conversión no llegó
// a correr (alimentación o bus inestables), no es una medición.
constexpr float kDsResetValue = 85.0f;
#endif

void add(SensorReading& out, uint8_t code, float value) {
  if (out.count < SENSOR_MAX_MEASUREMENTS) {
    out.m[out.count++] = {code, value};
  }
}

}  // namespace

void sensors_begin(bool cold) {
#if defined(SENSOR_DS18B20)
  // La conversión arranca aquí y corre en paralelo con la lectura del DHT22.
  ds18b20.begin();
  ds18b20.setResolution(DS18B20_RESOLUTION);
  ds18b20.setWaitForConversion(false);
  ds18b20.requestTemperatures();
  ds_requested_ms = millis();
#endif
#if defined(SENSOR_DHT22)
  dht.begin();
  dht_cold = cold;
#else
  (void)cold;
#endif
}

void sensors_read(SensorReading& out) {
  out.count = 0;
  out.error = false;

#if defined(SENSOR_DHT22)
  if (dht_cold) {
    while (millis() < kDhtWarmupMs) {
      delay(10);
    }
  }
  const float t = dht.readTemperature();
  const float h = dht.readHumidity();  // misma lectura de 40 bits que la temperatura
  if (isnan(t) || isnan(h)) {
    Serial.println("[sensor] DHT22 sin respuesta o checksum inválido");
    out.error = true;
  } else {
    add(out, SENSOR_CODE_TEMPERATURA, t);
    add(out, SENSOR_CODE_HUMEDAD, h);
  }
#endif

#if defined(SENSOR_DS18B20)
  const uint32_t conv_ms = ds18b20.millisToWaitForConversion(DS18B20_RESOLUTION);
  while (millis() - ds_requested_ms < conv_ms) {
    delay(5);
  }
  const float ds = ds18b20.getTempCByIndex(0);
  if (ds == DEVICE_DISCONNECTED_C || ds == kDsResetValue) {
    Serial.printf("[sensor] DS18B20 lectura inválida (%.2f)\n", ds);
    out.error = true;
  } else {
    add(out, SENSOR_CODE_TEMPERATURA_DS18B20, ds);
  }
#endif

#if defined(SENSOR_SIMULADO)
  // Valores sintéticos: suficientes para validar el enlace, no para medir.
#if defined(ESP32)
  add(out, SENSOR_CODE_TEMPERATURA, temperatureRead());  // sensor interno del ESP32
#else
  add(out, SENSOR_CODE_TEMPERATURA, 20.0f + static_cast<float>(random(0, 1000)) / 100.0f);
#endif
  add(out, SENSOR_CODE_HUMEDAD, 40.0f + static_cast<float>(random(0, 5000)) / 100.0f);
#endif

  for (uint8_t i = 0; i < out.count; ++i) {
    Serial.printf("[sensor] code %u = %.2f\n", out.m[i].code, out.m[i].value);
  }
}
