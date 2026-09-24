#include "sensors.h"

#include <Arduino.h>

void sensors_begin() {}

void sensors_read(SensorReading& out) {
  out.count = 0;
  out.error = false;

#if defined(SENSOR_SIMULADO)
  // Temperatura del sensor interno del ESP32 (impreciso, pero real) y una
  // humedad sintética: suficiente para validar el enlace, no para medir.
  out.m[out.count++] = {SENSOR_CODE_TEMPERATURA, temperatureRead()};
  out.m[out.count++] = {SENSOR_CODE_HUMEDAD, 40.0f + static_cast<float>(esp_random() % 5000) / 100.0f};
#else
#error "Definir un sensor (ej. -DSENSOR_SIMULADO) en platformio.ini"
#endif
}
