// Lo que cambia entre ESP32 y ESP8266: deep sleep, memoria RTC y radio WiFi.
// main.cpp solo usa estas funciones.
#pragma once

#include <stddef.h>
#include <stdint.h>

// Apaga lo que el nodo no usa (WiFi en el ESP8266).
void platform_begin();

// true si este arranque viene de un deep sleep por timer (no de un
// encendido, un reset o un crash).
bool platform_woke_from_sleep();

// Copia el estado guardado antes del último deep sleep. Solo tiene sentido
// si platform_woke_from_sleep(); quien llama valida su contenido.
void platform_rtc_load(void* data, size_t len);
void platform_rtc_save(const void* data, size_t len);
#if defined(ESP8266)
constexpr size_t PLATFORM_RTC_MAX_LEN = 512;  // toda la memoria RTC de usuario
#else
constexpr size_t PLATFORM_RTC_MAX_LEN = 2048;
#endif

// Deep sleep más largo que admite la placa en un solo tramo.
uint64_t platform_max_sleep_ms();

[[noreturn]] void platform_deep_sleep(uint64_t ms);

// Prepara el bus SPI del SX1276 (el ESP8266 usa los pines fijos del HSPI).
void platform_spi_begin();
