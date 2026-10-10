# raspberry/ — edge_agent

Servicio que corre en la Raspberry: recibe LoRa de los ESP32 del sitio y lo
sube por MQTT al broker (`BROKER-MQTT-SGPMP`), siguiendo el contrato de
`INTEGRACION_DISPOSITIVOS_RF23.md`. Mantiene una conexión MQTT persistente y
responde los comandos RF-23 por sí solo: no requiere SSH para operar.

También recibe los umbrales ambientales de RF-17 (`tipo_comando:
"UMBRAL_AMBIENTAL"`, contrato en `INTEGRACION_DISPOSITIVOS_RF17.md` del broker):
los guarda en `umbrales.json`, junto a `config.json`, y confirma con
`ACK_UMBRAL`. Evaluar las lecturas contra esos umbrales es de RF-55.

Al conectar declara un Last Will `{"tipo_mensaje": "DESCONEXION"}` en el
`status` del Gateway (primer serial de `EDGE_SERIALS`) y lo publica a mano
antes de un cierre ordenado: así el broker sabe al instante que el Edge está
apagado y no espera los 30 s del ACK (TC-M09-63).

**Estado: M1–M3 y Fase 2 implementados** (`../docs/PLAN_DESARROLLO.md`).
`EDGE_SOURCE=lora` usa el SX1276 real; `EDGE_SOURCE=fake` usa datos
sintéticos (desarrollo y pruebas contra el broker sin radio).

Instalación en la Raspberry (una vez; después las versiones nuevas llegan
solas, ver abajo):

```bash
sudo ./scripts/install.sh --env /ruta/edge-agent.env [--ca /ruta/ca.pem]
journalctl -u edge-agent -f
```

Actualización automática: `edge-updater.timer` revisa cada 3 horas los tags
`vX.Y.Z` de `main`, los instala con `install.sh` y vuelve a la versión anterior
si el servicio no queda sano. Firma de tags opcional y clave de deploy para
cuando el repo sea privado. Cómo publicar un release: `../docs/RELEASES.md`.

## Qué hace

- Conexión persistente (`clean_session=False`, `client_id` fijo, QoS 1,
  reconexión automática con backoff), con TLS opcional.
- Heartbeat cada `min(EDGE_HEARTBEAT_INTERVAL_S, frecuencia_captura/2)`,
  con `estado_local_buffer`, `datos_pendientes_buffer`, `version_firmware` y
  `reloj_sincronizado`.
- Comando `sgpmp/<serial>/command` → validación → persistencia → ACK en
  `sgpmp/<serial>/status` con el `id_comando` del broker; idempotente por
  `id_comando`/`config_version` y, con NTP, rechaza comandos vencidos por
  `emitido_en` (TC-M09-252).
- Telemetría y ACKs pasan por un buffer SQLite: nada se pierde sin red ni
  al reiniciar, y lo retenido sale con `origen: BUFFER_LOCAL`.

## Correr en desarrollo

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"      # Linux: .venv/bin/pip
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check .

# Aceptación contra dev (además de EDGE_*: EDGE_IT_API_URL, EDGE_IT_API_TOKEN)
.venv/Scripts/python -m pytest -m integration -v

# Contra un broker (variables en edge-agent.env.example)
set -a; . ./mi-ambiente.env; set +a
.venv/Scripts/python -m edge_agent
```

## Estructura

```
edge_agent/
  agent.py            # orquestación: agenda, comandos, buffer, heartbeat
  mqtt_client.py      # enlace paho → eventos en cola
  commands.py         # validación RF-23/RF-17 e idempotencia, armado del ACK
  config_store.py     # config vigente por serial y umbrales RF-17 (JSON atómico)
  buffer.py           # outbox SQLite (WAL) + estado_local_buffer
  sources.py          # DataSource + FakeLoraSource
  system.py           # reloj_sincronizado (timedatectl)
  systemd.py          # sd_notify: READY y watchdog
  config.py           # Settings desde variables de entorno
  main.py             # arranque del proceso (--check-config)
  lora/
    protocol.py       # tramas v1 (docs/PROTOCOLO_LORA.md)
    sx1276.py         # driver SPI del SX1276
    gateway.py        # DataSource LoRa: uplink, dedupe, downlink de config
    monitor.py        # monitor de enlace para pruebas de campo
systemd/
  edge-agent.service  # Type=notify, Restart=always, WatchdogSec
  edge-updater.*      # timer cada 3 h + servicio de actualización automática
  journald-sgpmp-edge.conf
scripts/
  install.sh          # provisión/actualización idempotente
  update.sh           # busca, verifica e instala el último release (rollback)
release/
  allowed_signers     # claves que pueden firmar releases (firma opcional)
tests/
  integration/        # M3 — pytest -m integration (contra dev)
  updater/            # update.sh contra un repo local (bash)
```
