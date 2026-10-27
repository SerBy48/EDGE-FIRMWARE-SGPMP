// MPU6050 (y compatibles: MPU6500, MPU9250) por I2C, sin librería, como el
// sketch de diagnóstico. El nodo duerme entre capturas, así que no vigila en
// continuo: al despertar muestrea una ventana corta y resume
//   actividad  RMS de la aceleración sin gravedad (0 g = quieto)
//   pico       máximo del módulo de la aceleración (golpes, caídas)
//   pitch/roll inclinación promedio (postura: de pie, acostado...)
// Entre capturas el chip queda en sleep (~5 µA).
#if defined(SENSOR_MPU6050)
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#include "config.h"
#include "sensor_drivers.h"

namespace {

constexpr uint8_t kRegSmplrtDiv = 0x19;
constexpr uint8_t kRegConfig = 0x1A;
constexpr uint8_t kRegAccelConfig = 0x1C;
constexpr uint8_t kRegAccelXoutH = 0x3B;
constexpr uint8_t kRegPwrMgmt1 = 0x6B;
constexpr uint8_t kRegPwrMgmt2 = 0x6C;
constexpr uint8_t kRegWhoAmI = 0x75;

// ±8 g: a ±2 g un golpe satura y el pico queda recortado.
constexpr uint8_t kAccelFs8g = 0x10;
constexpr float kLsbPerG = 4096.0f;
constexpr uint32_t kSamplePeriodMs = 20;  // 50 Hz

uint8_t addr = 0;  // 0 = no detectado

bool write_reg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool read_regs(uint8_t reg, uint8_t* buf, uint8_t len) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(addr, len) != len) {
    return false;
  }
  for (uint8_t i = 0; i < len; ++i) {
    buf[i] = static_cast<uint8_t>(Wire.read());
  }
  return true;
}

// WHO_AM_I de los chips con el mismo mapa de registros de acelerómetro.
bool known_id(uint8_t id) {
  return id == 0x68 || id == 0x70 || id == 0x71 || id == 0x73 || id == 0x98;
}

SensorStatus fail(const char* hint) {
  Serial.printf("[sensor] MPU6050 en 0x%02x: %s\n", addr, hint);
  write_reg(kRegPwrMgmt1, 0x40);  // sleep
  return SensorStatus::FALLA;
}

}  // namespace

void mpu6050_begin() {
  for (uint8_t candidate : {0x68, 0x69}) {  // AD0 a GND o a 3V3
    addr = candidate;
    uint8_t id = 0;
    if (read_regs(kRegWhoAmI, &id, 1) && known_id(id)) {
      break;
    }
    addr = 0;
  }
  if (addr == 0) {
    return;
  }
  write_reg(kRegPwrMgmt1, 0x80);  // reset
  delay(100);
  write_reg(kRegPwrMgmt1, 0x00);  // despierto, oscilador interno
  write_reg(kRegPwrMgmt2, 0x07);  // giroscopio en standby: no se usa
  write_reg(kRegConfig, 0x03);    // filtro pasa bajos de 44 Hz
  write_reg(kRegSmplrtDiv, 19);   // 1 kHz / (1 + 19) = 50 Hz
  write_reg(kRegAccelConfig, kAccelFs8g);
}

SensorStatus mpu6050_read(SensorReading& out) {
  if (addr == 0) {
    return SensorStatus::AUSENTE;
  }
  const uint32_t n = MPU6050_WINDOW_MS / kSamplePeriodMs;
  float sum[3] = {}, sum_sq[3] = {};
  float peak = 0;
  uint32_t ok = 0, frozen = 0;
  int16_t prev_x = 0;
  uint32_t next = millis();
  for (uint32_t i = 0; i < n; ++i) {
    while (static_cast<int32_t>(millis() - next) < 0) {
      yield();
    }
    next += kSamplePeriodMs;
    uint8_t b[6];
    if (!read_regs(kRegAccelXoutH, b, sizeof(b))) {
      continue;
    }
    float a[3];
    for (int axis = 0; axis < 3; ++axis) {
      const int16_t raw = static_cast<int16_t>((b[2 * axis] << 8) | b[2 * axis + 1]);
      a[axis] = raw / kLsbPerG;
      sum[axis] += a[axis];
      sum_sq[axis] += a[axis] * a[axis];
      if (axis == 0) {
        frozen += ok > 0 && raw == prev_x;
        prev_x = raw;
      }
    }
    peak = fmaxf(peak, sqrtf(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]));
    ++ok;
  }
  write_reg(kRegPwrMgmt1, 0x40);  // sleep hasta la próxima captura

  if (ok < n / 2) {
    return fail("más de la mitad de las lecturas I2C fallaron: revisar SDA/SCL");
  }
  if (frozen + 1 >= ok) {
    return fail("el valor no cambia: sensor congelado");  // un MEMS real siempre tiembla
  }
  float mean[3], var = 0;
  for (int axis = 0; axis < 3; ++axis) {
    mean[axis] = sum[axis] / ok;
    var += sum_sq[axis] / ok - mean[axis] * mean[axis];
  }
  const float gravity = sqrtf(mean[0] * mean[0] + mean[1] * mean[1] + mean[2] * mean[2]);
  if (gravity < 0.5f || gravity > 1.5f) {
    return fail("la gravedad promedio no da ~1 g: sensor dañado");
  }
  constexpr float kDeg = 180.0f / static_cast<float>(M_PI);
  out.set(VAR_ACTIVIDAD, sqrtf(fmaxf(var, 0.0f)));
  out.set(VAR_ACELERACION_PICO, peak);
  out.set(VAR_INCLINACION_PITCH,
          atan2f(-mean[0], sqrtf(mean[1] * mean[1] + mean[2] * mean[2])) * kDeg);
  out.set(VAR_INCLINACION_ROLL, atan2f(mean[1], mean[2]) * kDeg);
  return SensorStatus::OK;
}
#endif
