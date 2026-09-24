# raspberry/ — edge_agent

Servicio que corre en la Raspberry: recibe LoRa de los ESP32 del sitio y lo
sube por MQTT al broker (`BROKER-MQTT-SGPMP`), siguiendo el contrato de
`INTEGRACION_DISPOSITIVOS_RF23.md`. Mantiene una conexión MQTT persistente y
responde los comandos RF-23 por sí solo: no requiere SSH para operar.

**Estado: hito M1 implementado** (`../docs/PLAN_DESARROLLO.md`). Hasta la
Fase 2, las lecturas vienen de `FakeLoraSource` (datos sintéticos).

## Qué hace

- Conexión persistente (`clean_session=False`, `client_id` fijo, QoS 1,
  reconexión automática con backoff), con TLS opcional.
- Heartbeat cada `min(EDGE_HEARTBEAT_INTERVAL_S, frecuencia_captura/2)`,
  con `estado_local_buffer`, `datos_pendientes_buffer`, `version_firmware` y
  `reloj_sincronizado`.
- Comando `sgpmp/<serial>/command` → validación → persistencia → ACK en
  `sgpmp/<serial>/status`, idempotente por `comando_id`/`config_version`.
- Telemetría y ACKs pasan por un buffer SQLite: nada se pierde sin red ni
  al reiniciar, y lo retenido sale con `origen: BUFFER_LOCAL`.

## Correr en desarrollo

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Linux: .venv/bin/pip
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check .

# Contra un broker (variables en edge-agent.env.example)
set -a; . ./mi-ambiente.env; set +a
.venv/Scripts/python -m edge_agent
```

## Estructura

```
edge_agent/
  agent.py            # orquestación: agenda, comandos, buffer, heartbeat
  mqtt_client.py      # enlace paho → eventos en cola
  commands.py         # validación RF-23 e idempotencia, armado del ACK
  config_store.py     # config vigente por serial (JSON atómico)
  buffer.py           # outbox SQLite (WAL) + estado_local_buffer
  sources.py          # DataSource + FakeLoraSource
  system.py           # reloj_sincronizado (timedatectl)
  config.py           # Settings desde variables de entorno
  main.py             # arranque del proceso
  lora_receiver.py    # Fase 2 — bloqueado por Fase 0
systemd/
  edge-agent.service  # M2 — Restart=always + WatchdogSec
scripts/
  install.sh          # M2 — provisión única (/etc/sgpmp/edge-agent.env)
tests/
  integration/        # M3 — aceptación contra dev vía POST /v1/commands
```
