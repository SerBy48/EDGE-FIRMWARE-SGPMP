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
// Un solo DS18B20 por nodo: se le habla con SKIP_ROM, sin buscar su
// dirección. Así también funcionan los clones con la serie mal grabada (CRC
// de ROM inválido), que DallasTemperature descarta y reporta como -127. Cada
// lectura se sigue validando con el CRC del scratchpad. Requiere VDD a 3V3
// (sin alimentación parásita).
constexpr uint8_t kSkipRom = 0xCC;
constexpr uint8_t kConvertT = 0x44;
constexpr uint8_t kReadScratchpad = 0xBE;
constexpr uint8_t kWriteScratchpad = 0x4E;
// 750 ms a 12 bits; la mitad por cada bit menos.
constexpr uint32_t kDsConversionMs = 750u >> (12 - DS18B20_RESOLUTION);
// Valor de reset del scratchpad: aparece si la conversión no llegó a correr.
constexpr int16_t kDsResetRaw = 85 * 16;

OneWire one_wire(PIN_DS18B20);
bool ds_converting = false;
uint32_t ds_requested_ms = 0;

bool ds_command(uint8_t cmd) {
  if (!one_wire.reset()) {
    return false;  // sin pulso de presencia
  }
  one_wire.write(kSkipRom);
  one_wire.write(cmd);
  return true;
}

// Devuelve nullptr si `celsius` es válido, o qué revisar si no.
const char* ds_read(float& celsius) {
  if (!ds_converting || !ds_command(kReadScratchpad)) {
    return "sin pulso de presencia: revisar DATA, VDD/GND y pull-up 4.7k a 3V3";
  }
  uint8_t sp[9];
  one_wire.read_bytes(sp, sizeof(sp));
  bool all_zero = true;
  for (uint8_t b : sp) {
    all_zero = all_zero && b == 0;
  }
  if (all_zero) {
    return "la línea queda en bajo: DATA en corto a GND o sin pull-up";
  }
  if (OneWire::crc8(sp, 8) != sp[8]) {
    return "CRC del scratchpad inválido: contacto flojo, pull-up débil o ruido";
  }
  int16_t raw = static_cast<int16_t>((sp[1] << 8) | sp[0]);
  if (raw == kDsResetRaw) {
    return "la conversión no corrió: alimentación insuficiente, conectar VDD a 3V3";
  }
  raw &= ~((1 << (12 - DS18B20_RESOLUTION)) - 1);  // bits sin definir a menos de 12 bits
  celsius = raw / 16.0f;
  return nullptr;
}
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
  // La resolución va en la RAM del sensor (no en su EEPROM): se escribe en
  // cada arranque. TH/TL (alarmas) no se usan.
  if (ds_command(kWriteScratchpad)) {
    one_wire.write(0x4B);
    one_wire.write(0x46);
    one_wire.write(((DS18B20_RESOLUTION - 9) << 5) | 0x1F);
    ds_converting = ds_command(kConvertT);
  }
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
  while (ds_converting && millis() - ds_requested_ms < kDsConversionMs) {
    delay(5);
  }
  float ds = 0;
  if (const char* hint = ds_read(ds)) {
    Serial.printf("[sensor] DS18B20 en GPIO%d lectura inválida: %s\n", PIN_DS18B20, hint);
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
