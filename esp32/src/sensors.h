// Lectura de sensores del nodo.
//
// El hardware de sensado todavía no está definido (docs/PROTOCOLO_LORA.md
// §10): SENSOR_SIMULADO genera valores para probar el enlace de punta a punta.
// Para un sensor real, agregar su driver en sensors.cpp bajo su propio #if y
// asignarle un `code` acordado en EDGE_LORA_VARIABLES de la Raspberry.
#pragma once

#include "sgpmp_protocol.h"

// Códigos de variable (deben existir en EDGE_LORA_VARIABLES).
#ifndef SENSOR_CODE_TEMPERATURA
#define SENSOR_CODE_TEMPERATURA 1
#endif
#ifndef SENSOR_CODE_HUMEDAD
#define SENSOR_CODE_HUMEDAD 2
#endif

struct SensorReading {
  uint8_t count;
  bool error;  // alguna lectura falló: se informa en el flag del ESTADO
  sgpmp::Measurement m[sgpmp::MAX_MEASUREMENTS];
};

void sensors_begin();
void sensors_read(SensorReading& out);
