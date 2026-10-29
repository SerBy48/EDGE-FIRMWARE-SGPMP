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
                 hilo LoRa                         hilo principal (agent.py)                 hilo paho
┌──────────────────────────────┐   lecturas   ┌──────────────────────────────┐  eventos  ┌──────────────────┐
│ lora/gateway.py              │ ───────────▶ │ agenda captura/tx/heartbeat  │ ◀──────── │ mqtt_client.py   │
│  sx1276.py (SPI)             │              │ commands.py → config_store   │           │ sesión           │
│  protocol.py (tramas v1)     │ ◀─────────── │   y reglas/store (RF-55)     │ ────────▶ │ persistente,     │
│  dedupe/pérdidas por seq     │  config      │ reglas/evaluador (RF-55)     │  publish  │ QoS 1, TLS opc.  │
│  downlink CONFIG en ventana  │  deseada     │ buffer.py (outbox SQLite)    │           │                  │
│                              │              │ heartbeat (estado buffer)    │           │                  │
└──────────────────────────────┘              └──────────────────────────────┘           └──────────────────┘
   (o FakeLoraSource: datos sintéticos, EDGE_SOURCE=fake)      │ sd_notify (watchdog de systemd)
```

- Solo el hilo principal toca SQLite y la config; los otros hilos entregan
  eventos y lecturas por colas o con un lock.
- Todo lo que debe llegar al broker (telemetría, eventos Edge, ACK) pasa
  primero por el buffer y se borra con el PUBACK: sobrevive cortes de red y
  reinicios.
- Las escrituras de una captura (buffer e historial) van en una transacción, y
  cada racha de PUBACK también: un fsync por bloque, no por fila. Cada vuelta
  del loop captura a lo sumo `PRESUPUESTO_CAPTURAS_S` (200 ms, al menos un
  nodo); con una ráfaga, el resto queda para la vuelta siguiente y entretanto
  se atienden comandos y PUBACK (TC-M03-102, `CONTRATO_EVENTO_EDGE.md` §6.2).

Ningún módulo de este proceso toca directamente la base de datos ni el
backend HTTPS — esa separación de capas ya vive en `BROKER-MQTT-SGPMP` y no
se duplica acá (mismo principio que su `AGENTS.md`, aunque este es un repo
distinto).

## Motor de reglas RF-55 (`edge_agent/reglas/`)

Clasifica cada captura en el Edge antes de enviarla. Contrato y casos:
`CONTRATO_EVENTO_EDGE.md`.

| Módulo | Rol |
|---|---|
| `store.py` | `ReglaStore`: reglas de `REGLA_COMPUESTA` en `reglas.json` (archivo propio; un rollback del updater no lo lee y el resto del estado queda intacto) |
| `evaluador.py` | Funciones sin I/O: `evaluar_simple` (RF-55.1, umbrales RF-17), `evaluar_lote` (captura completa, RF-55.2 y prevalencia), `validar_regla` (límites al recibir) y `motivo_niveles` (forma válida de un umbral, también al recibirlo) |
| `formulas.py` | Catálogo cerrado de fórmulas Tipo B (`ESTRES_TERMICO`, `CALIDAD_HIDRICA`, `CALIDAD_AIRE`), mapa de nombres canónicos a variables de telemetría y rangos físicos |
| `ventana.py` | Estado en memoria de RF-55.2: ventana de correlación (≤ 30 s) y conteos de confirmación Tipo A; acotado y no persistido |
| `evento.py` | Sobre del evento Edge (`a_payload`) |
| `perfil.py` | Perfil de hardware al arrancar: habilita o no RF-55.2 |
| `disyuntor.py` | Fallas de RF-55.2 en ejecución: alternativa RF-55.1 por captura, deshabilitación tras N fallas seguidas y reintento automático |

Fuera de `reglas/`: `historial.py` (log local de clasificaciones, `historial.db`)
e `inspeccionar.py` (CLI de solo lectura del buffer y del historial).

En `_capture` la telemetría se encola primero y la evaluación va en un
`try/except`: un fallo de RF-55 nunca tumba la captura ni el servicio. Dentro
de `evaluar_lote`, RF-55.2 va aislado: si falla, la captura entera se clasifica
con RF-55.1 y el disyuntor cuenta la falla. Los
eventos salen por `sgpmp/<serial nodo>/evento` sin esperar la ventana de
transmisión.

## Fuera de alcance de este repositorio

- Lógica de negocio de `modulo3`/`modulo9` (vive en el backend/BD, no acá).
- El contrato de payload MQTT en sí (vive en `BROKER-MQTT-SGPMP`; este repo
  lo consume, no lo redefine).
- Reenvío automático de comandos perdidos por desconexión — no implementado
  del lado del servidor, ver `PLAN_DESARROLLO.md` sección 2.2.
