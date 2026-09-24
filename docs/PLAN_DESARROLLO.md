# Plan de desarrollo — Firmware nodo edge SGPMP (Raspberry + ESP32/LoRa)

Este repositorio es distinto de `BROKER-MQTT-SGPMP` (el gateway MQTT↔HTTPS ya
validado). Aquí vive el firmware/software que corre **en el campo**: el
servicio de la Raspberry (nodo edge/gateway LoRa) y el firmware de los ESP32
(nodos sensores), que juntos son quienes hablan MQTT contra ese broker.

Referencia obligatoria antes de tocar cualquier parte del cliente MQTT:
`INTEGRACION_DISPOSITIVOS_RF23.md` del repo `BROKER-MQTT-SGPMP` — define el
contrato de topics/payload que este firmware debe respetar exactamente
(ya validado end-to-end en dev con ACK simulado por SSH).

## Objetivo inmediato

Que la Raspberry mantenga por sí sola una **conexión MQTT persistente** con el
broker y responda los comandos RF-23 automáticamente, de modo que **nadie
tenga que volver a entrar por SSH** ni al servidor (hoy el ACK se simula con
`docker exec … mosquitto_pub`) ni a la Raspberry (para configurarla,
reiniciarla o revisar su estado). Eso se cubre con los hitos M1–M3, que **no
dependen del protocolo LoRa** y pueden avanzar mientras la Fase 0 sigue en
revisión.

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

Pendiente de decidir con el equipo: **¿el `serial` MQTT registrado en
`modulo9.dispositivos_iot` identifica a la Raspberry (un serial agrega todos
sus ESP32) o a cada ESP32 individualmente (la Raspberry reenvía N
seriales)?**

- **No bloquea el cliente MQTT (M1).** La ACL de `sgpmp_devices` permite
  escribir en `sgpmp/+/{telemetry,heartbeat,status}` y leer
  `sgpmp/+/command`, así que una sola conexión MQTT puede atender 1 o N
  seriales. `mqtt_client.py` se diseña desde el inicio con una lista de
  seriales.
- **Sí bloquea** el protocolo LoRa (Fase 0), el downlink y la semántica del
  heartbeat por serial (ver sección 2.3).

## 1. Hitos del enlace persistente (sin dependencia de LoRa)

### M1 — Cliente MQTT persistente del edge_agent
- Una conexión MQTT por Raspberry, `client_id` fijo y único
  (`edge-<serial_raspberry>`). La credencial es compartida por todos los
  dispositivos: dos clientes con el mismo `client_id` se desconectan entre sí.
- Sesión persistente: `clean_session=False` y suscripción a
  `sgpmp/<serial>/command` con QoS 1, para que Mosquitto encole los comandos
  durante cortes cortos. Publicaciones con QoS 1.
- Keepalive (~60 s), reconexión con backoff exponencial y jitter, sin límite
  de reintentos.
- Heartbeat periódico según la sección 2.1; telemetría según
  `intervalo_transmision`.
- Recepción de comando → validación → `config_store` → ACK en
  `sgpmp/<serial>/status` (contrato de la sección 2.2).
- Buffer local en disco (SQLite en modo WAL, tamaño máximo configurable) para
  telemetría y ACKs no entregados; se reenvía al reconectar con
  `origen: "BUFFER_LOCAL"`. No se depende de la cola en memoria de paho,
  que se pierde al reiniciar.
- TLS configurable (`ca_cert`, puerto `MQTT_TLS_HOST_PORT`) desde el primer
  día, aunque `dev` todavía no tenga certificados.
- Hora: NTP obligatorio. `timestamp_captura` lo pone la Raspberry (los ESP32
  no tienen reloj). Si el sitio puede arrancar sin red, agregar un RTC.
  Informar `reloj_sincronizado` en el heartbeat.
- Fuente de datos intercambiable: `FakeLoraSource` (datos sintéticos) hasta
  que exista `lora_receiver.py` (Fase 2).
- Entregables: `raspberry/edge_agent/` (`agent.py`, `mqtt_client.py`,
  `commands.py`, `config_store.py`, `buffer.py`, `sources.py`) y
  `raspberry/edge-agent.env.example`. **Implementado** — falta verificar la
  sesión persistente contra el Mosquitto de dev (M3).

### M2 — Operación sin SSH
- `edge-agent.service` (systemd): `Restart=always`, `WatchdogSec=` con
  `sd_notify` (se reinicia si el proceso se cuelga, no solo si muere), logs
  en journald con rotación.
- Provisión de una sola vez: `/etc/sgpmp/edge-agent.env` (permisos 0600,
  cargado con `EnvironmentFile=`) con host, puerto, usuario, contraseña,
  seriales y ruta del CA. Nunca en el repo.
- Observabilidad remota por el heartbeat, con campos que ya existen en
  `HeartbeatPayload`: `version_firmware`, `estado_local_buffer`,
  `datos_pendientes_buffer`, `reloj_sincronizado`, `calidad_senal_*`.
- Actualización remota del software: **decisión pendiente** (sección 4, punto 11).
  No se puede hacer por MQTT sin topics nuevos, lo que requiere RFC
  (`GUIA_CONEXION_IOT.md` §5).
- Entregables: `raspberry/systemd/edge-agent.service`,
  `raspberry/scripts/install.sh`.

### M3 — Aceptación automática (reemplaza la prueba manual por SSH)
- Test de integración contra `dev`: arrancar `edge_agent` con
  `FakeLoraSource`, llamar `POST /v1/commands` del broker y esperar
  `estado: "APLICADA"`; verificar que telemetría y heartbeat se ingestan.
- Casos de resiliencia: cortar la red mientras corre (el buffer se vacía al
  volver), reiniciar la Raspberry (la config sobrevive) y recibir un comando
  duplicado (se re-confirma sin volver a aplicarlo).
- Criterio de cierre: los escenarios P1–P7 de `DOC_PRUEBA_ACL_EDGE_DEV.docx`
  pasan sin que nadie entre por SSH.
- Entregable: `raspberry/tests/integration/`.

## 2. Reglas del contrato que el edge_agent debe cumplir

### 2.1 Heartbeat y estado del dispositivo (M03 — Algoritmo Único de Evaluación de Estado)

El servidor no maneja un estado "offline", sino una escalera según el tiempo
sin contacto, contado desde `MAX(último heartbeat, última telemetría válida)`:

| Estado | Condición | Alerta |
|---|---|---|
| `ACTIVO` | sin contacto ≤ `umbral_activo = max(intervalo_heartbeat, frecuencia_muestreo)` | — |
| `SIN_SEÑAL` | > `umbral_activo + margen_tolerancia` y buffer local no activo | MODERADO |
| `INACTIVO` | > `umbral_sin_señal + tiempo_inactividad_extendida` (ej. 6 h) | CRÍTICO |
| `BUFFER_ACTIVO` | offline intencional declarado por el dispositivo | ninguna |

Reglas de RF-60 (Restricción 12), todas configurables desde M09 — la
Restricción 1 prohíbe valores fijos en el sistema:

- `intervalo_heartbeat ≤ frecuencia_muestreo / 2`
- `margen_tolerancia ≥ max(intervalo_heartbeat, frecuencia_muestreo × 0.5)`
- `umbral_sin_señal = max(intervalo_heartbeat, frecuencia_muestreo) + margen_tolerancia`
- `umbral_inactivo = umbral_sin_señal + tiempo_inactividad_extendida`

Referencias: frecuencias de muestreo válidas 5/10/15/30 min (precondición
2); CA-20 resuelto: heartbeat 5, muestreo 10, margen 5 → `SIN_SEÑAL` a los
15 min.

El período de emisión del heartbeat **no viaja en RF-23** (entradas:
`dispositivo_iot_id`, `frecuencia_captura`, `intervalo_transmision`). Según
RF-18, los parámetros de M09 son referencia del lado servidor y no
configuran el dispositivo. RF-23 permite bajar `frecuencia_captura` a 5 min
sin validarlo contra el heartbeat.

Consecuencias para el firmware:
- **Emitir más seguido que lo que asume el servidor nunca es un problema**
  (más contacto → sigue `ACTIVO`). Emitir menos seguido sí lo es. Por eso
  el período efectivo es:
  `heartbeat_efectivo = min(HEARTBEAT_INTERVAL, frecuencia_captura / 2)`,
  recalculado con cada comando RF-23. Así la regla de RF-60 se cumple del
  lado del dispositivo aunque RF-23 baje la captura a 5 min (→ 2.5 min).
- `HEARTBEAT_INTERVAL` es configurable (provisión en
  `/etc/sgpmp/edge-agent.env`), no una constante en el código (RF-60 R1).
- Si RF-23 llega a incluir `intervalo_heartbeat` en el comando (opción B de
  la sección 4, punto 2), el firmware ya lo acepta como campo opcional y lo usa en
  lugar de `HEARTBEAT_INTERVAL` dentro del mismo `min(...)`.
- El heartbeat se programa **independiente de `intervalo_transmision`**.
- El firmware no valida `frecuencia_captura` contra el conjunto 5/10/15/30
  (sería un valor fijo en el dispositivo): solo exige entero > 0. La
  validación de dominio es de M09/RF-23.

Buffer local y `BUFFER_ACTIVO` (RF-60 Paso 2, E5, CA-18, CA-21):
- Enum `INACTIVO | ACTIVO | LLENO`. Solo `ACTIVO` en el último heartbeat
  válido lleva al estado `BUFFER_ACTIVO`. Se sale a `ACTIVO` cuando termina
  la sincronización y el buffer queda vacío.
- **Nombre del campo (decidido):** se usa `estado_local_buffer`, que es
  el nombre del broker (`HeartbeatPayload`) y de la columna
  `modulo3.heartbeats.estado_local_buffer`. La especificación (RF-60, que
  dice `estado_buffer_local`) se corrige para alinearse con la
  implementación. Ojo: Pydantic descarta sin error los campos desconocidos,
  así que un nombre distinto se pierde sin aviso.
- Semántica en el edge_agent:
  - `INACTIVO`: buffer vacío.
  - `ACTIVO`: hay mensajes pendientes (`datos_pendientes_buffer` = N).
  - `LLENO`: se alcanzó el tamaño máximo configurado. Se descarta la
    telemetría más antigua y **nunca** un ACK.
- `BUFFER_ACTIVO` solo es posible si el dispositivo **lo anuncia antes** de
  quedar offline: publicar un heartbeat con `estado_local_buffer: "ACTIVO"`
  antes de un corte planificado. Un corte no planificado debe terminar en
  `SIN_SEÑAL`, que es el comportamiento correcto; no se "maquilla" con LWT.
- Secuencia al reconectar: heartbeat inmediato (`ACTIVO`, N pendientes) →
  vaciar el buffer → heartbeat con `INACTIVO`, para que el servidor vuelva
  a `ACTIVO` sin esperar el siguiente ciclo.
- Los heartbeats **no se guardan en el buffer ni se reenvían**: un
  heartbeat viejo no dice nada del estado actual. Al reconectar se emite
  uno nuevo.
- Si el serial es por ESP32, la Raspberry **solo** publica heartbeat de un
  serial si escuchó a ese nodo por LoRa dentro de su propio umbral. Si no, un
  ESP32 muerto seguiría apareciendo `ACTIVO` gracias a la Raspberry.
- La telemetría reenviada desde buffer conserva su `timestamp_captura`
  original. Si M03 cuenta la "última telemetría válida" por captura, el
  reenvío no refresca el estado; el heartbeat post-reconexión sí.

### 2.2 Comando y ACK (RF-23)

Estado actual en SGPMP: `POST /v1/commands` es síncrono, espera 30 s y, si
no llega el ACK, responde `NO_CONF`. Un ACK tardío se registra en el log del
broker y se descarta. El ACK se correlaciona **solo por serial**.

Diseño M09: `ENTREGADO → TIMEOUT → REINTENTADO (×3) → FALLIDO` + notificación
al administrador, con ACK que incluye `comando_id` y
`config_version_aplicada`, e idempotencia por `event_id`.

ACK que publica el edge_agent (compatible hacia atrás, porque el broker hoy
solo mira `tipo_mensaje` y `resultado` e ignora campos extra):

```json
{
  "tipo_mensaje": "ACK_CONFIGURACION",
  "resultado": "OK",
  "comando_id": "<eco del comando, si viene>",
  "config_version_aplicada": "<eco del comando, si viene>",
  "event_id": "<uuid generado por el edge>"
}
```

Reglas del lado del firmware:
- `comando_id`/`config_version_aplicada` se devuelven como eco **solo si el
  comando los trae**; mientras el broker no los envíe, se omiten.
- Idempotencia local: `config_store` guarda el último `comando_id` y la
  `config_version` aplicada. Un duplicado (redelivery QoS 1 o reintento M09)
  se vuelve a confirmar sin reaplicarse. Una versión menor a la vigente se
  descarta y se confirma con la versión vigente.
- El ACK **siempre** se envía, aunque hayan pasado más de 30 s (por ejemplo,
  comando encolado durante un corte). Si no hay conexión, va al buffer
  persistente como cualquier otro mensaje.
- `event_id` se genera al crear cada mensaje (ACK, telemetría en
  `metadatos.event_id`) y se conserva en el buffer, para que un reenvío
  lleve el mismo id.
- Valores inválidos (≤ 0, fuera de rango) → `resultado: "ERROR"` con un
  campo `motivo`. Hoy el broker lo trata como timeout; queda como propuesta
  para M09.

Fuera de alcance de este repo (lado servidor): pasar de `NO_CONF` a
`APLICADA` de forma asíncrona con un ACK tardío, los reintentos ×3 y el
reenvío de comandos pendientes al reconectar.

### 2.3 Impacto en el protocolo LoRa

Si el serial es por ESP32 y la config baja por downlink, el ESP32 duerme
entre capturas (minutos), así que su confirmación **no puede llegar dentro
de 30 s**. Con el broker actual, eso obliga a una de estas dos opciones:
- La Raspberry confirma al recibir y encolar ("aceptado"), y el ESP32 aplica
  en su próxima ventana. Hoy no hay forma de reportar esa aplicación real.
- Se espera al ACK asíncrono de M09 (sección 2.2) antes de habilitar el
  downlink por nodo.

Esto refuerza evaluar primero el serial por sitio (`PROTOCOLO_LORA.md` §6).

## 3. Fases LoRa (dependen de la Fase 0)

### Fase 0 — Protocolo LoRa (bloqueante, sin código todavía)
- Definir banda/frecuencia según regulación de radiofrecuencia en el país de
  despliegue (asumido Colombia → banda ISM 915 MHz; **confirmar con
  regulación local antes de fijar la frecuencia en firmware**, no asumir EU868).
- Definir formato de trama (header + payload + CRC), direccionamiento por
  nodo ESP32, esquema de reintento/ACK (LoRa no es fiable como IP).
- Definir si hay downlink (Raspberry → ESP32), considerando la sección 2.3.
- Entregable: `docs/PROTOCOLO_LORA.md` firmado/revisado por el equipo de
  hardware antes de pasar a Fase 1.

### Fase 1 — Firmware ESP32 (nodo sensor)
- Lectura de sensores según `frecuencia_captura` vigente (default hasta que
  llegue la primera config).
- Empaquetado y envío de tramas LoRa uplink al gateway.
- Recepción de downlink (si Fase 0 lo define) para actualizar su propia
  frecuencia de captura.
- Modo bajo consumo entre capturas (estos nodos son a batería).
- Entregable: `esp32/` — proyecto PlatformIO, sin lógica de MQTT.

### Fase 2 — Driver LoRa + listener en la Raspberry
- Recepción continua de tramas LoRa (SPI, `pySX127x` o equivalente),
  validación de CRC, decodificado.
- Reemplaza a `FakeLoraSource` detrás de la misma interfaz que usa M1.
- Entregable: `raspberry/edge_agent/lora_receiver.py`.

### Fase 3 — Pruebas de campo con hardware real
- Repetir un set de pruebas equivalente a P1-P7 pero para el enlace LoRa:
  pérdida de trama, fuera de rango, colisión con otro nodo, batería baja.
- Prueba de extremo a extremo real: cambio de config desde la web →
  Raspberry → (ESP32 por LoRa, si aplica) → ACK MQTT real.

## 4. Pendientes a decidir

Lado servidor / equipo SGPMP (especificación):
1. **Contradicción RF-60 vs RF-18.** RF-60 (M03) define `intervalo_heartbeat`
   como período de emisión (`≤ frecuencia_muestreo/2`, por tipo de
   dispositivo, con `margen_tolerancia`). RF-18 (M09) lo define como "tiempo
   máximo sin datos" (`≥ frecuencia_muestreo`, global, sin
   `margen_tolerancia`). Con RF-18 tal cual, M09 rechaza toda configuración
   válida según RF-60. Requiere RFC. El firmware no queda bloqueado: su
   regla de la sección 2.1 es segura con cualquiera de las dos.
2. **Cómo se acuerda el período del heartbeat.** Opción A: valor acordado
   con firmware, y RF-23 rechaza `frecuencia_captura < 2 × intervalo_heartbeat`.
   Opción B (recomendada): agregar `intervalo_heartbeat` como parámetro de
   RF-23 por tipo de dispositivo; requiere agregar el campo a
   `CommandRequest` y al payload en el broker. El firmware ya soporta B.
3. RF-49 (M02) fija "30 minutos sin heartbeat", lo que choca con RF-60 R1.
4. Corregir RF-60: `estado_buffer_local` → `estado_local_buffer` (decidido:
   manda la implementación del broker/BD).
5. Contrato de ACK extendido (`comando_id`, `config_version`, `event_id`) y
   transición asíncrona `NO_CONF → APLICADA` (sección 2.2).
6. `persistence true` en Mosquitto, para que los comandos encolados
   sobrevivan un reinicio del broker.
7. Fecha de TLS en `dev`. Requisito para salir a campo con la credencial
   compartida.

Lado edge / hardware:
8. Frecuencia LoRa exacta y regulación aplicable.
9. Serial MQTT: ¿por Raspberry o por ESP32?
10. Conectividad IP de la Raspberry en campo (WiFi / Ethernet / 4G) —
   dimensiona el buffer.
11. Mecanismo de actualización remota del edge_agent (release firmado +
    timer, paquete `.deb`, o VPN de emergencia).
12. Si el equipo de hardware ya tiene un formato de trama LoRa propio, este
    plan debe referenciarlo en vez de proponer uno nuevo.

## 5. Convenciones de este repo

- Git Flow igual que `BROKER-MQTT-SGPMP`: `main`/`develop` protegidas, todo
  entra por `feature/*` + Pull Request, sin commit directo. (Pendiente:
  el repo aún solo tiene `master` con el commit de bootstrap).
- Una feature = un hito o un sub-punto = una tarjeta de Taiga.
- El contrato MQTT (topics/payload) **no se redefine acá**: cualquier cambio
  se acuerda primero con el equipo de `BROKER-MQTT-SGPMP`. Los campos extra
  del ACK (sección 2.2) son una propuesta compatible hacia atrás, no un
  cambio unilateral.
- Credenciales MQTT nunca en el repo (ni en `.env` versionado, ni
  hardcodeadas en `.cpp`/`.py`) — mismo criterio de SEG-BROKER-01.
