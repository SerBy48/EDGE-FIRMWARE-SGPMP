#include "sensors.h"

#include <Arduino.h>

#include "config.h"
#include "sensor_drivers.h"

#if defined(SENSOR_MPU6050) || defined(SENSOR_MAX30102)
#include <Wire.h>
#endif

#if !defined(SENSOR_SIMULADO) && !defined(SENSOR_DHT22) && !defined(SENSOR_DS18B20) && \
    !defined(SENSOR_MPU6050) && !defined(SENSOR_MAX30102)
#error "Definir al menos un sensor (SENSOR_DHT22, SENSOR_DS18B20, ...) en platformio.ini"
#endif
#if defined(SENSOR_SIMULADO) && defined(SENSOR_DHT22)
#error "SENSOR_SIMULADO usa los codes del DHT22: no combinarlos"
#endif

namespace {

constexpr uint8_t kCodes[VAR_COUNT] = {
    SENSOR_CODE_TEMPERATURA,       SENSOR_CODE_HUMEDAD,
    SENSOR_CODE_TEMPERATURA_DS18B20, SENSOR_CODE_ACTIVIDAD,
    SENSOR_CODE_ACELERACION_PICO,  SENSOR_CODE_INCLINACION_PITCH,
    SENSOR_CODE_INCLINACION_ROLL,  SENSOR_CODE_FRECUENCIA_CARDIACA,
    SENSOR_CODE_SPO2,
};

String describe(uint8_t mask) {
  static const struct {
    uint8_t bit;
    const char* name;
  } kNames[] = {{sgpmp::SENSOR_BIT_DHT22, "DHT22"},
                {sgpmp::SENSOR_BIT_DS18B20, "DS18B20"},
                {sgpmp::SENSOR_BIT_MPU6050, "MPU6050"},
                {sgpmp::SENSOR_BIT_MAX30102, "MAX30102"}};
  String s;
  for (const auto& n : kNames) {
    if (mask & n.bit) {
      s += s.length() ? ", " : "";
      s += n.name;
    }
  }
  return s.length() ? s : String("ninguno");
}

[[maybe_unused]] void track(SensorReading& out, uint8_t bit, SensorStatus status) {
  if (status != SensorStatus::AUSENTE) {
    out.detected |= bit;
  }
  if (status == SensorStatus::FALLA) {
    out.failing |= bit;
  }
}

}  // namespace

uint8_t sensor_var_code(SensorVar var) { return kCodes[var]; }

void sensors_begin(bool cold) {
#if defined(SENSOR_MPU6050) || defined(SENSOR_MAX30102)
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  Wire.setClock(100000);  // los módulos traen pull-ups débiles
#endif
  // El orden importa: la conversión del DS18B20 (hasta 750 ms) corre
  // mientras se leen los demás.
#if defined(SENSOR_DS18B20)
  ds18b20_begin();
#endif
#if defined(SENSOR_DHT22)
  dht22_begin(cold);
#else
  (void)cold;
#endif
#if defined(SENSOR_MPU6050)
  mpu6050_begin();
#endif
#if defined(SENSOR_MAX30102)
  max30102_begin();
#endif
}

void sensors_read(SensorReading& out) {
  out = SensorReading{};

  // Primero la ventana del MPU6050 (~2 s): cubre la conversión del DS18B20 y
  // el arranque del DHT22 tras un encendido.
#if defined(SENSOR_MPU6050)
  track(out, sgpmp::SENSOR_BIT_MPU6050, mpu6050_read(out));
#endif
#if defined(SENSOR_DS18B20)
  track(out, sgpmp::SENSOR_BIT_DS18B20, ds18b20_read(out));
#endif
#if defined(SENSOR_DHT22)
  track(out, sgpmp::SENSOR_BIT_DHT22, dht22_read(out));
#endif
#if defined(SENSOR_MAX30102)
  track(out, sgpmp::SENSOR_BIT_MAX30102, max30102_read(out));
#endif

#if defined(SENSOR_SIMULADO)
  // Valores sintéticos: suficientes para validar el enlace, no para medir.
#if defined(ESP32)
  out.set(VAR_TEMPERATURA, temperatureRead());  // sensor interno del ESP32
#else
  out.set(VAR_TEMPERATURA, 20.0f + static_cast<float>(random(0, 1000)) / 100.0f);
#endif
  out.set(VAR_HUMEDAD, 40.0f + static_cast<float>(random(0, 5000)) / 100.0f);
  out.detected |= sgpmp::SENSOR_BIT_DHT22;
#endif

  for (uint8_t i = 0; i < VAR_COUNT; ++i) {
    if (out.has(static_cast<SensorVar>(i))) {
      Serial.printf("[sensor] code %u = %.2f\n", kCodes[i], out.values[i]);
    }
  }
  Serial.printf("[sensor] detectados: %s; fallando: %s\n", describe(out.detected).c_str(),
                describe(out.failing).c_str());
}
