# RF-55 — Evento Edge: contrato y trazabilidad de casos de prueba

Este documento es para los equipos de broker, backend y QA. Describe qué
publica el Gateway Edge (la Raspberry, `edge-agent`) cuando su motor de reglas
RF-55 clasifica una lectura, cómo recibe las reglas y cómo reproducir cada caso
sin hardware.

RF-55 es un motor de reglas **determinista** que corre en el Edge, no un motor
de inferencia de ML (eso es RF-66, fuera de alcance):

| Nivel | Qué hace | Obligatoriedad |
|---|---|---|
| RF-55.1 | Umbrales simples por variable (los de RF-17) | Siempre activo |
| RF-55.2 | Reglas compuestas con ventana temporal | Según el perfil de hardware (§6) |

## Flujo construido

```
Umbral RF-17  → POST /v1/commands {origen: "umbral"}           → sgpmp/<Edge>/command (UMBRAL_AMBIENTAL)
Regla RF-55   → POST /v1/commands {origen: "regla_compuesta"}  → sgpmp/<Edge>/command (REGLA_COMPUESTA)
                                     ← sgpmp/<Edge>/status  (ACK_UMBRAL / ACK_REGLA)

Lecturas LoRa → telemetría (sgpmp/<nodo>/telemetry)       [siempre, primero]
              → RF-55 → evento (sgpmp/<nodo>/evento)     [solo si hay desviación]
              → broker → POST /iot/eventos-edge
```

`<nodo>` es el serial del **nodo sensor** dueño de la variable primaria (la
primera de `variables_involucradas`), no el del Gateway.

## 1. Comando `REGLA_COMPUESTA` y su ACK

En `sgpmp/<serial Edge>/command`:

| `tipo_comando` | Qué es | ACK |
|---|---|---|
| ausente | Configuración RF-23 | `ACK_CONFIGURACION` |
| `UMBRAL_AMBIENTAL` | Umbral RF-17 | `ACK_UMBRAL` |
| `REGLA_COMPUESTA` | Regla RF-55 de `modulo3.reglas_alertas` | `ACK_REGLA` |
| `CONFIG_MOTOR_IA` / `TIPO_MODELO_ASIGNADO` | RF-65/RF-66, no implementados | `ACK_CONFIG_MOTOR` / `ACK_TIPO_MODELO` con `resultado: "ERROR"`, motivo "tipo de comando no soportado" |
| cualquier otro | — | `ACK_CONFIGURACION` con `resultado: "ERROR"` |

Cuerpo que publica el broker (`dispatch.py`):

```json
{
  "id_comando": "…", "emitido_en": "2026-10-26T14:00:00+00:00",
  "tipo_comando": "REGLA_COMPUESTA",
  "id_regla_alerta": 12, "version_regla": 3, "es_activa": true,
  "regla": { "nombre": "Estres termico por THI", "tipo_sensor": "TEMPERATURA", "…": "…" }
}
```

| Situación | ACK | ¿Se guarda? |
|---|---|---|
| Regla válida, versión nueva | `OK` | Sí, en `reglas.json` |
| Mismo `id_comando` (reentrega QoS 1) | `OK` | No |
| `version_regla` menor o igual a la vigente | `OK` | No (ya rige algo igual o más nuevo) |
| `es_activa: false` | `OK` | Se retira la regla |
| Campos de cabecera inválidos (`id_regla_alerta`, `version_regla`, `es_activa`, `regla`) | `ERROR` + `motivo` | No |
| `regla` que no se puede evaluar (§4) o que rompe un límite (§5) | `ERROR` + `motivo` "regla inválida: …" | No |
| Ya hay 5 reglas activas y llega una sexta | `ERROR` + `motivo` | No |
| `emitido_en` vencido (con NTP) | `ERROR` | No |

Una reentrega o versión vieja se confirma `OK` a propósito: un `ERROR` marcaría
como fallida en `sincronizaciones_reglas_edge` una sincronización que sí llegó.

## 2. Evento publicado

Topic `sgpmp/<serial nodo>/evento`, QoS 1. Ejemplo (caso C1 de la tabla):

```json
{
  "clasificacion_rf55": "DESVIACION_COMPUESTA",
  "severidad": "MODERADO",
  "variables_involucradas": [
    {"tipo_variable": "temperatura_ambiente", "valor": 32.5, "unidad": "C",
     "timestamp_captura": "2026-10-26T14:30:00.000Z", "serial": "TC-M09-XXXX-0001", "sensor": "dht22_ambiente"},
    {"tipo_variable": "humedad_relativa", "valor": 78.0, "unidad": "%",
     "timestamp_captura": "2026-10-26T14:30:02.000Z", "serial": "TC-M09-XXXX-0001", "sensor": "dht22_ambiente"}
  ],
  "timestamp_captura": "2026-10-26T14:30:00.000Z",
  "timestamp_procesamiento_edge": "2026-10-26T14:30:02.004Z",
  "origen": "TIEMPO_REAL",
  "estado_conectividad": true,
  "metadata_edge": {
    "id_evento": "edge-evt-7a8b9c0d-1e2f-3a4b-5c6d-7e8f9a0b1c2d",
    "regla_aplicada": "ESTRES_TERMICO",
    "id_regla_alerta": 12, "version_regla": 3,
    "nombre_regla": "Estres termico por THI",
    "valores_derivados": {"THI": 81.3},
    "ventana_temporal_ms": 2000,
    "tiempo_procesamiento_ms": 0.108,
    "version_firmware": "v0.3.0"
  }
}
```

| Campo | Valores | Nota |
|---|---|---|
| `clasificacion_rf55` | `DESVIACION_SIMPLE`, `DESVIACION_COMPUESTA`, `ERROR_CONFIGURACION` | `NORMAL` nunca se emite: un evento por muestra saturaría el rate limit del backend |
| `severidad` | `LEVE`, `MODERADO`, `CRITICO` | Ausente en `ERROR_CONFIGURACION` |
| `origen` | `TIEMPO_REAL`, `BUFFER_LOCAL` | `BUFFER_LOCAL` si salió más de 60 s después de evaluarse (sin red) |
| Timestamps | ISO-8601 UTC con `Z` y milisegundos | |
| `tipo_variable` | `variable` de la telemetría (nombres de `modulo9`) | |
| `sensor`, `serial` por variable | | El backend aún no los persiste (BE-1); el broker los usa para resolver `sensor_id` |
| `metadata_edge.regla_aplicada` | `UMBRAL_AMBIENTAL`, `VENTANA_CONFIRMACION`, código de fórmula (`ESTRES_TERMICO`) o `null` si la regla no se pudo interpretar | |
| `metadata_edge.tiempo_procesamiento_ms` | Medido alrededor de la evaluación | |
| `metadata_edge.version_firmware` | El mismo SemVer del heartbeat (con prefijo `v`) | |

Solo en eventos de umbral simple: `id_umbral_ambiental`, `version_umbral` y
`nivel_umbral` (`precaucion`, `critico`, `fuera_de_rango`). Solo en Tipo A:
`lectura_confirmacion_max`, `ventana_confirmacion_min`. En
`ERROR_CONFIGURACION` de una regla: `motivo`.

**Entrega y buffer.** El evento entra al mismo outbox SQLite que la telemetría,
sin esperar la ventana de transmisión, y se borra con el PUBACK: sin red queda
retenido y sale en orden al reconectar. Con el buffer lleno se descartan
lecturas, nunca eventos ni ACKs. Un evento con más de 72 h de antigüedad se
descarta localmente con log (el backend lo rechazaría con `TIMESTAMP_EXPIRADO`).
Un fallo del motor de reglas se registra y no afecta la captura ni la
telemetría, que se encola antes de evaluar.

## 3. RF-55.1 — umbrales simples

Mismas bandas que el broker y el semáforo del backend: los niveles de RF-17 son
contiguos y cubren `[valor_min, valor_max]`.

| Lectura | Clasificación | Severidad |
|---|---|---|
| En banda `normal` | (no emite) | — |
| En banda `precaucion` | `DESVIACION_SIMPLE` | `MODERADO` |
| En banda `critico` o fuera de todas | `DESVIACION_SIMPLE` | `CRITICO` |
| En el límite entre dos bandas | la más grave de las dos | |
| Variable sin umbral en este Edge | (no emite; no es error) | — |
| Umbral guardado que no se puede usar | `ERROR_CONFIGURACION` (una vez por umbral y versión) | — |

Con varios umbrales para la misma variable (una especie por umbral) rige el más
restrictivo (RFC-012).

## 4. RF-55.2 — reglas compuestas

`modulo3.reglas_alertas` modela una sola variable por regla, así que el Edge
interpreta la regla de dos formas (§6 de `PLAN_RF55_00_CONTEXTO_Y_CONTRATOS.md`):

**Tipo B — fórmula del catálogo.** Si el `nombre` normalizado (mayúsculas, sin
tildes, `_` como separador) es una fórmula conocida o uno de sus alias, se
aplica esa fórmula. Catálogo cerrado en firmware: no se evalúan expresiones
llegadas por la red. Hoy:

| Código | Alias | Variables | Resultado |
|---|---|---|---|
| `ESTRES_TERMICO` | `ESTRES_TERMICO_POR_THI`, `THI`, `INDICE_TEMPERATURA_HUMEDAD` | `TEMPERATURA_AMBIENTAL`, `HUMEDAD_RELATIVA` (+ `TEMPERATURA_CORPORAL`, `FRECUENCIA_RESPIRATORIA` para la escalada) | `valores_derivados.THI` |

`THI = T − (0.55 − 0.0055·HR)·(T − 14.5)`, con **T en °F** (una lectura en °C se
convierte). Es la escala en la que se definen los cortes:

| THI | Severidad |
|---|---|
| < 72 | no dispara |
| 72 ≤ THI < 79 | `LEVE` |
| 79 ≤ THI < 84 | `MODERADO` |
| ≥ 84 | `CRITICO` |

Se clasifica con el THI redondeado a dos decimales, el mismo que se reporta.
**Escalada:** con severidad ≥ `LEVE`, si `temperatura_corporal` > 40 °C o
`frecuencia_respiratoria` está en `MODERADO` o peor según su umbral RF-17, sube
exactamente un nivel (`CRITICO` es tope); esas lecturas se agregan a
`variables_involucradas`. `umbrales_severidad` puede cambiar los cortes:
`{"LEVE": 72, "MODERADO": 79, "CRITICO": 84, "temperatura_corporal_max": 40}`.

Todas las variables requeridas deben estar dentro de la ventana de correlación
(30 s; `ventana_correlacion_s` en la regla para acortarla). Si falta alguna, no
dispara y no es error. La variable que disparó la evaluación va primera.

**Tipo A — ventana de confirmación** (cualquier otro `nombre`). La variable es
`tipo_sensor` + `categoria_variable` (ej. `TEMPERATURA_AMBIENTAL`); dispara
`DESVIACION_COMPUESTA` cuando la lectura sale de `[umbral_min, umbral_max]`
`lectura_confirmacion_max` veces dentro de `ventana_confirmacion_min` minutos, y
la cuenta se reinicia. `ventana_confirmacion_min = 0` significa "sin ventana"
(la columna es NOT NULL en el backend) y solo se acepta con
`lectura_confirmacion_max = 1`. Severidad: `umbrales_severidad.severidad`, o `MODERADO`.
Los números pueden venir como texto (Decimal serializado del backend).

**Nombres de variable.** Las reglas usan nombres canónicos; el Edge los traduce
a la `variable` de la telemetría con un mapa configurable
(`EDGE_RF55_VARIABLES`); por defecto `TEMPERATURA_AMBIENTAL=temperatura_ambiente`,
`HUMEDAD_RELATIVA=humedad_relativa`, `TEMPERATURA_CORPORAL=temperatura_corporal`,
`FRECUENCIA_RESPIRATORIA=frecuencia_respiratoria`.

**Prevalencia.** Si una compuesta con `prevalece_sobre_regla_simple: true`
dispara, no se emite el `DESVIACION_SIMPLE` de ninguna de sus lecturas (RF-57
E14/E15). Se evalúa la captura completa para que eso funcione.

## 5. Límites duros

Se rechazan al **recibir** la regla (ACK `ERROR`), no al evaluarla:

| Límite | Valor |
|---|---|
| Reglas activas simultáneas | ≤ 5 |
| Variables correlacionadas por regla | ≤ 3 |
| Ventana de correlación (Tipo B) | ≤ 30 s |
| `lectura_confirmacion_max` (Tipo A) | 1–100 |

La ventana de confirmación del Tipo A va en minutos y no tiene el tope de
30 s. Latencia medida: RF-55.1 ≤ 500 ms por lectura y RF-55.2 ≤ 200 ms
adicionales (en la práctica, menos de 1 ms). Una regla guardada que no se puede
interpretar produce `ERROR_CONFIGURACION` una sola vez, nunca una excepción.

## 6. Perfil de hardware

Al arrancar se verifica CPU ≥ 160 MHz, RAM disponible ≥ 256 KB y
almacenamiento libre ≥ 1 MB. Si algo no alcanza, RF-55.2 se apaga (WARNING con
el recurso), RF-55.1 sigue y el arranque nunca falla. `EDGE_RF55_2_DESHABILITAR=1`
lo apaga a mano. El heartbeat lo informa en `sensores_operativos`:
`{"sensor": "rf55_2", "operativo": true|false}`.

## 7. Casos de prueba (QA, sin hardware)

```bash
cd raspberry
python scripts/inyectar_lecturas.py scripts/casos_rf55.json         # todos
python scripts/inyectar_lecturas.py scripts/casos_rf55.json -v \
    --caso "C1 vector THI (32.5 C, 78 %)"                            # uno, con el evento
```

El inyector corre el agente real con lecturas guionadas, le envía umbrales y
reglas por el topic `command` y compara los eventos publicados. Los mismos
casos corren en `pytest` (`tests/integration/test_rf55_extremo_a_extremo.py`).
Umbral de los casos `S*`: `temperatura_ambiente` normal 18–24, precaución
24–28, crítico 28–32. Regla global: `ESTRES_TERMICO` con prevalencia.

| Caso | Entrada | Regla | `clasificacion_rf55` | `severidad` | `valores_derivados` |
|---|---|---|---|---|---|
| S1 | T = 21.0 °C | Umbral | (sin evento) | — | — |
| S2 | T = 26.0 °C | Umbral | `DESVIACION_SIMPLE` | `MODERADO` | — |
| S3 | T = 30.0 °C | Umbral | `DESVIACION_SIMPLE` | `CRITICO` | — |
| S4 | T = 15.0 °C (fuera de bandas) | Umbral | `DESVIACION_SIMPLE` | `CRITICO` | — |
| S5 | T = 24.0 °C (borde) | Umbral | `DESVIACION_SIMPLE` | `MODERADO` | — |
| S6 | T = 28.0 °C (borde) | Umbral | `DESVIACION_SIMPLE` | `CRITICO` | — |
| S7 | T = 18.0 °C (borde inferior) | Umbral | (sin evento) | — | — |
| S8 | `humedad_suelo` = 99 | Sin umbral | (sin evento) | — | — |
| C1 | T = 32.5 °C, HR = 78 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `MODERADO` | THI ≈ 81.3 |
| C2 | T = 72 °F, HR = 100 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `LEVE` | THI = 72.0 |
| C3 | T = 79 °F, HR = 100 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `MODERADO` | THI = 79.0 |
| C4 | T = 84 °F, HR = 100 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` | THI = 84.0 |
| C5 | T = 80 °F, HR = 100 %, T corporal = 41 °C | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` (escalada) | THI = 80.0 |
| C6 | T = 72 °F, HR = 100 %, FR = 50 rpm (precaución) | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` (sin simple de FR) | `MODERADO` (escalada) | THI = 72.0 |
| C7 | Solo T = 32.5 °C | `ESTRES_TERMICO` | (sin evento, sin error) | — | — |
| C8 | T en t = 0 s, HR en t = 31 s | `ESTRES_TERMICO` | (sin evento) | — | — |
| C9 | T en t = 0 s, HR en t = 29 s | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA`, HR primera | `MODERADO` | THI ≈ 81.3 |
| C10 | T = 32.5 °C (crítico por umbral), HR = 78 % | Umbral + `ESTRES_TERMICO` | solo `DESVIACION_COMPUESTA` | `MODERADO` | THI ≈ 81.3 |
| A1 | T = 31 °C en 0, 60 y 120 s | Tipo A: máx 30, 3 en 5 min | `DESVIACION_COMPUESTA` (a la 3.ª) | `MODERADO` | — |
| A2 | T = 31 °C en 0, 3, 6 y 9 min | Tipo A: máx 30, 3 en 5 min | (sin evento) | — | — |
| L1 | 6 reglas activas | — | sexta: `ACK_REGLA` `ERROR` | — | — |
| L2 | `ventana_correlacion_s` = 45 | `ESTRES_TERMICO` | `ACK_REGLA` `ERROR` | — | — |

Para un caso nuevo, agregarlo a `scripts/casos_rf55.json` (formato en el
encabezado de `inyectar_lecturas.py`) y a esta tabla.

## 8. Pendiente fuera del Edge

- **BE-1:** persistir `sensor`/`serial` de `variables_involucradas` (hoy
  `VariableInvolucradaDTO` los ignora sin error).
- **BE-5:** modelar reglas multivariable en `modulo3.reglas_alertas`; hasta
  entonces las fórmulas viven en el catálogo del firmware.
- Merge a `main`: después del broker y del `BE-0` del backend (§7 del documento
  de contratos). Si el firmware llega primero, los eventos se rechazan.
