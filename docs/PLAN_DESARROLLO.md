# Plan de desarrollo — Firmware nodo edge SGPMP (Raspberry + ESP32/LoRa)

Este repositorio es distinto de `BROKER-MQTT-SGPMP` (el gateway MQTT↔HTTPS ya
validado). Aquí vive el firmware/software que corre **en el campo**: el
servicio de la Raspberry (nodo edge/gateway LoRa) y el firmware de los ESP32
(nodos sensores), que juntos son quienes hablan MQTT contra ese broker.

Referencia obligatoria antes de tocar cualquier parte del cliente MQTT:
`INTEGRACION_DISPOSITIVOS_RF23.md` del repo `BROKER-MQTT-SGPMP` — define el
contrato de topics/payload que este firmware debe respetar exactamente
(ya validado end-to-end en dev con ACK simulado por SSH).

## 0. Decisión de arquitectura ya tomada

**Opción A confirmada:** la Raspberry lleva su propio módulo LoRa SX1276, y
cada ESP32 lleva su propio módulo LoRa SX1276. Topología estrella
punto-a-multipunto: la Raspberry es el único "gateway LoRa" del sitio: N ESP32
le hablan a ella, ella agrega y sube todo por MQTT/TCP-IP (WiFi/Ethernet/4G,
a definir) al broker.

```
[ESP32 + SX1276] ─┐
[ESP32 + SX1276] ─┤  LoRa punto-a-multipunto (radio, no mesh)
[ESP32 + SX1276] ─┘
         │
         ▼
[Raspberry + SX1276]  (gateway LoRa del sitio)
         │  IP (WiFi/Ethernet/4G — a definir por sitio)
         ▼
   Broker MQTT (BROKER-MQTT-SGPMP, ya en dev)
```

Pendiente de decidir con el equipo (bloquea el diseño del protocolo LoRa,
sección `PROTOCOLO_LORA.md`): **¿el `serial` MQTT registrado en
`modulo9.dispositivos_iot` identifica a la Raspberry (un serial agrega todos
sus ESP32) o a cada ESP32 individualmente (la Raspberry es transparente y
reenvía N seriales)?** Esto determina si `frecuencia_captura`/
`intervalo_transmision` del comando RF-23 aplican a todo el sitio o por
sensor. Se documenta como assumption en la sección 1 hasta que se confirme.

## 1. Fases

### Fase 0 — Protocolo LoRa (bloqueante, sin código todavía)
- Definir banda/frecuencia según regulación de radiofrecuencia en el país de
  despliegue (asumido Colombia → banda ISM 915 MHz; **confirmar con
  regulación local antes de fijar la frecuencia en firmware**, no asumir EU868).
- Definir formato de trama (header + payload + CRC), direccionamiento por
  nodo ESP32, esquema de reintento/ACK (LoRa no es fiable como IP).
- Definir si hay downlink (Raspberry → ESP32) para reenviar cambios de
  configuración por sensor, o si la config del comando MQTT solo afecta el
  agregado en la Raspberry.
- Entregable: `docs/PROTOCOLO_LORA.md` (ver plantilla ya creada en este repo)
  firmado/revisado por el equipo de hardware antes de pasar a Fase 1.

### Fase 1 — Firmware ESP32 (nodo sensor)
- Lectura de sensores según `frecuencia_captura` vigente (default hasta que
  llegue la primera config).
- Empaquetado y envío de tramas LoRa uplink al gateway.
- Recepción de downlink (si Fase 0 lo define) para actualizar su propia
  frecuencia de captura.
- Modo bajo consumo entre capturas (estos nodos son a batería).
- Entregable: `esp32/` — proyecto PlatformIO, sin lógica de MQTT (el ESP32
  nunca habla MQTT directo, solo LoRa).

### Fase 2 — Driver LoRa + listener en la Raspberry
- Recepción continua de tramas LoRa (SPI, `pySX127x` o equivalente),
  validación de CRC, decodificado.
- Sin esto no hay datos que subir; es prerequisito de la Fase 3.
- Entregable: `raspberry/edge_agent/lora_receiver.py`.

### Fase 3 — Cliente MQTT del edge-agent (reusa el contrato ya probado)
- Publicación en `sgpmp/<serial>/telemetry` y `sgpmp/<serial>/heartbeat`.
- Suscripción a `sgpmp/<serial>/command`, aplicación de
  `frecuencia_captura`/`intervalo_transmision`, publicación del ACK en
  `sgpmp/<serial>/status` con el payload exacto
  `{"tipo_mensaje":"ACK_CONFIGURACION","resultado":"OK"}` dentro de 30s.
- Reutiliza exactamente lo que ya se validó a mano por SSH en
  `DOC_PRUEBA_ACL_EDGE_DEV.docx` (P1-P7 + flujo extremo a extremo) — ese
  documento es la referencia de aceptación para probar este cliente contra
  dev antes de dar la fase por cerrada.
- Entregable: `raspberry/edge_agent/mqtt_client.py`.

### Fase 4 — Persistencia y resiliencia
- Config (`frecuencia_captura`/`intervalo_transmision`) persistida en disco
  (sobrevive reinicio de la Raspberry).
- Reconexión automática MQTT; buffer local de telemetría si se pierde
  conexión IP (no se pierde el dato, se sube cuando vuelve la red).
- Nota conocida (ya documentada en RF-23): reenvío automático de comandos
  perdidos mientras el dispositivo estaba offline **no existe del lado del
  servidor todavía** — no intentar resolverlo acá, es fuera de alcance de
  este repo hasta que el backend lo soporte.
- Entregable: `raspberry/edge_agent/config_store.py`.

### Fase 5 — Empaquetado y despliegue en campo
- `edge-agent.service` (systemd, `Restart=always`).
- Script de provisión: credenciales MQTT fuera del código versionado
  (variable de entorno o archivo fuera del repo — nunca hardcodeadas, mismo
  criterio que se usó al probar por SSH con `read -s`).
- Entregable: `raspberry/systemd/edge-agent.service` + script de instalación.

### Fase 6 — Pruebas de campo con hardware real
- Repetir un set de pruebas equivalente a P1-P7 pero para el enlace LoRa:
  pérdida de trama, fuera de rango, colisión con otro nodo, batería baja.
- Prueba de extremo a extremo real: cambio de config desde la web → Raspberry
  reenvía a ESP32 por LoRa (si aplica) → ESP32 aplica → confirma a la
  Raspberry → Raspberry publica el ACK MQTT real (ya no simulado por SSH).

## 2. Convenciones de este repo

- Git Flow igual que `BROKER-MQTT-SGPMP`: `main`/`develop` protegidas, todo
  entra por `feature/*` + Pull Request, sin commit directo.
- Una feature = una fase o un sub-punto de una fase = una tarjeta de Taiga.
- El contrato MQTT (topics/payload) **no se redefine acá**: cualquier cambio
  debe acordarse primero con el equipo de `BROKER-MQTT-SGPMP` (ver aviso en
  `INTEGRACION_DISPOSITIVOS_RF23.md`, sección de contrato de ACK "propuesto,
  no confirmado").
- Credenciales MQTT nunca en el repo (ni en `.env` versionado, ni
  hardcodeadas en `.cpp`/`.py`) — mismo criterio de SEG-BROKER-01.

## 3. Qué falta decidir antes de escribir código de Fase 1 en adelante

1. Frecuencia LoRa exacta y regulación aplicable (país de despliegue).
2. Serial MQTT: ¿por Raspberry o por ESP32? (afecta el protocolo LoRa y el
   agregado de datos).
3. Conectividad IP de la Raspberry en campo (WiFi del sitio / Ethernet / 4G) —
   afecta el diseño del buffer de Fase 4.
4. Si el equipo de hardware ya tiene un formato de trama LoRa propio definido
   en otro documento — si existe, este plan debe referenciarlo en vez de
   proponer uno nuevo en `PROTOCOLO_LORA.md`.
