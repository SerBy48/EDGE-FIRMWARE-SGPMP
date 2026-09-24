# Protocolo LoRa — Raspberry (SX1276) ↔ ESP32 (SX1276)

**Estado: borrador, sin código escrito todavía.** Es el entregable de la
Fase 0 de `PLAN_DESARROLLO.md`, y bloquea el resto de fases hasta que el
equipo de hardware lo revise. Las secciones marcadas `(pendiente)` no deben
codificarse hasta confirmarlas.

## 1. Topología

Estrella punto-a-multipunto: 1 Raspberry (gateway del sitio) escucha a N
ESP32 (nodos sensores). No es mesh — un ESP32 nunca reenvía tramas de otro.

## 2. Capa física (pendiente de confirmar)

| Parámetro | Valor propuesto | Nota |
|---|---|---|
| Banda | 915 MHz (ISM) | Asumido para Colombia — **confirmar con la regulación de espectro vigente antes de fijarlo en firmware**, no asumir la banda europea 868 MHz |
| Spreading Factor | SF7–SF9 | A definir según alcance real del sitio (SF más alto = más alcance, menos throughput, más tiempo en aire) |
| Ancho de banda | 125 kHz | Default típico SX1276, ajustar si el alcance de campo lo requiere |
| Potencia TX | Según límite regulatorio local | No asumir el máximo de la radio sin confirmar el límite legal |

## 3. Direccionamiento

- Cada ESP32 tiene un `node_id` propio (1 byte o 2 bytes, según cuántos nodos
  por sitio se esperen — definir el máximo realista de sensores por
  Raspberry antes de fijar el tamaño).
- La Raspberry conoce la lista de `node_id` esperados en su sitio (config
  local, no hardcodeada — un sitio nuevo no debe requerir reflashear la
  Raspberry).
- **Pendiente de decidir (bloquea Fase 1):** si `node_id` mapea 1:1 a un
  `serial` MQTT propio (`modulo9.dispositivos_iot`), o si todos los `node_id`
  de un sitio se agregan bajo el `serial` MQTT de la Raspberry. Ver sección 0
  de `PLAN_DESARROLLO.md`.

## 4. Formato de trama (borrador)

```
┌─────────┬─────────┬─────────┬──────────────────┬─────────┐
│ node_id │ msg_type│  seq    │      payload      │  CRC    │
│ 1-2 B   │  1 B    │  1 B    │   N bytes (TBD)   │  2 B    │
└─────────┴─────────┴─────────┴──────────────────┴─────────┘
```

- `msg_type`: `0x01` = telemetría, `0x02` = heartbeat/estado del nodo,
  `0x03` = ACK de config (si hay downlink), `0x04` = config downlink
  (Raspberry → ESP32, si Fase 0 lo confirma).
- `seq`: número de secuencia por nodo, para detectar tramas perdidas/duplicadas
  del lado de la Raspberry (LoRa no garantiza entrega).
- `payload`: formato binario compacto por definir junto al equipo que ya
  eligió el sensor específico — no se propone acá porque depende del
  hardware de sensado, no del enlace.
- `CRC`: validación de integridad antes de que la Raspberry acepte la trama.

## 5. Esquema de confiabilidad (pendiente)

LoRa no es como MQTT/TCP: no hay QoS ni reintento automático. Opciones a
evaluar con el equipo antes de implementar:

1. **Fire-and-forget con `seq`:** el ESP32 envía y no espera respuesta; la
   Raspberry detecta huecos en `seq` y los reporta en el heartbeat agregado
   (pérdida de tramas queda visible, no se recupera).
2. **ACK LoRa simple:** la Raspberry responde con un ACK corto tras recibir
   cada trama; el ESP32 reintenta N veces si no lo recibe. Consume más
   batería y más tiempo en aire (revisar límites regulatorios de duty cycle
   si aplican en la banda elegida).

Recomendación para la primera iteración: opción 1 (más simple, menor consumo
en el ESP32), con la pérdida de tramas visible como métrica en el heartbeat
que la Raspberry sube por MQTT — permite medir qué tan seguido hace falta la
opción 2 antes de construirla.

## 6. Downlink (Raspberry → ESP32) — solo si Fase 0 lo confirma

Si el `serial` MQTT es por ESP32 (no por sitio agregado), la Raspberry
necesita reenviar `frecuencia_captura` a cada ESP32 individualmente cuando
llega el comando MQTT `sgpmp/<serial>/command`. Eso implica:

- La Raspberry transmite (no solo escucha) — revisar si el mismo módulo
  SX1276 alterna TX/RX sin conflicto con la recepción continua de los demás
  nodos (es half-duplex, hay que diseñar la ventana de transmisión).
- El ESP32 debe confirmar aplicación (`msg_type 0x03`) antes de que la
  Raspberry publique el ACK MQTT real en `sgpmp/<serial>/status` — replica a
  nivel LoRa el mismo patrón comando/ACK que ya se validó a nivel MQTT en
  `DOC_PRUEBA_ACL_EDGE_DEV.docx`.

Si el `serial` es por sitio (agregado en la Raspberry), esta sección no
aplica: la Raspberry aplica la config a su propio agregado y no necesita
downlink LoRa en absoluto — opción más simple, evaluar primero.
