// DS18B20 por OneWire. Un solo sensor por nodo: se le habla con SKIP_ROM,
// sin buscar su dirección. Así también funcionan los clones con la serie mal
// grabada (CRC de ROM inválido), que DallasTemperature descarta y reporta
// como -127. Cada lectura se valida con el CRC del scratchpad. Requiere VDD
// a 3V3 (sin alimentación parásita).
#if defined(SENSOR_DS18B20)
#include <Arduino.h>
#include <OneWire.h>

#include "config.h"
#include "sensor_drivers.h"

namespace {

constexpr uint8_t kSkipRom = 0xCC;
constexpr uint8_t kConvertT = 0x44;
constexpr uint8_t kReadScratchpad = 0xBE;
constexpr uint8_t kWriteScratchpad = 0x4E;
// 750 ms a 12 bits; la mitad por cada bit menos.
constexpr uint32_t kConversionMs = 750u >> (12 - DS18B20_RESOLUTION);
// Valor de reset del scratchpad: aparece si la conversión no llegó a correr.
constexpr int16_t kResetRaw = 85 * 16;

OneWire one_wire(PIN_DS18B20);
bool converting = false;
uint32_t requested_ms = 0;

bool command(uint8_t cmd) {
  if (!one_wire.reset()) {
    return false;  // sin pulso de presencia
  }
  one_wire.write(kSkipRom);
  one_wire.write(cmd);
  return true;
}

SensorStatus fail(const char* hint) {
  Serial.printf("[sensor] DS18B20 en GPIO%d lectura inválida: %s\n", PIN_DS18B20, hint);
  return SensorStatus::FALLA;
}

}  // namespace

void ds18b20_begin() {
  // La resolución va en la RAM del sensor (no en su EEPROM): se escribe en
  // cada arranque. TH/TL (alarmas) no se usan.
  if (command(kWriteScratchpad)) {
    one_wire.write(0x4B);
    one_wire.write(0x46);
    one_wire.write(((DS18B20_RESOLUTION - 9) << 5) | 0x1F);
    converting = command(kConvertT);
  }
  requested_ms = millis();
}

SensorStatus ds18b20_read(SensorReading& out) {
  if (!converting) {
    return SensorStatus::AUSENTE;  // no contestó al pulso de presencia
  }
  while (millis() - requested_ms < kConversionMs) {
    delay(5);
  }
  if (!command(kReadScratchpad)) {
    return fail("dejó de contestar: contacto flojo en DATA, VDD o GND");
  }
  uint8_t sp[9];
  one_wire.read_bytes(sp, sizeof(sp));
  bool all_zero = true;
  for (uint8_t b : sp) {
    all_zero = all_zero && b == 0;
  }
  if (all_zero) {
    return fail("la línea queda en bajo: DATA en corto a GND o sin pull-up");
  }
  if (OneWire::crc8(sp, 8) != sp[8]) {
    return fail("CRC del scratchpad inválido: contacto flojo, pull-up débil o ruido");
  }
  int16_t raw = static_cast<int16_t>((sp[1] << 8) | sp[0]);
  if (raw == kResetRaw) {
    return fail("la conversión no corrió: alimentación insuficiente, conectar VDD a 3V3");
  }
  raw &= ~((1 << (12 - DS18B20_RESOLUTION)) - 1);  // bits sin definir a menos de 12 bits
  out.set(VAR_TEMPERATURA_DS18B20, raw / 16.0f);
  return SensorStatus::OK;
}
#endif
