#include "node_config.h"

#include "config.h"

#if defined(ESP8266)
#include <EEPROM.h>
#include <stddef.h>
#include <string.h>

#include "sgpmp_protocol.h"
#else
#include <Preferences.h>
#endif

namespace {
constexpr NodeConfig kDefaultConfig{0, DEFAULT_FRECUENCIA_CAPTURA_MIN,
                                    DEFAULT_INTERVALO_TRANSMISION_MIN};
}

bool node_config_valid(uint16_t frecuencia_min, uint16_t intervalo_min) {
  return frecuencia_min >= CONFIG_MIN_MIN && frecuencia_min <= CONFIG_MAX_MIN &&
         intervalo_min >= CONFIG_MIN_MIN && intervalo_min <= CONFIG_MAX_MIN;
}

#if defined(ESP8266)
// El ESP8266 no tiene NVS: EEPROM emulada en un sector de flash, con magic y
// CRC para distinguir lo guardado de un sector borrado (0xFF). Config y
// contadores van en el mismo bloque: cada escritura relee y conserva el otro.
namespace {

constexpr uint32_t kMagic = 0x53474332;  // "SGC2": config + contadores v2

struct Stored {
  uint32_t magic;
  NodeConfig cfg;
  NodeCounters counters;
  uint16_t crc;
};

uint16_t stored_crc(const Stored& s) {
  return sgpmp::crc16_ccitt(reinterpret_cast<const uint8_t*>(&s.cfg),
                            offsetof(Stored, crc) - offsetof(Stored, cfg));
}

Stored load() {
  Stored s;
  EEPROM.begin(sizeof(Stored));
  EEPROM.get(0, s);
  EEPROM.end();
  if (s.magic != kMagic || s.crc != stored_crc(s)) {
    s = {kMagic, kDefaultConfig, {0, 0}, 0};
  }
  return s;
}

// Se arma desde cero: el relleno entre campos entra al CRC y debe ser fijo.
void save(const NodeConfig& cfg, const NodeCounters& counters) {
  Stored s;
  memset(&s, 0, sizeof(s));
  s.magic = kMagic;
  s.cfg = cfg;
  s.counters = counters;
  s.crc = stored_crc(s);
  EEPROM.begin(sizeof(Stored));
  EEPROM.put(0, s);
  EEPROM.end();  // escribe en flash solo si cambió algo
}

}  // namespace

NodeConfig node_config_load() {
  const NodeConfig cfg = load().cfg;
  return node_config_valid(cfg.frecuencia_captura_min, cfg.intervalo_transmision_min)
             ? cfg
             : kDefaultConfig;
}

void node_config_save(const NodeConfig& cfg) { save(cfg, load().counters); }

NodeCounters node_counters_load() { return load().counters; }

void node_counters_save(const NodeCounters& counters) { save(load().cfg, counters); }

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
  return node_config_valid(cfg.frecuencia_captura_min, cfg.intervalo_transmision_min)
             ? cfg
             : kDefaultConfig;
}

void node_config_save(const NodeConfig& cfg) {
  Preferences prefs;
  prefs.begin(kNamespace, false);
  prefs.putUShort("ver", cfg.cfg_version);
  prefs.putUShort("frec", cfg.frecuencia_captura_min);
  prefs.putUShort("int", cfg.intervalo_transmision_min);
  prefs.end();
}

NodeCounters node_counters_load() {
  Preferences prefs;
  prefs.begin(kNamespace, true);
  const NodeCounters counters{prefs.getUInt("fres", 0), prefs.getUInt("fdn", 0)};
  prefs.end();
  return counters;
}

void node_counters_save(const NodeCounters& counters) {
  Preferences prefs;
  prefs.begin(kNamespace, false);
  prefs.putUInt("fres", counters.fcnt_reservado);
  prefs.putUInt("fdn", counters.fcnt_bajada);
  prefs.end();
}
#endif
