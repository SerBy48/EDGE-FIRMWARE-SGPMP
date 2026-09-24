# Pruebas de campo — Fase 3

Protocolo de pruebas con hardware real: Raspberry + SX1276 como gateway y
ESP32 + SX1276 como nodos. Es el equivalente, para el enlace LoRa, de lo que
`DOC_PRUEBA_ACL_EDGE_DEV.docx` (P1–P7) hizo para MQTT. Cada prueba deja
evidencia (salida del monitor, logs de `journalctl` o captura de la UI) en el
documento de resultados.

## 0. Preparación

| Paso | Cómo | Verificación |
|---|---|---|
| Parámetros de radio legales | Confirmar frecuencia y potencia con la ANE; fijar `EDGE_LORA_FREQ_HZ`/`EDGE_LORA_TX_POWER_DBM` y `LORA_FREQ_HZ`/`LORA_TX_POWER_DBM` | Mismo valor en `/etc/sgpmp/edge-agent.env` y en `platformio.ini` |
| Nodos flasheados | `pio run -e esp32dev -t upload` con un `NODE_ID` distinto por nodo | Serial del ESP32 muestra `[boot] nodo 0x000N` |
| Raspberry instalada | `sudo raspberry/scripts/install.sh --env ...` con `EDGE_SOURCE=lora` | `systemctl status edge-agent` activo; log "Gateway LoRa escuchando" |
| Seriales registrados | Cada serial de `EDGE_LORA_NODES` existe en `modulo9.dispositivos_iot` | `GET /v1/devices` los lista |
| Variables | Cada `nombre` de `EDGE_LORA_VARIABLES` existe en `modulo9.variables_ambientales` | La telemetría aparece en la BD, sin `VariableNotFoundError` en el broker |

Para las pruebas de enlace (L1–L4) se usa el monitor, con el servicio detenido
porque la radio es una sola:

```bash
sudo systemctl stop edge-agent
sudo systemd-run --pipe --wait -p EnvironmentFile=/etc/sgpmp/edge-agent.env \
    /opt/sgpmp-edge/venv/bin/python -m edge_agent.lora.monitor
# Ctrl+C imprime el resumen por nodo (recibidas, perdidas, %, RSSI, SNR)
sudo systemctl start edge-agent
```

Para acelerar, flashear los nodos de prueba con
`-DDEFAULT_FRECUENCIA_CAPTURA_MIN=1 -DDEFAULT_INTERVALO_TRANSMISION_MIN=1`.

## 1. Enlace LoRa

| # | Prueba | Procedimiento | Criterio de aceptación |
|---|---|---|---|
| L1 | Recepción básica | Nodo a 5 m, 30 min | ≥ 99 % de tramas, sin errores de CRC de radio |
| L2 | Alcance | Nodo a distancias crecientes hasta el punto más lejano real del sitio, 30 min en cada punto | Pérdida ≤ 5 % y SNR > −7 dB (margen sobre el límite de SF9 ≈ −12.5 dB) en el punto más lejano. Si no se cumple, subir SF y repetir, verificando tiempo en aire (§4 del protocolo) |
| L3 | Fuera de rango | Alejar el nodo hasta perder enlace; después volver | Al volver, el gateway reanuda sin reiniciar; las pérdidas quedan contadas en `metadatos.lora.perdidas` |
| L4 | Colisión | 2+ nodos con el mismo intervalo, encendidos a la vez | Pérdida medida por nodo documentada; si es > 5 %, evaluar jitter en el nodo o la opción 2 de confiabilidad |
| L5 | Red vecina | Un nodo con otro `NET_ID` transmitiendo cerca | El gateway lo descarta (el monitor muestra "otra red") y no genera telemetría |

## 2. Extremo a extremo (servicio corriendo)

| # | Prueba | Procedimiento | Criterio de aceptación |
|---|---|---|---|
| E2E-1 | Telemetría real | Nodo operando 1 h | La telemetría llega a la BD con `timestamp_captura` correcto (diferencia menor a 5 s con la hora real de captura) |
| E2E-2 | Estado en el servidor | Operación normal | `GET /v1/devices` muestra `ACTIVO` para cada serial con nodos vivos |
| E2E-3 | Cambio de config desde la web | Cambiar frecuencia/intervalo en la UI | UI: `APLICADA` (ACK de la Raspberry < 30 s). Log: "CONFIG vN enviado a nodo" en la siguiente ventana del nodo y luego "Nodo aplicó config vN". Serial del ESP32: `[config] vN` |
| E2E-4 | Config sobrevive corte | Tras E2E-3, desconectar la batería del nodo 1 min | Al volver, el ESTADO reporta la config nueva (NVS); no hay downlink repetido |
| E2E-5 | Nodo caído | Apagar un nodo (serial por ESP32) | Tras `2 × max(frecuencia, intervalo)` la Raspberry deja de publicar su heartbeat y el servidor lo pasa a `SIN_SEÑAL` (alerta MODERADO) |
| E2E-6 | Corte de internet | Desconectar el uplink IP de la Raspberry 30 min | Sin pérdida: al volver, heartbeat con `estado_local_buffer=ACTIVO`, telemetría con `origen=BUFFER_LOCAL`, luego heartbeat `INACTIVO` |
| E2E-7 | Corte de energía de la Raspberry | Quitar energía 5 min | Arranca sola (systemd), conserva config y buffer; los nodos siguen transmitiendo y el gateway los retoma |
| E2E-8 | Proceso trabado | `sudo kill -STOP $(pidof -s python)` del servicio | systemd lo reinicia por watchdog en ≤ 60 s (`journalctl -u edge-agent` muestra "Watchdog timeout") |

## 3. Batería (nodo)

| # | Prueba | Procedimiento | Criterio de aceptación |
|---|---|---|---|
| B1 | Consumo en sleep | Medir corriente con multímetro/medidor en deep sleep | ≤ 150 µA (ESP32 DevKit tiene LED/regulador que suben el consumo; documentar la placa) |
| B2 | Consumo por ciclo | Medir carga de un ciclo con transmisión | Documentar mAs por ciclo para estimar autonomía con la config por defecto |
| B3 | Batería baja | Alimentar con fuente variable, bajar a 3.3 V | `nivel_bateria_pct` baja hasta 0 en el heartbeat del serial (requiere `BATTERY_ADC_PIN`) |

## 4. Resultado

Registrar por prueba: fecha, versión de firmware (`version_firmware` del
heartbeat y `FW_MAJOR.FW_MINOR` del nodo), parámetros de radio, distancia o
condiciones, evidencia y veredicto. Con los resultados de L2/L4 se decide SF
y si hace falta la opción 2 de confiabilidad (`PROTOCOLO_LORA.md` §7).
