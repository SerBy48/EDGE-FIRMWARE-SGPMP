# esp32/ — firmware del nodo sensor

Firmware de cada nodo sensor con su propio SX1276 (Fase 1). Captura sensores
y transmite por LoRa al gateway (la Raspberry) siguiendo
`../docs/PROTOCOLO_LORA.md`, con el protocolo v2: cada trama va cifrada y
autenticada con la clave propia del nodo (§11). Nunca habla MQTT.

Hay dos placas soportadas (la carpeta conserva el nombre `esp32/`):

| Env | Placa | Sensores |
|---|---|---|
| `nodemcuv2` (por defecto) | NodeMCU/Wemos con ESP-12E (ESP8266) | catálogo: DHT22, DS18B20, MPU6050, MAX30102 |
| `esp32dev` | ESP32 DevKit | simulados (`SENSOR_SIMULADO`) |

## Ciclo

1. Despierta del deep sleep cada `frecuencia_captura`, revisa el catálogo de
   sensores y captura (memoria RTC).
2. Cada `intervalo_transmision` (o al llenar `MAX_SAMPLES`) transmite una
   TELEMETRIA por captura, con `age_s`, y un ESTADO al final.
3. Abre una ventana RX de 1.5 s. Si llega un CONFIG, lo valida, lo guarda en
   flash y responde ACK_CONFIG.
4. Duerme hasta la próxima captura. El ESP8266 duerme como máximo ~3.5 h
   seguidas: los períodos más largos se encadenan en tramos.

Al encenderse transmite enseguida: así la Raspberry conoce al nodo y le envía
la config vigente sin esperar un intervalo completo.

## Catálogo de sensores

En cada captura se revisan todos los sensores del catálogo, estén o no
conectados. El mismo firmware sirve para un nodo ambiental (DHT22 + DS18B20)
y para uno de individuo (MPU6050 + MAX30102), o para cualquier combinación.

| Sensor | Estado | Qué hace el nodo |
|---|---|---|
| no conectado | ausente | no envía sus variables; no es un error |
| conectado y lee bien | detectado | envía sus variables |
| conectado pero falla | detectado + fallando | no envía sus variables, marca `FLAG_ERROR_SENSOR` y el log dice qué revisar |

El ESTADO de cada ráfaga lleva los sensores detectados y los que fallan
(`PROTOCOLO_LORA.md` §5). La Raspberry los registra en su log cuando
cambian, por ejemplo: `Nodo 0x0001 sensores detectados: DHT22, MPU6050;
fallando: ninguno`.

| Sensor | Variables | Tiempo despierto por captura |
|---|---|---|
| DHT22 / AM2302 | temperatura (°C), humedad (%) | ~5 ms (2 s tras encender) |
| DS18B20 | temperatura (°C) | 750 ms a 12 bits, en paralelo con el MPU6050 |
| MPU6050 | actividad (g), pico de aceleración (g), pitch y roll (°) | 2 s (`MPU6050_WINDOW_MS`) |
| MAX30102 | frecuencia cardíaca (BPM), SpO2 (%) | 0.5 s sin dedo; 5-9 s con el dedo apoyado |

- **MPU6050**: el nodo duerme entre capturas, así que no vigila en continuo
  (no detecta una caída en el momento): muestrea 2 s al despertar.
  *Actividad* es el RMS de la aceleración sin la gravedad (≈0 g quieto);
  *pico* es el máximo del módulo de la aceleración en la ventana (≈1 g
  quieto); *pitch/roll* es la inclinación promedio (postura).
- **MAX30102**: si no hay dedo apoyado no mide y no es un error. Si hay dedo
  pero la señal no da un pulso claro (movimiento), omite esa captura. El SpO2
  de estos módulos es orientativo, no clínico.
- **DS18B20**: se lee con SKIP_ROM (un sensor por nodo), así que funcionan
  también los clones con la serie mal grabada.

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
| MPU6050 y MAX30102 (I2C) | SDA | D2 | 4 |
| | SCL | D1 | 5 |
| DHT22 / AM2302 | DATA (pin 2) | D3 | 0 |
| DS18B20 | DATA | D4 | 2 |
| Deep sleep | — | D0 ↔ RST (cable) | 16 |

Todos los sensores a 3V3 y GND. El MPU6050 y el MAX30102 comparten el bus
I2C (direcciones 0x68 y 0x57).

- **I2C en D1/D2**, como en los sketches de diagnóstico. Los módulos traen
  sus propias pull-ups.
- **DHT22 en D3 y DS18B20 en D4**: son pines de arranque que deben estar en
  alto al encender, y la pull-up de 4.7k a 3V3 de cada sensor los deja así.
  Cada uno necesita su pull-up entre DATA y 3V3; los módulos de 3 pines ya la
  traen. No van en D5 como en los sketches: D5, D6 y D7 son el SPI del
  SX1276.
- **D0 ↔ RST** es obligatorio: así el ESP8266 despierta del deep sleep. Sin
  ese cable, el nodo transmite una vez y no vuelve a despertar. Si falla la
  carga por USB, desconectarlo mientras se flashea.
- **NSS en D8**: GPIO15 debe estar en bajo al arrancar (el NodeMCU trae un
  pull-down). Si el módulo LoRa trae una pull-up en NSS, el ESP8266 no
  arranca: retirarla.
- **RESET y DIO0 sin conectar**: el firmware consulta el SX1276 por SPI y no
  necesita interrupción. No quedan pines libres para RESET, y D3/D4 no
  servirían: el SX1276 baja su RESET al encenderse y dejaría al ESP8266 en
  modo de carga. El RESET del SX1276 puede quedar al aire.
- Solo quedan libres RX/TX (serial de logs) y A0 (batería, ver abajo).

## Identidad y clave del nodo (`secrets.ini`)

Cada nodo se identifica con el serial con que se registró en la plataforma.
Su `node_id` de radio y su clave AES los deriva la Raspberry de ese serial y
del secreto del sitio; nadie genera claves a mano. Para flashear un nodo:

1. En la Raspberry: `sudo /opt/sgpmp-edge/bin/lora-key <serial del nodo>`
   (el serial también va en `EDGE_SERIALS`, ver `INSTALACION_RASPBERRY.md` §9.3).
2. Copiar todo lo que imprime a `esp32/secrets.ini` en este PC, en UTF-8 o
   ASCII. PlatformIO lo incluye solo (`extra_configs`).
3. Flashear como siempre. Un archivo por nodo: para el siguiente, reemplazarlo
   con el suyo.

`secrets.ini` contiene la clave del nodo: está en `.gitignore` y nunca se sube
al repositorio. Sin ese archivo el firmware compila (así corre el CI), pero
no transmite: el monitor muestra `[lora] sin clave`.

## Compilar, flashear, probar

```bash
pip install platformio
pio run -e nodemcuv2                  # compilar
pio run -e nodemcuv2 -t upload        # flashear (con el secrets.ini del nodo)
pio device monitor                    # logs por serial (74880 en el ESP8266)
pio test -e native                    # tests del codec y del cifrado (requiere gcc/g++)
```

Salida esperada en el monitor al encender:

```
[boot] nodo TC-M09-G64-1789321890010 (0xd35d) fw 0.3 (encendido)
[lora] huella de clave 219b1a1d
[sensor] MAX30102 sin dedo: no se mide
[sensor] code 1 = 24.30
[sensor] code 2 = 58.10
[sensor] code 3 = 24.31
[sensor] code 4 = 0.01
[sensor] code 5 = 1.02
[sensor] code 6 = -2.10
[sensor] code 7 = 1.30
[sensor] detectados: DHT22, DS18B20, MPU6050, MAX30102; fallando: ninguno
[lora] ráfaga: 1 capturas + ESTADO
[sleep] 591 s
```

Cuando algo falla, el log dice qué revisar:

| Mensaje | Qué revisar |
|---|---|
| `[lora] sin clave` | falta `secrets.ini` al compilar: generarlo con `lora-key` y volver a flashear |
| `CONFIG con MIC inválido` | el CONFIG no se selló con la clave de este nodo; comparar la huella con la del log de la Raspberry |
| `el SX1276 no responde (RegVersion=0x00 ...)` | VCC/GND del módulo LoRa, MISO o NSS |
| `el SX1276 no responde (RegVersion=0xFF ...)` | MISO al aire o módulo sin alimentación |
| `DS18B20 ... CRC del scratchpad inválido` | contacto flojo o pull-up débil |
| `DHT22 ... la respuesta se cortó a mitad` | contacto flojo o pull-up débil |
| `MPU6050 ... lecturas I2C fallaron` | SDA/SCL |
| `MAX30102 dejó de entregar muestras` | SDA/SCL |

Un sensor que no aparece en `detectados` no contestó: está desconectado o
mal cableado (VCC, GND, pin de datos).

## Configuración por nodo (`platformio.ini` → `build_flags`)

| Flag | Obligatorio | Descripción |
|---|---|---|
| `NODE_SERIAL`, `NODE_ID`, `LORA_KEY_HEX` | sí | vienen de `secrets.ini` (`lora-key`); no se escriben a mano |
| `NET_ID` | sí | igual a `EDGE_LORA_NET_ID` de la Raspberry |
| `FCNT_BLOQUE` | no (1024) | contadores de trama que se reservan por escritura en flash |
| `LORA_FREQ_HZ`, `LORA_TX_POWER_DBM` | sí | **[confirmar]** con la regulación de la ANE |
| `LORA_SF`, `LORA_BW_HZ`, `LORA_CR`, `LORA_SYNC_WORD` | no (9, 125000, 5, 0x12) | deben coincidir con la Raspberry |
| `PIN_LORA_*`, `PIN_DHT22`, `PIN_DS18B20`, `PIN_I2C_SDA`, `PIN_I2C_SCL` | no | por defecto, la tabla de cableado de arriba (ESP32: ver `include/config.h`) |
| `SENSOR_DHT22`, `SENSOR_DS18B20`, `SENSOR_MPU6050`, `SENSOR_MAX30102` | al menos uno | sensores del catálogo que se revisan (el env `nodemcuv2` trae los 4) |
| `SENSOR_SIMULADO` | — | temperatura y humedad sintéticas; no se combina con `SENSOR_DHT22` |
| `SENSOR_CODE_*` | no (1–9) | codes de `EDGE_LORA_VARIABLES` (abajo) |
| `DS18B20_RESOLUTION` | no (12) | 9–12 bits; 11 bits reduce a la mitad el tiempo despierto |
| `MPU6050_WINDOW_MS` | no (2000) | ventana de muestreo del acelerómetro |
| `MAX30102_FINGER_IR`, `MAX30102_ATTEMPTS` | no (50000, 2) | umbral de IR para "hay dedo" y ventanas de 4 s a intentar |
| `DEFAULT_FRECUENCIA_CAPTURA_MIN`, `DEFAULT_INTERVALO_TRANSMISION_MIN` | no (10, 15) | config hasta el primer CONFIG recibido |
| `MAX_SAMPLES` | no (ESP8266 10, ESP32 32) | capturas pendientes en memoria RTC |
| `BATTERY_ADC_PIN`, `BATTERY_DIVIDER` | no (−1, 2.0) | −1 = sin medición de batería; en el ESP8266, `A0` |

### Codes de variable

| Code | Sensor | Variable | Unidad |
|---|---|---|---|
| 1 | DHT22 | temperatura | C |
| 2 | DHT22 | humedad relativa | % |
| 3 | DS18B20 | temperatura | C |
| 4 | MPU6050 | actividad | g |
| 5 | MPU6050 | pico de aceleración | g |
| 6 | MPU6050 | inclinación pitch | ° |
| 7 | MPU6050 | inclinación roll | ° |
| 8 | MAX30102 | frecuencia cardíaca | BPM |
| 9 | MAX30102 | SpO2 | % |

Cada code se mapea en `EDGE_LORA_VARIABLES` de la Raspberry
(`code=nombre:unidad`) al nombre con que la variable existe en la BD. Un
code sin mapear se descarta en la Raspberry con un warning.

## Estructura

```
platformio.ini
include/config.h            # flags de compilación, pines y valores por defecto
lib/sgpmp_protocol/         # codec de tramas v1/v2 y AES-CCM (C++ puro, compartido con los tests)
src/main.cpp                # ciclo captura → ráfaga → ventana RX → deep sleep
src/platform.*              # deep sleep, memoria RTC y WiFi de cada placa
src/sensors.*               # catálogo: detección, estado y variables de cada captura
src/sensor_*.cpp            # un driver por sensor (DHT22, DS18B20, MPU6050, MAX30102)
src/node_config.*           # config y contadores v2 persistidos en flash (NVS / EEPROM)
src/battery.*               # nivel de batería por ADC
test/test_protocol/         # vectores de PROTOCOLO_LORA.md §9 y §11, FIPS-197 y SP 800-38C
```

## Pendiente

- Medir en el ESP8266 el tiempo de cifrado por trama (se espera del orden de
  milisegundos, frente a cientos de ms de transmisión).
- No borrar la flash del nodo (`pio run -t erase`): su contador de tramas
  vuelve a 0 y la Raspberry lo rechaza como repetición
  (`INSTALACION_RASPBERRY.md` §12).
- Verificar en hardware real (docs/PRUEBAS_CAMPO.md): la ventana RX tras
  `endPacket()` y el consumo en deep sleep (el NodeMCU tiene regulador y
  USB-serial que consumen aunque el ESP8266 duerma).
