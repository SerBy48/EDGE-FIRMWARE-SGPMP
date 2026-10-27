// Lectura de sensores del nodo.
//
// Cada sensor se activa con su flag en platformio.ini y tiene su propio
// `code`, que debe existir en EDGE_LORA_VARIABLES de la Raspberry:
//   SENSOR_DHT22     temperatura (SENSOR_CODE_TEMPERATURA) y humedad
//                    (SENSOR_CODE_HUMEDAD) del DHT22/AM2302
//   SENSOR_DS18B20   temperatura del DS18B20 (SENSOR_CODE_TEMPERATURA_DS18B20)
//   SENSOR_SIMULADO  valores sintéticos para probar el enlace de punta a punta
#pragma once

#include "sgpmp_protocol.h"

// Códigos de variable (deben existir en EDGE_LORA_VARIABLES).
#ifndef SENSOR_CODE_TEMPERATURA
#define SENSOR_CODE_TEMPERATURA 1
#endif
#ifndef SENSOR_CODE_HUMEDAD
#define SENSOR_CODE_HUMEDAD 2
#endif
#ifndef SENSOR_CODE_TEMPERATURA_DS18B20
#define SENSOR_CODE_TEMPERATURA_DS18B20 3
#endif

// Mediciones por captura. Menor que sgpmp::MAX_MEASUREMENTS para que las
// capturas pendientes quepan en la memoria RTC del ESP8266.
constexpr uint8_t SENSOR_MAX_MEASUREMENTS = 4;
static_assert(SENSOR_MAX_MEASUREMENTS <= sgpmp::MAX_MEASUREMENTS, "no cabe en una TELEMETRIA");

struct SensorReading {
  uint8_t count;
  bool error;  // alguna lectura falló: se informa en el flag del ESTADO
  sgpmp::Measurement m[SENSOR_MAX_MEASUREMENTS];
};

// `cold`: el nodo acaba de encenderse (el DHT22 necesita ~2 s antes de la
// primera lectura; tras un deep sleep ya lleva minutos alimentado).
void sensors_begin(bool cold);
void sensors_read(SensorReading& out);
