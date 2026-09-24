# Arquitectura — nodo edge SGPMP

## Componentes

| Componente | Rol | Habla |
|---|---|---|
| ESP32 + SX1276 (nodo sensor) | Captura datos de sensores, los empaqueta y transmite | Solo LoRa (uplink), y downlink si Fase 0 de `PLAN_DESARROLLO.md` lo confirma |
| Raspberry + SX1276 (nodo edge / gateway de sitio) | Recibe LoRa de todos los ESP32 del sitio, agrega, aplica config, habla MQTT | LoRa (con los ESP32) + MQTT/TCP-IP (con el broker) |
| Broker MQTT (`BROKER-MQTT-SGPMP`) | Gateway MQTT↔HTTPS, ya desplegado y validado en dev | MQTT (con la Raspberry) + HTTPS (con el backend) |

La Raspberry es la única frontera entre el mundo LoRa (radio, sin IP) y el
mundo MQTT/IP. Ningún ESP32 se conecta nunca directo al broker.

## Por qué la Raspberry y no cada ESP32 hablan MQTT directo

- MQTT sobre TCP/IP requiere WiFi/Ethernet/celular en cada nodo — encarece y
  consume mucha más batería que LoRa en cada ESP32 sensor.
- LoRa da largo alcance con bajo consumo para los sensores; la Raspberry
  concentra la única conexión a internet del sitio (WiFi/Ethernet/4G, a
  definir por ubicación).
- Esto ya es consistente con el contrato de
  `INTEGRACION_DISPOSITIVOS_RF23.md`: ese documento asume un cliente MQTT por
  `serial`, que en este diseño es responsabilidad exclusiva del proceso
  `edge_agent` en la Raspberry.

## Diagrama de capas del edge-agent (Raspberry)

```
┌─────────────────────────────────────────────────────────┐
│                     edge_agent (proceso)                 │
│                                                           │
│  lora_receiver.py   →   procesamiento/agregado   →  mqtt_client.py │
│  (SPI, SX1276,          (buffer, validación,          (paho-mqtt,  │
│   decodifica tramas)     aplica config vigente)         topics RF-23)│
│                                                           │
│  config_store.py  (persistencia local: frecuencia_captura,          │
│                     intervalo_transmision — sobrevive reinicio)      │
└─────────────────────────────────────────────────────────┘
```

Ningún módulo de este proceso toca directamente la base de datos ni el
backend HTTPS — esa separación de capas ya vive en `BROKER-MQTT-SGPMP` y no
se duplica acá (mismo principio que su `AGENTS.md`, aunque este es un repo
distinto).

## Fuera de alcance de este repositorio

- Lógica de negocio de `modulo3`/`modulo9` (vive en el backend/BD, no acá).
- El contrato de payload MQTT en sí (vive en `BROKER-MQTT-SGPMP`; este repo
  lo consume, no lo redefine).
- Reenvío automático de comandos perdidos por desconexión — no implementado
  del lado del servidor, ver nota en `PLAN_DESARROLLO.md` Fase 4.
