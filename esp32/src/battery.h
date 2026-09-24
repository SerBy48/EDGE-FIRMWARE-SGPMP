#pragma once

#include <stdint.h>

// Porcentaje 0-100 (lineal entre BATTERY_EMPTY_MV y BATTERY_FULL_MV), o
// sgpmp::BATERIA_DESCONOCIDA si no hay pin de medición configurado.
uint8_t battery_percent();
