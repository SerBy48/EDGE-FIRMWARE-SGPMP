#include "node_config.h"

#include "config.h"

#if defined(ESP8266)
#include <EEPROM.h>

#include "sgpmp_protocol.h"
#else
#include <Preferences.h>
#endif

bool node_config_valid(uint16_t frecuencia_min, uint16_t intervalo_min) {
  return frecuencia_min >= CONFIG_MIN_MIN && frecuencia_min <= CONFIG_MAX_MIN &&
         intervalo_min >= CONFIG_MIN_MIN && intervalo_min <= CONFIG_MAX_MIN;
}

#if defined(ESP8266)
// El ESP8266 no tiene NVS: EEPROM emulada en un sector de flash, con magic y
// CRC para distinguir una config guardada de un sector borrado (0xFF).
namespace {

constexpr uint32_t kMagic = 0x53474331;  // "SGC1"

struct Stored {
  uint32_t magic;
  NodeConfig cfg;
  uint16_t crc;
};

uint16_t stored_crc(const Stored& s) {
  return sgpmp::crc16_ccitt(reinterpret_cast<const uint8_t*>(&s.cfg), sizeof(s.cfg));
}

}  // namespace

NodeConfig node_config_load() {
  Stored s;
  EEPROM.begin(sizeof(Stored));
  EEPROM.get(0, s);
  EEPROM.end();
  NodeConfig cfg = s.cfg;
  if (s.magic != kMagic || s.crc != stored_crc(s) ||
      !node_config_valid(cfg.frecuencia_captura_min, cfg.intervalo_transmision_min)) {
    cfg = {0, DEFAULT_FRECUENCIA_CAPTURA_MIN, DEFAULT_INTERVALO_TRANSMISION_MIN};
  }
  return cfg;
}

void node_config_save(const NodeConfig& cfg) {
  Stored s{kMagic, cfg, 0};
  s.crc = stored_crc(s);
  EEPROM.begin(sizeof(Stored));
  EEPROM.put(0, s);
  EEPROM.end();  // escribe en flash solo si cambió algo
}

#else
namespace {
constexpr const char* kNamespace = "sgpmp";
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
#endif
