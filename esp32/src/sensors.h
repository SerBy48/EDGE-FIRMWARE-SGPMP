// Catálogo de sensores del nodo.
//
// En cada captura se revisan todos los sensores compilados (flags SENSOR_* en
// platformio.ini): los que no se detectan se omiten del envío y los que
// fallan levantan su bit en `failing`. El ESTADO lleva ambos al gateway.
//
//   SENSOR_DHT22     temperatura y humedad ambiente
//   SENSOR_DS18B20   temperatura (sonda)
//   SENSOR_MPU6050   actividad, pico de aceleración e inclinación
//   SENSOR_MAX30102  frecuencia cardíaca y SpO2 (con el dedo apoyado)
//   SENSOR_SIMULADO  valores sintéticos para probar el enlace (solo DHT22)
#pragma once

#include <stdint.h>

#include "sgpmp_protocol.h"

// Variables del catálogo. El índice es fijo: es la posición en
// SensorReading::values. El `code` LoRa de cada una sale de SENSOR_CODE_*.
enum SensorVar : uint8_t {
  VAR_TEMPERATURA,          // DHT22, C
  VAR_HUMEDAD,              // DHT22, %
  VAR_TEMPERATURA_DS18B20,  // DS18B20, C
  VAR_ACTIVIDAD,            // MPU6050, g (RMS de la aceleración sin gravedad)
  VAR_ACELERACION_PICO,     // MPU6050, g (máximo del módulo de la aceleración)
  VAR_INCLINACION_PITCH,    // MPU6050, grados
  VAR_INCLINACION_ROLL,     // MPU6050, grados
  VAR_FRECUENCIA_CARDIACA,  // MAX30102, BPM
  VAR_SPO2,                 // MAX30102, %
  VAR_COUNT,
};

// Codes de variable (deben existir en EDGE_LORA_VARIABLES).
#ifndef SENSOR_CODE_TEMPERATURA
#define SENSOR_CODE_TEMPERATURA 1
#endif
#ifndef SENSOR_CODE_HUMEDAD
#define SENSOR_CODE_HUMEDAD 2
#endif
#ifndef SENSOR_CODE_TEMPERATURA_DS18B20
#define SENSOR_CODE_TEMPERATURA_DS18B20 3
#endif
#ifndef SENSOR_CODE_ACTIVIDAD
#define SENSOR_CODE_ACTIVIDAD 4
#endif
#ifndef SENSOR_CODE_ACELERACION_PICO
#define SENSOR_CODE_ACELERACION_PICO 5
#endif
#ifndef SENSOR_CODE_INCLINACION_PITCH
#define SENSOR_CODE_INCLINACION_PITCH 6
#endif
#ifndef SENSOR_CODE_INCLINACION_ROLL
#define SENSOR_CODE_INCLINACION_ROLL 7
#endif
#ifndef SENSOR_CODE_FRECUENCIA_CARDIACA
#define SENSOR_CODE_FRECUENCIA_CARDIACA 8
#endif
#ifndef SENSOR_CODE_SPO2
#define SENSOR_CODE_SPO2 9
#endif

uint8_t sensor_var_code(SensorVar var);

// Una captura. Compacta a propósito: varias quedan en la memoria RTC del
// ESP8266 (512 B) entre transmisiones.
struct SensorReading {
  uint16_t valid;    // bit i: values[i] se midió
  uint8_t detected;  // bits de sgpmp::SensorBit
  uint8_t failing;   // bits de sgpmp::SensorBit
  float values[VAR_COUNT];

  void set(SensorVar var, float value) {
    values[var] = value;
    valid |= static_cast<uint16_t>(1u << var);
  }
  bool has(SensorVar var) const { return valid & (1u << var); }
};

// `cold`: el nodo acaba de encenderse (el DHT22 necesita ~2 s antes de la
// primera lectura; tras un deep sleep ya lleva minutos alimentado).
void sensors_begin(bool cold);
void sensors_read(SensorReading& out);
