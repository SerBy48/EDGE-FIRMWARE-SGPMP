# Protocolo LoRa v1 — Raspberry (SX1276) ↔ ESP32 (SX1276)

**Estado: propuesta v1 implementada, pendiente de revisión por el equipo de
hardware** (Fase 0 de `PLAN_DESARROLLO.md`). Está codificada en
`raspberry/edge_agent/lora/protocol.py` y `esp32/lib/sgpmp_protocol/`, y ambas
implementaciones se prueban contra los mismos vectores (sección 9). Los
valores marcados **[confirmar]** son supuestos que deben validarse antes de
salir a campo; todos son configurables y ninguno obliga a cambiar código.

## 1. Topología

Estrella punto-a-multipunto: 1 Raspberry (gateway del sitio) escucha a N
ESP32 (nodos sensores). No es mesh: un ESP32 nunca reenvía tramas de otro.

## 2. Capa física

| Parámetro | Valor v1 | Configurable en | Nota |
|---|---|---|---|
| Banda | 902–928 MHz (ISM) | `EDGE_LORA_FREQ_HZ` / `LORA_FREQ_HZ` | **[confirmar]** con la regulación de la ANE (Colombia). Obligatorio, sin valor por defecto |
| Spreading Factor | SF9 | `EDGE_LORA_SF` / `LORA_SF` | SF7–SF12. Con SF10 o más, la trama máxima de 51 B supera 400 ms en aire |
| Ancho de banda | 125 kHz | `EDGE_LORA_BW_HZ` / `LORA_BW_HZ` | |
| Coding rate | 4/5 | `EDGE_LORA_CR` / `LORA_CR` | |
| Sync word | `0x12` | `EDGE_LORA_SYNC_WORD` / `LORA_SYNC_WORD` | Red privada; no `0x34` (LoRaWAN) |
| Preámbulo | 8 símbolos | fijo | |
| CRC de radio | activado | fijo | Además del CRC propio de la trama |
| Potencia TX | **[confirmar]** | `EDGE_LORA_TX_POWER_DBM` / `LORA_TX_POWER_DBM` | Obligatorio. No usar el máximo de la radio sin confirmar el límite legal |

Los dos lados deben usar exactamente los mismos parámetros de radio.

## 3. Direccionamiento y mapeo a MQTT

- `net_id` (1 B): identifica la red del sitio. La Raspberry descarta tramas
  de otro `net_id`, porque un nodo de un sitio vecino puede estar en alcance.
- `node_id` (2 B): 1–65534 por nodo. `0x0000` = gateway, `0xFFFF` reservado.
- **Mapeo `node_id → serial` en la Raspberry** (`EDGE_LORA_NODES`), sin
  reflashear nada. Resuelve el pendiente "serial por Raspberry o por ESP32"
  como configuración:
  - **Serial por ESP32:** `1=IOT-A,2=IOT-B` (cada nodo, su serial).
  - **Serial por sitio:** `1=IOT-SITIO,2=IOT-SITIO` (todos bajo el serial de
    la Raspberry). Un comando a ese serial se reenvía a todos sus nodos.
- Tramas de `node_id` no mapeados se descartan y se registran en el log.
- Variables: cada medición lleva un `code` (1 B). La Raspberry lo traduce a
  `variable`/`unidad` MQTT con `EDGE_LORA_VARIABLES`
  (`1=temperatura_ambiente:C;2=humedad_relativa:%`). `variable` debe
  existir en `modulo9.variables_ambientales`.

## 4. Formato de trama

```
┌─────┬────────┬─────────┬──────────┬─────┬──────────────┬─────────┐
│ ver │ net_id │ node_id │ msg_type │ seq │   payload    │  crc16  │
│ 1 B │  1 B   │   2 B   │   1 B    │ 1 B │  0–43 B      │   2 B   │
└─────┴────────┴─────────┴──────────┴─────┴──────────────┴─────────┘
```

- Enteros en big-endian. Floats IEEE-754 de 32 bits, big-endian.
- `ver` = `0x01`. Una versión desconocida se descarta.
- `seq`: contador por nodo (mod 256) de las tramas uplink. La Raspberry
  descarta una trama repetida (mismo `seq` que la anterior de ese nodo) y
  cuenta los huecos como tramas perdidas.
- `crc16`: CRC-16/CCITT-FALSE (poly `0x1021`, init `0xFFFF`, sin reflexión,
  sin xorout) sobre cabecera + payload. Valor de referencia:
  `crc16("123456789") = 0x29B1`.
- Largo máximo total: 51 B.

## 5. Mensajes

| `msg_type` | Nombre | Sentido | Payload |
|---|---|---|---|
| `0x01` | TELEMETRIA | ESP32 → RPi | `age_s` u16 · `count` u8 · `count` × (`code` u8 · `valor` f32). Máximo 8 mediciones |
| `0x02` | ESTADO | ESP32 → RPi | `bateria_pct` u8 (`0xFF` = desconocida) · `cfg_version` u16 · `frecuencia_captura_min` u16 · `intervalo_transmision_min` u16 · `fw_major` u8 · `fw_minor` u8 · `flags` u8 |
| `0x03` | ACK_CONFIG | ESP32 → RPi | `cfg_version` u16 · `resultado` u8 (`0` OK, `1` inválida) |
| `0x04` | CONFIG | RPi → ESP32 | `cfg_version` u16 · `frecuencia_captura_min` u16 · `intervalo_transmision_min` u16 |

- `age_s`: segundos entre la captura y la transmisión de esa trama. Los
  ESP32 no tienen hora real. La Raspberry calcula
  `timestamp_captura = hora_recepción − age_s` con su reloj NTP.
- `flags` de ESTADO: bit0 = primer ESTADO tras encendido/reset, bit1 = falló
  alguna lectura de sensor en el ciclo.

## 6. Ciclo del nodo y ventana de downlink

1. El ESP32 despierta cada `frecuencia_captura_min`, lee sus sensores y
   guarda la captura en memoria RTC.
2. Cuando pasó `intervalo_transmision_min` desde la última transmisión (o si
   la memoria de capturas está llena), transmite en ráfaga una TELEMETRIA
   por captura y, al final, **un ESTADO**.
3. Tras el ESTADO abre una **ventana RX de 1500 ms** (`LORA_RX_WINDOW_MS`).
4. La Raspberry, al recibir un ESTADO cuya config no coincide con la
   deseada para ese nodo, espera 50 ms (`EDGE_LORA_DOWNLINK_DELAY_MS`) y
   transmite un CONFIG.
5. El ESP32 valida, persiste en NVS, responde ACK_CONFIG y aplica la
   config desde el ciclo siguiente.
6. Si el CONFIG o el ACK se pierden, el siguiente ESTADO sigue mostrando la
   config vieja y la Raspberry reintenta. **El reintento ocurre solo, en cada
   ciclo, sin temporizadores extra.**

La Raspberry compara por valores (`frecuencia`/`intervalo`), no solo por
`cfg_version`. Así un nodo que perdió su NVS o una Raspberry reinstalada
vuelven a converger solos.

## 7. Confiabilidad

- Uplink sin ACK (fire-and-forget) con `seq`: menor consumo en el ESP32. Las
  pérdidas se miden en la Raspberry y viajan en `metadatos.lora` de cada
  telemetría MQTT (`seq`, `rssi`, `snr`, `perdidas`). El heartbeat del broker
  no tiene campo para esto.
- Downlink con reintento implícito (sección 6).
- Si hace falta ACK por trama uplink (opción 2 del borrador anterior), se
  decide con las pérdidas medidas en campo (Fase 3).

## 8. Relación con RF-23 y el ACK MQTT

El ACK MQTT (`sgpmp/<serial>/status`) lo publica la Raspberry apenas acepta y
persiste el comando, dentro de los 30 s del broker. El ESP32 recibe la
config en su próxima ventana, que puede llegar minutos después
(`PLAN_DESARROLLO.md` §2.3). La aplicación real en el nodo queda visible en
los logs de la Raspberry y en el siguiente ESTADO. Todavía no hay campo MQTT
para reportarla: pendiente del ACK asíncrono de M09.

## 9. Vectores de prueba

`net_id=0x2A`, `node_id=0x0102`. Deben producir exactamente estos bytes en
Python y en C++.

| Caso | seq | Mensaje | Trama (hex) |
|---|---|---|---|
| telemetria | 7 | age 90 s; (1, 23.5), (2, −4.25) | `012a01020107005a020141bc000002c08800005494` |
| estado | 8 | bat 87 %, ver 0x0305, 10/15 min, fw 1.2, REINICIO | `012a01020208570305000a000f010201ec76` |
| estado_sin_bateria | 9 | bat desconocida, ver 0, 10/15 min, fw 0.1 | `012a01020209ff0000000a000f000100b4f5` |
| ack_config | 10 | ver 0x0306, OK | `012a0102030a0306005feb` |
| config | 3 | ver 0x0306, 5/30 min | `012a0102040303060005001e4a6c` |

## 10. Pendientes de esta versión

1. **[confirmar]** Frecuencia, potencia y límites de tiempo en aire según la
   ANE.
2. Sin autenticación: cualquiera en alcance con los parámetros de radio
   puede inyectar tramas con un `node_id` válido. Propuesta v2: MIC
   AES-CMAC de 4 B con clave por nodo.
3. Tabla de `code` de variables definitiva, junto al equipo que eligió los
   sensores.
4. Duty cycle / canales: v1 usa un solo canal fijo. Evaluar salto de
   frecuencia si la regulación lo exige para la potencia elegida.
