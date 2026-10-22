# esp32/ — firmware del nodo sensor

Firmware de cada nodo sensor con su propio SX1276 (Fase 1). Captura sensores
y transmite por LoRa al gateway (la Raspberry) siguiendo
`../docs/PROTOCOLO_LORA.md`. Nunca habla MQTT.

Hay dos placas soportadas (la carpeta conserva el nombre `esp32/`):

| Env | Placa | Sensores |
|---|---|---|
| `nodemcuv2` (por defecto) | NodeMCU/Wemos con ESP-12E (ESP8266) | DHT22/AM2302 + DS18B20 |
| `esp32dev` | ESP32 DevKit | simulados (`SENSOR_SIMULADO`) |

## Ciclo

1. Despierta del deep sleep cada `frecuencia_captura` y captura (memoria RTC).
2. Cada `intervalo_transmision` (o al llenar `MAX_SAMPLES`) transmite una
   TELEMETRIA por captura, con `age_s`, y un ESTADO al final.
3. Abre una ventana RX de 1.5 s. Si llega un CONFIG, lo valida, lo guarda en
   flash y responde ACK_CONFIG.
4. Duerme hasta la próxima captura. El ESP8266 duerme como máximo ~3.5 h
   seguidas: los períodos más largos se encadenan en tramos.

Al encenderse transmite enseguida: así la Raspberry conoce al nodo y le envía
la config vigente sin esperar un intervalo completo.

## Cableado del nodo ESP8266 (NodeMCU)

**Todo a 3V3, nunca 5 V. Nunca transmitir sin la antena de 915 MHz puesta.**

| Componente | Pin del componente | NodeMCU | GPIO |
|---|---|---|---|
| SX1276 / RFM95 | 3.3V | 3V3 | — |
| | GND | GND | — |
| | SCK | D5 | 14 |
| | MISO | D6 | 12 |
| | MOSI | D7 | 13 |
| | NSS / CS | D8 | 15 |
| | RESET | sin conectar | — |
| | DIO0 | sin conectar | — |
| DHT22 / AM2302 | DATA (pin 2) | D1 | 5 |
| DS18B20 | DATA | D2 | 4 |
| Deep sleep | — | D0 ↔ RST (cable) | 16 |

- **DHT22 y DS18B20 ya no van en D5**, como en los sketches de diagnóstico:
  D5, D6 y D7 son el SPI por hardware del ESP8266 y los usa el SX1276. Cada
  sensor necesita su pull-up de 4.7k entre DATA y 3V3; los módulos de 3 pines
  del DHT22 ya la traen.
- **D0 ↔ RST** es obligatorio: así el ESP8266 despierta del deep sleep. Sin
  ese cable, el nodo transmite una vez y no vuelve a despertar. Si falla la
  carga por USB, desconectarlo mientras se flashea.
- **NSS en D8**: GPIO15 debe estar en bajo al arrancar (el NodeMCU trae un
  pull-down). Si el módulo LoRa trae una pull-up en NSS, el ESP8266 no
  arranca: retirarla.
- **RESET y DIO0 sin conectar**: el firmware consulta el SX1276 por SPI y no
  necesita interrupción. Los únicos pines libres que quedan (D3, D4) son de
  arranque, y el SX1276 baja su RESET al encenderse, así que puede dejar al
  ESP8266 en modo de carga. El RESET del SX1276 puede quedar al aire.
- Quedan libres D3, D4, RX/TX (serial de logs) y A0 (batería, ver abajo).

## Compilar, flashear, probar

```bash
pip install platformio
pio run -e nodemcuv2                  # compilar
pio run -e nodemcuv2 -t upload        # flashear
pio device monitor                    # logs por serial (115200)
pio test -e native                    # tests del codec (requiere gcc/g++)
```

Salida esperada en el monitor al encender:

```
[boot] nodo 0x0001 fw 0.1 (encendido)
[sensor] code 1 = 24.30
[sensor] code 2 = 58.10
[sensor] code 3 = 24.31
[lora] ráfaga: 1 capturas + ESTADO
[sleep] 599 s
```

Si aparece `[lora] el SX1276 no responde`, revisar el cableado SPI y NSS.

## Configuración por nodo (`platformio.ini` → `build_flags`)

| Flag | Obligatorio | Descripción |
|---|---|---|
| `NODE_ID` | sí | 1–65534, único en el sitio; mapearlo en `EDGE_LORA_NODES` |
| `NET_ID` | sí | igual a `EDGE_LORA_NET_ID` de la Raspberry |
| `LORA_FREQ_HZ`, `LORA_TX_POWER_DBM` | sí | **[confirmar]** con la regulación de la ANE |
| `LORA_SF`, `LORA_BW_HZ`, `LORA_CR`, `LORA_SYNC_WORD` | no (9, 125000, 5, 0x12) | deben coincidir con la Raspberry |
| `PIN_LORA_*`, `PIN_DHT22`, `PIN_DS18B20` | no | por defecto, la tabla de cableado de arriba (ESP32: ver `include/config.h`) |
| `SENSOR_DHT22`, `SENSOR_DS18B20`, `SENSOR_SIMULADO` | al menos uno | sensores presentes; el simulado no se combina con los reales |
| `SENSOR_CODE_*` | no (1, 2, 3) | codes de `EDGE_LORA_VARIABLES` (abajo) |
| `DS18B20_RESOLUTION` | no (12) | 9–12 bits; 11 bits reduce a la mitad el tiempo despierto |
| `DEFAULT_FRECUENCIA_CAPTURA_MIN`, `DEFAULT_INTERVALO_TRANSMISION_MIN` | no (10, 15) | config hasta el primer CONFIG recibido |
| `MAX_SAMPLES` | no (ESP8266 12, ESP32 32) | capturas pendientes en memoria RTC |
| `BATTERY_ADC_PIN`, `BATTERY_DIVIDER` | no (−1, 2.0) | −1 = sin medición de batería; en el ESP8266, `A0` |

### Codes de variable

| Code | Sensor | `EDGE_LORA_VARIABLES` |
|---|---|---|
| 1 | DHT22 temperatura | `1=temperatura_ambiente:C` |
| 2 | DHT22 humedad | `2=humedad_relativa:%` |
| 3 | DS18B20 temperatura | `3=<nombre en BD>:C` |

Si un sensor falla (DHT22 sin respuesta, DS18B20 desconectado o en 85 °C), se
omite su medición y el ESTADO lleva `FLAG_ERROR_SENSOR`.

## Estructura

```
platformio.ini
include/config.h            # flags de compilación, pines y valores por defecto
lib/sgpmp_protocol/         # codec de tramas (C++ puro, compartido con los tests)
src/main.cpp                # ciclo captura → ráfaga → ventana RX → deep sleep
src/platform.*              # deep sleep, memoria RTC y WiFi de cada placa
src/sensors.*               # DHT22, DS18B20 o simulado
src/node_config.*           # config persistida en flash (NVS / EEPROM)
src/battery.*               # nivel de batería por ADC
test/test_protocol/         # vectores de PROTOCOLO_LORA.md §9
```

## Pendiente

- Verificar en hardware real (docs/PRUEBAS_CAMPO.md): la ventana RX tras
  `endPacket()` y el consumo en deep sleep (el NodeMCU tiene regulador y
  USB-serial que consumen aunque el ESP8266 duerma).
