// Configuración de compilación del nodo. Todo se puede sobreescribir con
// build_flags en platformio.ini (-DNOMBRE=valor).
#pragma once

// --- Identidad -----------------------------------------------------------
#ifndef NODE_ID
#error "Definir NODE_ID (1-65534), único por nodo del sitio"
#endif
#ifndef NET_ID
#error "Definir NET_ID: debe coincidir con EDGE_LORA_NET_ID de la Raspberry"
#endif

#define FW_MAJOR 0
#define FW_MINOR 1

// --- Radio (docs/PROTOCOLO_LORA.md §2) -----------------------------------
// Frecuencia y potencia sin default a propósito: dependen de la regulación.
#ifndef LORA_FREQ_HZ
#error "Definir LORA_FREQ_HZ según la regulación de la ANE"
#endif
#ifndef LORA_TX_POWER_DBM
#error "Definir LORA_TX_POWER_DBM según la regulación de la ANE"
#endif
#ifndef LORA_SF
#define LORA_SF 9
#endif
#ifndef LORA_BW_HZ
#define LORA_BW_HZ 125000
#endif
#ifndef LORA_CR
#define LORA_CR 5
#endif
#ifndef LORA_SYNC_WORD
#define LORA_SYNC_WORD 0x12
#endif
// Reloj SPI del SX1276. La librería usa 8 MHz por defecto, que falla con
// cables dupont; para tramas de 51 bytes 1 MHz no cambia el tiempo despierto.
#ifndef LORA_SPI_HZ
#define LORA_SPI_HZ 1000000
#endif
#ifndef LORA_RX_WINDOW_MS
#define LORA_RX_WINDOW_MS 1500
#endif

// --- Pines ----------------------------------------------------------------
// -1 = no conectado. DIO0 no hace falta: el firmware consulta el SX1276 por
// SPI (parsePacket/endPacket), no por interrupción.
#if defined(ESP8266)
// NodeMCU/Wemos (ESP-12E). El SPI por hardware es fijo: SCK D5 (GPIO14),
// MISO D6 (GPIO12), MOSI D7 (GPIO13). NSS en D8 (GPIO15), que debe estar en
// bajo al arrancar: no ponerle pull-up. RESET sin conectar por defecto: los
// pines libres que quedan (D3, D4) son de arranque y el SX1276 baja su
// NRESET durante su power-on reset.
#ifndef PIN_LORA_SS
#define PIN_LORA_SS 15
#endif
#ifndef PIN_LORA_RST
#define PIN_LORA_RST -1
#endif
#ifndef PIN_LORA_DIO0
#define PIN_LORA_DIO0 -1
#endif
// Datos de los sensores: D1 (GPIO5) y D2 (GPIO4), cada uno con pull-up de
// 4.7k a 3V3. D5 (el pin de los sketches de diagnóstico) es el SCK del SX1276.
#ifndef PIN_DHT22
#define PIN_DHT22 5
#endif
#ifndef PIN_DS18B20
#define PIN_DS18B20 4
#endif
#else
// ESP32 DevKit + RFM95 por VSPI.
#ifndef PIN_LORA_SCK
#define PIN_LORA_SCK 18
#endif
#ifndef PIN_LORA_MISO
#define PIN_LORA_MISO 19
#endif
#ifndef PIN_LORA_MOSI
#define PIN_LORA_MOSI 23
#endif
#ifndef PIN_LORA_SS
#define PIN_LORA_SS 5
#endif
#ifndef PIN_LORA_RST
#define PIN_LORA_RST 14
#endif
#ifndef PIN_LORA_DIO0
#define PIN_LORA_DIO0 26
#endif
#ifndef PIN_DHT22
#define PIN_DHT22 27
#endif
#ifndef PIN_DS18B20
#define PIN_DS18B20 25
#endif
#endif

// Resolución del DS18B20 (9-12 bits). 12 bits: paso 0.0625 C, 750 ms de
// conversión; 11 bits: 0.125 C en 375 ms, la mitad del tiempo despierto.
#ifndef DS18B20_RESOLUTION
#define DS18B20_RESOLUTION 12
#endif

// --- Config inicial hasta el primer downlink (minutos) --------------------
#ifndef DEFAULT_FRECUENCIA_CAPTURA_MIN
#define DEFAULT_FRECUENCIA_CAPTURA_MIN 10
#endif
#ifndef DEFAULT_INTERVALO_TRANSMISION_MIN
#define DEFAULT_INTERVALO_TRANSMISION_MIN 15
#endif
// Rango aceptado en un CONFIG; fuera de esto se responde INVALIDA.
#define CONFIG_MIN_MIN 1
#define CONFIG_MAX_MIN 1440

// Capturas guardadas en memoria RTC entre transmisiones. Si se llena, se
// transmite aunque no se haya cumplido intervalo_transmision. El ESP8266 solo
// tiene 512 bytes de memoria RTC de usuario.
#ifndef MAX_SAMPLES
#if defined(ESP8266)
#define MAX_SAMPLES 12
#else
#define MAX_SAMPLES 32
#endif
#endif

// --- Batería -------------------------------------------------------------
// -1 = sin medición (se reporta 0xFF, "desconocida").
#ifndef BATTERY_ADC_PIN
#define BATTERY_ADC_PIN -1
#endif
#ifndef BATTERY_DIVIDER
#define BATTERY_DIVIDER 2.0f
#endif
// ESP8266: mV que corresponden a la lectura 1023 en A0 (el NodeMCU ya trae un
// divisor 220k/100k: A0 llega a ~3.2 V). BATTERY_ADC_PIN debe ser A0 (17).
#ifndef BATTERY_ADC_FULL_SCALE_MV
#define BATTERY_ADC_FULL_SCALE_MV 3200
#endif
#ifndef BATTERY_EMPTY_MV
#define BATTERY_EMPTY_MV 3300
#endif
#ifndef BATTERY_FULL_MV
#define BATTERY_FULL_MV 4200
#endif
