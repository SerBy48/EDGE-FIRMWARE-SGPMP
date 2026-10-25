// AES-128 y AES-CCM para el protocolo LoRa v2 (docs/PROTOCOLO_LORA.md §11).
//
// C++ puro, sin dependencias de la placa: el mismo código corre en el
// ESP8266/ESP32 y en los tests del PC (env native), que lo validan contra los
// vectores oficiales (FIPS-197, NIST SP 800-38C) y contra la librería
// `cryptography` de la Raspberry. Solo cifra bloques: CCM no usa el
// descifrado de AES.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace sgpmp {

constexpr size_t AES_BLOCK = 16;
constexpr size_t AES_KEY_LEN = 16;

class Aes128 {
 public:
  explicit Aes128(const uint8_t key[AES_KEY_LEN]);
  void encrypt_block(const uint8_t in[AES_BLOCK], uint8_t out[AES_BLOCK]) const;

 private:
  uint8_t round_keys_[176];
};

// AES-CCM (NIST SP 800-38C). `nonce_len` 7-13 (L = 15 - nonce_len), `tag_len`
// 4-16 y par, `aad_len` < 0xFF00. Cifra `len` bytes de `in` en `out` (pueden
// ser el mismo buffer) y escribe la etiqueta en `tag`.
bool ccm_encrypt(const Aes128& aes, const uint8_t* nonce, size_t nonce_len, const uint8_t* aad,
                 size_t aad_len, const uint8_t* in, size_t len, uint8_t* out, uint8_t* tag,
                 size_t tag_len);

// Descifra y verifica en tiempo constante. Si la etiqueta no coincide
// devuelve false y deja `out` en cero: nunca se usa un texto no autenticado.
bool ccm_decrypt(const Aes128& aes, const uint8_t* nonce, size_t nonce_len, const uint8_t* aad,
                 size_t aad_len, const uint8_t* in, size_t len, uint8_t* out, const uint8_t* tag,
                 size_t tag_len);

// Huella de una clave (key check value): los primeros 4 bytes de cifrar un
// bloque de ceros. Permite comprobar que nodo y Raspberry tienen la misma
// clave sin mostrarla.
uint32_t key_check_value(const uint8_t key[AES_KEY_LEN]);

}  // namespace sgpmp
