#include "battery.h"

#include <Arduino.h>

#include "config.h"
#include "sgpmp_protocol.h"

uint8_t battery_percent() {
#if BATTERY_ADC_PIN < 0
  return sgpmp::BATERIA_DESCONOCIDA;
#else
  uint32_t sum = 0;
  for (int i = 0; i < 8; ++i) {
    sum += analogReadMilliVolts(BATTERY_ADC_PIN);
  }
  const float mv = (sum / 8.0f) * BATTERY_DIVIDER;
  const float pct = (mv - BATTERY_EMPTY_MV) * 100.0f / (BATTERY_FULL_MV - BATTERY_EMPTY_MV);
  return static_cast<uint8_t>(constrain(pct, 0.0f, 100.0f));
#endif
}
