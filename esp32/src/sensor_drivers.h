// Interfaz interna de cada sensor del catálogo (sensors.cpp la recorre).
//
// begin(): detecta el sensor y arranca lo que pueda correr en paralelo.
// read():  mide y deja sus variables en `out`. Devuelve:
//   AUSENTE  no se detectó: no se envía nada, no es un error
//   OK       detectado; puede no dejar variables (MAX30102 sin dedo)
//   FALLA    detectado pero la lectura no es confiable
#pragma once

#include "sensors.h"

enum class SensorStatus : uint8_t { AUSENTE, OK, FALLA };

void dht22_begin(bool cold);
SensorStatus dht22_read(SensorReading& out);

void ds18b20_begin();
SensorStatus ds18b20_read(SensorReading& out);

void mpu6050_begin();
SensorStatus mpu6050_read(SensorReading& out);

void max30102_begin();
SensorStatus max30102_read(SensorReading& out);
