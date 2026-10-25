// DHT22/AM2302 con driver propio, como el sketch de diagnóstico: distingue
// un sensor que no contesta al inicio (ausente) de uno que contesta y se
// corta o llega con checksum malo (falla). La librería de Adafruit solo
// devuelve NaN en ambos casos.
#if defined(SENSOR_DHT22)
#include <Arduino.h>

#include "config.h"
#include "sensor_drivers.h"

namespace {

// El datasheet pide 1-2 s tras alimentarlo antes de la primera lectura.
constexpr uint32_t kWarmupMs = 2000;
constexpr uint32_t kTimeoutUs = 100;  // ninguna fase del protocolo pasa de 80 µs
bool cold_boot = false;

// Espera mientras la línea esté en `level`. Devuelve los µs que duró, o -1
// si pasó kTimeoutUs.
int32_t hold_time(int level) {
  const uint32_t t0 = micros();
  while (digitalRead(PIN_DHT22) == level) {
    if (micros() - t0 > kTimeoutUs) {
      return -1;
    }
  }
  return static_cast<int32_t>(micros() - t0);
}

SensorStatus fail(const char* hint) {
  Serial.printf("[sensor] DHT22 en GPIO%d: %s\n", PIN_DHT22, hint);
  return SensorStatus::FALLA;
}

}  // namespace

void dht22_begin(bool cold) {
  cold_boot = cold;
  pinMode(PIN_DHT22, INPUT_PULLUP);
}

SensorStatus dht22_read(SensorReading& out) {
  if (cold_boot) {
    while (millis() < kWarmupMs) {
      delay(10);
    }
  }
  // En reposo la pull-up deja la línea en alto; en bajo hay un corto o el
  // sensor quedó colgado.
  if (digitalRead(PIN_DHT22) == LOW) {
    return fail("la línea está en bajo en reposo: DATA en corto a GND");
  }

  // Inicio: el host baja la línea >= 1 ms y la suelta.
  pinMode(PIN_DHT22, OUTPUT);
  digitalWrite(PIN_DHT22, LOW);
  delayMicroseconds(1100);

  uint8_t data[5] = {};
  bool responded;
  int bits = 0;
  noInterrupts();  // ~5 ms: los tiempos de cada bit son de decenas de µs
  pinMode(PIN_DHT22, INPUT_PULLUP);
  // Respuesta: el sensor baja la línea 20-40 µs después de soltarla y la
  // mantiene 80 µs en bajo y 80 µs en alto. A los 55 µs (como la librería de
  // Adafruit) ya está en bajo si hay sensor; esperar a que la línea suba
  // antes desfasaría la lectura si la pull-up tarda en levantarla.
  delayMicroseconds(55);
  responded = digitalRead(PIN_DHT22) == LOW && hold_time(LOW) >= 0 && hold_time(HIGH) >= 0;
  // Cada bit: ~50 µs en bajo y luego en alto 26-28 µs (0) o ~70 µs (1).
  while (responded && bits < 40) {
    if (hold_time(LOW) < 0) {
      break;
    }
    const int32_t high = hold_time(HIGH);
    if (high < 0) {
      break;
    }
    data[bits / 8] = static_cast<uint8_t>((data[bits / 8] << 1) | (high > 40 ? 1 : 0));
    ++bits;
  }
  interrupts();

  if (!responded) {
    return SensorStatus::AUSENTE;  // nadie contestó al inicio
  }
  if (bits < 40) {
    return fail("la respuesta se cortó a mitad: contacto flojo o pull-up débil");
  }
  if ((data[0] | data[1] | data[2] | data[3] | data[4]) == 0) {
    return fail("solo llegaron ceros: la línea no sube, revisar pull-up");  // checksum 0 = 0
  }
  if (static_cast<uint8_t>(data[0] + data[1] + data[2] + data[3]) != data[4]) {
    return fail("checksum inválido: ruido en el cable o pull-up débil");
  }
  const float humedad = ((data[0] << 8) | data[1]) / 10.0f;
  float temperatura = (((data[2] & 0x7F) << 8) | data[3]) / 10.0f;
  if (data[2] & 0x80) {
    temperatura = -temperatura;
  }
  if (humedad > 100.0f || temperatura < -40.0f || temperatura > 80.0f) {
    return fail("valores fuera del rango físico del sensor");
  }
  out.set(VAR_TEMPERATURA, temperatura);
  out.set(VAR_HUMEDAD, humedad);
  return SensorStatus::OK;
}
#endif
