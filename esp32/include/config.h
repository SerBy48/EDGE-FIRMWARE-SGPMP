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
#ifndef LORA_RX_WINDOW_MS
#define LORA_RX_WINDOW_MS 1500
#endif

// --- Pines (ESP32 DevKit + RFM95 por VSPI) --------------------------------
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
// transmite aunque no se haya cumplido intervalo_transmision.
#ifndef MAX_SAMPLES
#define MAX_SAMPLES 32
#endif

// --- Batería -------------------------------------------------------------
// -1 = sin medición (se reporta 0xFF, "desconocida").
#ifndef BATTERY_ADC_PIN
#define BATTERY_ADC_PIN -1
#endif
#ifndef BATTERY_DIVIDER
#define BATTERY_DIVIDER 2.0f
#endif
#ifndef BATTERY_EMPTY_MV
#define BATTERY_EMPTY_MV 3300
#endif
#ifndef BATTERY_FULL_MV
#define BATTERY_FULL_MV 4200
#endif
