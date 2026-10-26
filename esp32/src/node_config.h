// Config del nodo y contadores del protocolo v2, persistidos en flash (NVS en
// el ESP32, EEPROM emulada en el ESP8266): sobreviven cortes de energía (la
// memoria RTC solo sobrevive al deep sleep).
#pragma once

#include <stdint.h>

struct NodeConfig {
  uint16_t cfg_version;
  uint16_t frecuencia_captura_min;
  uint16_t intervalo_transmision_min;
};

// Contadores de tramas v2 (docs/PROTOCOLO_LORA.md §11). Con la misma clave un
// nonce nunca se repite, así que no pueden volver atrás tras un corte.
struct NodeCounters {
  // Primer fcnt de subida todavía no reservado: al encender se arranca desde
  // acá. Se reservan bloques para no escribir la flash en cada trama.
  uint32_t fcnt_reservado;
  // fcnt del último CONFIG aceptado: uno repetido o más viejo se ignora.
  uint32_t fcnt_bajada;
};

NodeConfig node_config_load();
void node_config_save(const NodeConfig& cfg);
bool node_config_valid(uint16_t frecuencia_min, uint16_t intervalo_min);

NodeCounters node_counters_load();
void node_counters_save(const NodeCounters& counters);
