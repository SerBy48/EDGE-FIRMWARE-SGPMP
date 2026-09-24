# raspberry/ — edge_agent

Servicio que corre en la Raspberry: recibe LoRa de los ESP32 del sitio y lo
sube por MQTT al broker (`BROKER-MQTT-SGPMP`), siguiendo el contrato de
`INTEGRACION_DISPOSITIVOS_RF23.md`.

**Vacío a propósito por ahora.** El código de `edge_agent/lora_receiver.py`
depende del formato de trama definido en `../docs/PROTOCOLO_LORA.md`, que
sigue en borrador (Fase 0 de `../docs/PLAN_DESARROLLO.md`). El cliente MQTT
(`edge_agent/mqtt_client.py`, Fase 3) sí se puede empezar antes, reusando el
contrato ya validado en dev — no depende del protocolo LoRa.

## Estructura prevista

```
edge_agent/
  lora_receiver.py   # Fase 2 — bloqueado por Fase 0
  mqtt_client.py      # Fase 3 — puede iniciarse ya, contrato ya validado
  config_store.py     # Fase 4
  main.py              # ensambla todo, arranca el proceso
systemd/
  edge-agent.service   # Fase 5
tests/
```
