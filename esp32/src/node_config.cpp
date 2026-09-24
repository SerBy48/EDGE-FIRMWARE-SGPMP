#include "node_config.h"

#include <Preferences.h>

#include "config.h"

namespace {
constexpr const char* kNamespace = "sgpmp";
}

bool node_config_valid(uint16_t frecuencia_min, uint16_t intervalo_min) {
  return frecuencia_min >= CONFIG_MIN_MIN && frecuencia_min <= CONFIG_MAX_MIN &&
         intervalo_min >= CONFIG_MIN_MIN && intervalo_min <= CONFIG_MAX_MIN;
}

NodeConfig node_config_load() {
  Preferences prefs;
  prefs.begin(kNamespace, true);
  NodeConfig cfg{
      prefs.getUShort("ver", 0),
      prefs.getUShort("frec", DEFAULT_FRECUENCIA_CAPTURA_MIN),
      prefs.getUShort("int", DEFAULT_INTERVALO_TRANSMISION_MIN),
  };
  prefs.end();
  if (!node_config_valid(cfg.frecuencia_captura_min, cfg.intervalo_transmision_min)) {
    cfg = {0, DEFAULT_FRECUENCIA_CAPTURA_MIN, DEFAULT_INTERVALO_TRANSMISION_MIN};
  }
  return cfg;
}

void node_config_save(const NodeConfig& cfg) {
  Preferences prefs;
  prefs.begin(kNamespace, false);
  prefs.putUShort("ver", cfg.cfg_version);
  prefs.putUShort("frec", cfg.frecuencia_captura_min);
  prefs.putUShort("int", cfg.intervalo_transmision_min);
  prefs.end();
}
