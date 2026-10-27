// MAX30102: frecuencia cardíaca y SpO2 con el algoritmo de Maxim (librería
// SparkFun). Necesita el dedo apoyado: si no lo detecta en ~0.5 s no gasta
// la ventana completa y no es un error (el sensor está, solo no hay dedo).
// El SpO2 de estos módulos es orientativo, no clínico.
//
// Entre capturas los LEDs quedan apagados (shutdown, ~1 µA).
#if defined(SENSOR_MAX30102)
#include <Arduino.h>
#include <MAX30105.h>
#include <Wire.h>
#include <spo2_algorithm.h>  // define buffers static: incluir solo en este archivo

#include "config.h"
#include "sensor_drivers.h"

namespace {

// 100 muestras/s con promedio de 4 = las 25 muestras/s (FreqS) que espera el
// algoritmo; BUFFER_SIZE = 100 muestras = 4 s.
constexpr uint8_t kLedPower = 0x3C;
constexpr uint8_t kSampleAverage = 4;
constexpr uint8_t kLedModeRedIr = 2;
constexpr int kSampleRate = 100;
constexpr int kPulseWidth = 411;
constexpr int kAdcRange = 4096;
constexpr uint8_t kSettleSamples = FreqS;  // 1 s para que se estabilice la señal
constexpr uint8_t kFingerSamples = FreqS / 2;

MAX30105 sensor;
bool present = false;
uint32_t red_buf[BUFFER_SIZE];
uint32_t ir_buf[BUFFER_SIZE];

// false si el sensor dejó de entregar muestras (I2C caído).
bool next_sample(uint32_t& red, uint32_t& ir) {
  while (!sensor.available()) {
    if (!sensor.safeCheck(250)) {
      return false;
    }
  }
  red = sensor.getFIFORed();
  ir = sensor.getFIFOIR();
  sensor.nextSample();
  return true;
}

SensorStatus finish(SensorStatus status) {
  sensor.shutDown();
  return status;
}

}  // namespace

void max30102_begin() {
  // begin() verifica la respuesta I2C y el PART_ID (0x15).
  present = sensor.begin(Wire, I2C_SPEED_STANDARD);
}

SensorStatus max30102_read(SensorReading& out) {
  if (!present) {
    return SensorStatus::AUSENTE;
  }
  sensor.setup(kLedPower, kSampleAverage, kLedModeRedIr, kSampleRate, kPulseWidth, kAdcRange);

  uint32_t red, ir, ir_sum = 0;
  for (uint8_t i = 0; i < kFingerSamples; ++i) {
    if (!next_sample(red, ir)) {
      Serial.println("[sensor] MAX30102 dejó de entregar muestras: revisar SDA/SCL");
      return finish(SensorStatus::FALLA);
    }
    ir_sum += ir;
  }
  if (ir_sum / kFingerSamples < MAX30102_FINGER_IR) {
    Serial.println("[sensor] MAX30102 sin dedo: no se mide");
    return finish(SensorStatus::OK);
  }

  for (uint8_t attempt = 0; attempt < MAX30102_ATTEMPTS; ++attempt) {
    const uint8_t skip = attempt == 0 ? kSettleSamples : 0;
    for (uint16_t i = 0; i < skip + BUFFER_SIZE; ++i) {
      if (!next_sample(red, ir)) {
        Serial.println("[sensor] MAX30102 dejó de entregar muestras: revisar SDA/SCL");
        return finish(SensorStatus::FALLA);
      }
      if (i >= skip) {
        red_buf[i - skip] = red;
        ir_buf[i - skip] = ir;
      }
    }
    int32_t spo2, bpm;
    int8_t spo2_ok, bpm_ok;
    maxim_heart_rate_and_oxygen_saturation(ir_buf, BUFFER_SIZE, red_buf, &spo2, &spo2_ok, &bpm,
                                           &bpm_ok);
    if (bpm_ok && spo2_ok && bpm >= 30 && bpm <= 220 && spo2 >= 70 && spo2 <= 100) {
      out.set(VAR_FRECUENCIA_CARDIACA, static_cast<float>(bpm));
      out.set(VAR_SPO2, static_cast<float>(spo2));
      return finish(SensorStatus::OK);
    }
  }
  // Dedo puesto pero la señal no dio un pulso claro (movimiento, presión).
  Serial.println("[sensor] MAX30102 señal inestable: no se reporta esta captura");
  return finish(SensorStatus::OK);
}
#endif
