# EDGE-FIRMWARE-SGPMP

Firmware/software del nodo edge de campo de la plataforma SGPMP: la
Raspberry (gateway LoRa de sitio) y los ESP32 (nodos sensores) que juntos
alimentan de datos al broker MQTT de `BROKER-MQTT-SGPMP`.

Repositorio separado de `BROKER-MQTT-SGPMP` a propósito: ese repo es el
gateway MQTT↔HTTPS (servidor), este es el software que corre en el hardware
de campo.

## Por dónde empezar

1. Leer `docs/ARQUITECTURA.md` — qué es cada componente y cómo se hablan.
2. Leer `docs/PROTOCOLO_LORA.md` — protocolo LoRa v1 (implementado,
   pendiente de revisión con el equipo de hardware).
3. Leer `docs/PLAN_DESARROLLO.md` — fases, estado y decisiones pendientes.
4. Leer `docs/PRUEBAS_CAMPO.md` — pruebas con hardware real (Fase 3).

El contrato MQTT (topics, payload, credenciales) que el `edge_agent` de la
Raspberry debe respetar está documentado en el repo `BROKER-MQTT-SGPMP`,
archivo `INTEGRACION_DISPOSITIVOS_RF23.md`, y ya fue validado end-to-end en
el ambiente dev (ver `DOC_PRUEBA_ACL_EDGE_DEV.docx` en ese repo).

## Estructura

```
docs/          Arquitectura, protocolo LoRa, plan de desarrollo
raspberry/     Servicio edge_agent (Python) — gateway LoRa↔MQTT
esp32/         Firmware de los nodos sensores (PlatformIO/C++)
```

## Estado

Implementados M1–M3 (enlace MQTT persistente, operación sin SSH, aceptación
automática), el protocolo LoRa v1 y las fases 1–2 (firmware ESP32 y gateway
LoRa de la Raspberry). Falta validarlo contra dev y con hardware real: ver la
tabla de estado en `docs/PLAN_DESARROLLO.md` y `docs/PRUEBAS_CAMPO.md`.
