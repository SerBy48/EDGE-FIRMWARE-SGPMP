# esp32/ — firmware del nodo sensor

Firmware de cada ESP32 con su propio módulo SX1276: captura sensores y
transmite por LoRa al gateway (la Raspberry). Nunca habla MQTT directo.

**Vacío a propósito por ahora** — depende del formato de trama definido en
`../docs/PROTOCOLO_LORA.md` (borrador, Fase 0 de `../docs/PLAN_DESARROLLO.md`).

## Estructura prevista (PlatformIO)

```
platformio.ini
src/
  main.cpp
include/
lib/
```
