# esp32/ — firmware del nodo sensor

Firmware de cada ESP32 con su propio SX1276 (Fase 1). Captura sensores y
transmite por LoRa al gateway (la Raspberry) siguiendo
`../docs/PROTOCOLO_LORA.md`. Nunca habla MQTT.

## Ciclo

1. Despierta del deep sleep cada `frecuencia_captura` y captura (memoria RTC).
2. Cada `intervalo_transmision` (o al llenar `MAX_SAMPLES`) transmite una
   TELEMETRIA por captura, con `age_s`, y un ESTADO al final.
3. Abre una ventana RX de 1.5 s. Si llega un CONFIG, lo valida, lo guarda en
   NVS y responde ACK_CONFIG.
4. Duerme hasta la próxima captura.

Al encenderse transmite enseguida: así la Raspberry conoce al nodo y le envía
la config vigente sin esperar un intervalo completo.

## Compilar, flashear, probar

```bash
pip install platformio
pio run -e esp32dev                  # compilar
pio run -e esp32dev -t upload        # flashear
pio device monitor                   # logs por serial (115200)
pio test -e native                   # tests del codec (requiere gcc/g++)
```

## Configuración por nodo (`platformio.ini` → `build_flags`)

| Flag | Obligatorio | Descripción |
|---|---|---|
| `NODE_ID` | sí | 1–65534, único en el sitio; mapearlo en `EDGE_LORA_NODES` |
| `NET_ID` | sí | igual a `EDGE_LORA_NET_ID` de la Raspberry |
| `LORA_FREQ_HZ`, `LORA_TX_POWER_DBM` | sí | **[confirmar]** con la regulación de la ANE |
| `LORA_SF`, `LORA_BW_HZ`, `LORA_CR`, `LORA_SYNC_WORD` | no (9, 125000, 5, 0x12) | deben coincidir con la Raspberry |
| `PIN_LORA_*` | no | por defecto, ESP32 DevKit + RFM95 por VSPI (SCK 18, MISO 19, MOSI 23, SS 5, RST 14, DIO0 26) |
| `DEFAULT_FRECUENCIA_CAPTURA_MIN`, `DEFAULT_INTERVALO_TRANSMISION_MIN` | no (10, 15) | config hasta el primer CONFIG recibido |
| `BATTERY_ADC_PIN`, `BATTERY_DIVIDER` | no (−1, 2.0) | −1 = sin medición de batería |
| `SENSOR_SIMULADO` | hasta definir sensores | temperatura interna del ESP32 + humedad sintética |

## Estructura

```
platformio.ini
include/config.h            # flags de compilación y valores por defecto
lib/sgpmp_protocol/         # codec de tramas (C++ puro, compartido con los tests)
src/main.cpp                # ciclo captura → ráfaga → ventana RX → deep sleep
src/sensors.*               # lectura de sensores (simulado por ahora)
src/node_config.*           # config persistida en NVS
src/battery.*               # nivel de batería por ADC
test/test_protocol/         # vectores de PROTOCOLO_LORA.md §9
```

## Pendiente

- Drivers de los sensores reales y su tabla de `code` (hoy `SENSOR_SIMULADO`).
- Verificar en hardware real (docs/PRUEBAS_CAMPO.md): la ventana RX tras
  `endPacket()` y el consumo en deep sleep de la placa elegida.
