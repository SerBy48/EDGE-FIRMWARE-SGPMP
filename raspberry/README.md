# raspberry/ — edge_agent

Servicio que corre en la Raspberry: recibe LoRa de los ESP32 del sitio y lo
sube por MQTT al broker (`BROKER-MQTT-SGPMP`), siguiendo el contrato de
`INTEGRACION_DISPOSITIVOS_RF23.md`. Mantiene una conexión MQTT persistente y
responde los comandos RF-23 por sí solo: no requiere SSH para operar.

El enlace MQTT (hitos M1–M3 de `../docs/PLAN_DESARROLLO.md`) **no depende del
protocolo LoRa** y se desarrolla primero, con una fuente de datos simulada
(`FakeLoraSource`). `lora_receiver.py` (Fase 2) sí depende del formato de
trama de `../docs/PROTOCOLO_LORA.md`, que sigue en borrador.

## Estructura prevista

```
edge_agent/
  mqtt_client.py      # M1 — sesión persistente, heartbeat, comando→ACK
  config_store.py     # M1 — config + último comando aplicado (idempotencia)
  buffer.py           # M1 — cola SQLite para telemetría/ACK sin conexión
  sources.py          # M1 — FakeLoraSource (interfaz común con lora_receiver)
  lora_receiver.py    # Fase 2 — bloqueado por Fase 0
  main.py             # ensambla todo, arranca el proceso
systemd/
  edge-agent.service  # M2 — Restart=always + WatchdogSec
scripts/
  install.sh          # M2 — provisión única (/etc/sgpmp/edge-agent.env)
tests/
  integration/        # M3 — aceptación contra dev vía POST /v1/commands
```
