// Config del nodo persistida en NVS: sobrevive cortes de energía (la memoria
// RTC solo sobrevive al deep sleep).
#pragma once

#include <stdint.h>

struct NodeConfig {
  uint16_t cfg_version;
  uint16_t frecuencia_captura_min;
  uint16_t intervalo_transmision_min;
};

NodeConfig node_config_load();
void node_config_save(const NodeConfig& cfg);
bool node_config_valid(uint16_t frecuencia_min, uint16_t intervalo_min);
