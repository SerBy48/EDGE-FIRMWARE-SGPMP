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
| `regla.nombre` ausente, vacío o no texto (va en `nombre_regla`, TC-M03-199) | `ERROR` + `motivo` "regla inválida: nombre debe ser un texto no vacío" | No |
| Ya hay 5 reglas activas y llega una sexta | `ERROR` + `motivo` | No |
| `emitido_en` vencido (con NTP) | `ERROR` | No |

Una reentrega o versión vieja se confirma `OK` a propósito: un `ERROR` marcaría
como fallida en `sincronizaciones_reglas_edge` una sincronización que sí llegó.

## 2. Evento publicado

Topic `sgpmp/<serial nodo>/evento`, QoS 1. Ejemplo (caso C1 de la tabla):

```json
{
  "clasificacion_rf55": "DESVIACION_COMPUESTA",
  "severidad": "CRITICO",
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
    "valores_derivados": {"THI": 86.57},
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
| `metadata_edge.regla_aplicada` | `UMBRAL_AMBIENTAL`, `VENTANA_CONFIRMACION`, código de fórmula (`ESTRES_TERMICO`, `CALIDAD_HIDRICA`, `CALIDAD_AIRE`) o `null` si la regla no se pudo interpretar | |
| `metadata_edge.tiempo_procesamiento_ms` | Medido alrededor de la evaluación | |
| `metadata_edge.version_firmware` | El mismo SemVer del heartbeat (con prefijo `v`) | |

**Trazabilidad de la regla (TC-M03-199).** Todo `DESVIACION_COMPUESTA` la
genera el propio Edge, en `metadata_edge`, siempre con estos campos:

| Campo | Tipo | Obligatorio | Contenido |
|---|---|---|---|
| `regla_aplicada` | texto | Sí | Mecanismo que clasificó: código de fórmula (`ESTRES_TERMICO`, `CALIDAD_HIDRICA`, `CALIDAD_AIRE`) o `VENTANA_CONFIRMACION` (Tipo A) |
| `nombre_regla` | texto no vacío | Sí | `regla.nombre` exactamente como llegó en el comando (con tildes) |
| `version_regla` | entero ≥ 1 | Sí | `version_regla` vigente en el Edge al evaluar |
| `id_regla_alerta` | entero ≥ 1 | Sí | Identificador de la regla en `modulo3.reglas_alertas` |

El broker reenvía `metadata_edge` sin tocar estos campos (si agrega claves
propias y chocan, conserva las del Edge).

Solo en eventos de umbral simple: `id_umbral_ambiental`, `version_umbral` y
`nivel_umbral` (`precaucion`, `critico`, `fuera_de_rango`). Solo en Tipo A:
`lectura_confirmacion_max`, `ventana_confirmacion_min`. En
`ERROR_CONFIGURACION` de una regla: `motivo`. En `ERROR_CONFIGURACION` de un
umbral (§3): `motivo`, `variable`, `severidad_invalida: true`,
`id_umbral_ambiental` y `version_umbral` (`null` si la variable no tiene
umbral).

**Entrega y buffer.** El evento entra al mismo outbox SQLite que la telemetría,
sin esperar la ventana de transmisión, y se borra con el PUBACK: sin red queda
retenido y sale en orden al reconectar. Con el buffer lleno se descartan
lecturas, nunca eventos ni ACKs. Un evento con más de 72 h de antigüedad se
descarta localmente con log (el backend lo rechazaría con `TIMESTAMP_EXPIRADO`).
Un fallo del motor de reglas se registra y no afecta la captura ni la
telemetría, que se encola antes de evaluar.

## 2.1 Validación de entradas (TC-M03-081)

Antes de cualquier regla, cada lectura de la captura se valida. Una lectura
inválida **no se clasifica**: no entra a la ventana de correlación, no genera
`DESVIACION_*` ni `ERROR_CONFIGURACION` (el problema es el dato, no la regla), se
registra con WARNING y queda en el historial local como `LECTURA_INVALIDA` con
el motivo. Las demás lecturas válidas de la misma captura se clasifican igual.

| Entrada | Se considera inválida |
|---|---|
| Valor nulo, texto, booleano | Siempre |
| NaN, +∞, −∞ | Siempre |
| `humedad_relativa` | Fuera de [0, 100] % |
| `ph` | Fuera de [0, 14] |
| `oxigeno_disuelto` | Negativo |
| `nh3`, `co2` | Negativo |

Los rangos físicos se aplican por nombre canónico, así que respetan
`EDGE_RF55_VARIABLES`. Los bordes (0, 100, 14) son válidos.

Además, el Gateway LoRa descarta una medición NaN/∞ al decodificar la trama
(WARNING `valor no finito`) y el agente no encola como telemetría un valor que
no sea un número finito: todo lo que se publica es JSON válido. Un valor fuera
de rango físico sí viaja como telemetría cruda (lo valida el DTO del backend);
solo no se clasifica.

## 3. RF-55.1 — umbrales simples

Mismas bandas que el broker y el semáforo del backend: los niveles de RF-17 son
contiguos y cubren `[valor_min, valor_max]`.

| Lectura | Clasificación | Severidad |
|---|---|---|
| En banda `normal` | (no emite) | — |
| En banda `precaucion` | `DESVIACION_SIMPLE` | `MODERADO` |
| En banda `critico` o fuera de todas | `DESVIACION_SIMPLE` | `CRITICO` |
| En el límite entre dos bandas | la más grave de las dos | |
| Variable sin umbral en este Edge (TC-M03-083) | `ERROR_CONFIGURACION`, `motivo` `sin umbral RF-17 para la variable en este Edge` | — (`severidad_invalida: true`) |
| Umbral guardado que no se puede usar (TC-M03-095/122) | `ERROR_CONFIGURACION`, `motivo` con la causa | — (`severidad_invalida: true`) |

Con varios umbrales para la misma variable (una especie por umbral) rige el más
restrictivo (RFC-012).

**Forma válida de un umbral (TC-M03-095/122).** La misma que exige el backend
(rechaza otra con `SOLAPAMIENTO_NIVELES`): `valor_min < valor_max` y las tres
bandas `normal`, `precaucion` y `critico`, una vez cada una, de ancho mayor que
0, contiguas (sin huecos ni solapes) y cubriendo exactamente
`[valor_min, valor_max]`. El orden de la lista no importa y un límite en 0 es un
valor como cualquier otro. Un umbral que no la cumple:

| Dónde | Qué pasa | `motivo` (ejemplos) |
|---|---|---|
| Al recibirlo | `ACK_UMBRAL` `ERROR`; no se guarda y sigue el vigente (si había) | `valor_min y valor_max deben ser números con valor_min < valor_max`, `la banda precaucion debe tener ancho mayor que 0 (24–24)`, `niveles debe traer normal, precaucion y critico, una vez cada uno`, `las bandas normal y precaucion deben ser contiguas (sin huecos ni solapes)`, `las bandas deben cubrir exactamente [valor_min, valor_max]` |
| Ya guardado (firmware anterior) | `ERROR_CONFIGURACION`; nunca un `DESVIACION_SIMPLE` | el mismo |

Sin umbral usable no se inventa una severidad: antes, una banda en 0–0, una sin
`precaucion`/`critico` o con huecos hacía que todo lo de fuera de la `normal`
saliera `CRITICO`. El Edge no calcula porcentajes de desviación, así que un
umbral en cero no puede dividir por cero.

**Un error por variable, no por lectura.** El `ERROR_CONFIGURACION` se publica
una vez por umbral y versión (o por variable, si no tiene umbral) mientras el
servicio corre. Cada lectura siguiente queda en el historial local como
`ERROR_CONFIGURACION` `LOCAL`, sin `id_evento` ni severidad. La lectura cruda
sale siempre como telemetría (el backend la procesa con RF-62), y las demás
variables se siguen clasificando. Cuando llega un umbral válido, la captura
siguiente ya clasifica con él.

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
| `CALIDAD_HIDRICA` | `CALIDAD_DEL_AGUA`, `CALIDAD_AGUA`, `CALIDAD_HIDRICA_PH_OD` | `PH`, `OXIGENO_DISUELTO` | severidad fija (ver abajo) |
| `CALIDAD_AIRE` | `CALIDAD_DEL_AIRE`, `CORRELACION_NH3_CO2`, `NH3_CO2`, `AMONIACO_CO2`, `GASES_NH3_CO2` | `AMONIACO`, `DIOXIDO_CARBONO` | severidad fija (ver abajo) |

`THI = T − (0.55 − 0.0055·HR)·(T − 58)` (NRC, 1971), con **T en °F** (una lectura en °C se
convierte). Es la escala en la que se definen los cortes:

| THI | Severidad |
|---|---|
| < 72 | no dispara |
| 72 ≤ THI < 78 | `LEVE` |
| 78 ≤ THI < 84 | `MODERADO` |
| ≥ 84 | `CRITICO` |

Cada corte es inclusivo por abajo: 72, 78 y 84 caen en la banda superior
(matriz de QA, TC-M03-104). Se clasifica con el THI redondeado a dos
decimales, el mismo que se reporta.
**Escalada:** con severidad ≥ `LEVE`, si `temperatura_corporal` > 40 °C o
`frecuencia_respiratoria` está en `MODERADO` o peor según su umbral RF-17, sube
exactamente un nivel (`CRITICO` es tope); esas lecturas se agregan a
`variables_involucradas`. `umbrales_severidad` puede cambiar los cortes:
`{"LEVE": 72, "MODERADO": 78, "CRITICO": 84, "temperatura_corporal_max": 40}`.

**`CALIDAD_HIDRICA`** (regla AND, TC-M03-106): dispara solo si se cumplen las
dos condiciones a la vez:

| Condición | Dispara | No dispara |
|---|---|---|
| pH fuera de [6.5, 8.5] | pH < 6.5 o pH > 8.5 | 6.5 ≤ pH ≤ 8.5 (los límites son permitidos) |
| Oxígeno disuelto bajo | OD < 5 mg/L | OD ≥ 5 mg/L |

Severidad `CRITICO`. `umbrales_severidad` puede cambiar los límites y la
severidad: `{"ph_min": 6.5, "ph_max": 8.5, "oxigeno_min": 5, "severidad": "CRITICO"}`.
Con una sola condición no se emite `DESVIACION_COMPUESTA` (las lecturas siguen
pasando por sus umbrales simples RF-17, si los hay).

**`CALIDAD_AIRE`** (regla AND NH3/CO2, TC-M03-085/105): dispara solo si las
dos concentraciones **superan** (estrictamente) su límite a la vez:

| Condición | Dispara | No dispara |
|---|---|---|
| Amoníaco | NH3 > 25 ppm | NH3 ≤ 25 ppm |
| Dióxido de carbono | CO2 > 2000 ppm | CO2 ≤ 2000 ppm |

Severidad `MODERADO`. `umbrales_severidad` puede cambiar límites y severidad:
`{"nh3_max": 25, "co2_max": 2000, "severidad": "MODERADO"}`.

Todas las variables requeridas deben estar dentro de la ventana de correlación
(30 s; `ventana_correlacion_s` en la regla para acortarla). Si falta alguna, no
dispara y no es error. La variable que disparó la evaluación va primera. Se
prefieren las lecturas del mismo nodo; si no hay, sirve la de otro nodo del
mismo Edge (un Gateway cubre un sitio). Una correlación produce un solo evento,
aunque sus variables lleguen de dos nodos en la misma captura.

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
`FRECUENCIA_RESPIRATORIA=frecuencia_respiratoria`, `PH=ph`,
`OXIGENO_DISUELTO=oxigeno_disuelto` (OD en mg/L), `AMONIACO=nh3`,
`DIOXIDO_CARBONO=co2` (ambos en ppm).

**Prevalencia.** Si una compuesta con `prevalece_sobre_regla_simple: true`
dispara, no se emite el `DESVIACION_SIMPLE` de ninguna de sus lecturas (RF-57
E14/E15). Se evalúa la captura completa para que eso funcione.

**Máxima severidad (TC-M03-089).** Suprimir el simple no puede bajar la
gravedad. La compuesta que prevalece sale con la **mayor** severidad entre la
de la regla (RF-55.2) y la RF-55.1 de cualquiera de sus lecturas
(`LEVE` < `MODERADO` < `CRITICO`); en empate decide RF-55.2. La clasificación
sigue siendo `DESVIACION_COMPUESTA` de esa regla, y `metadata_edge` deja cómo
se obtuvo:

| Campo | Contenido |
|---|---|
| `severidad_regla` | Severidad que dio la regla compuesta por sí sola |
| `severidad_rf55_1` | La más grave de RF-55.1 entre sus lecturas, o `null` si todas estaban en banda normal o sin umbral |
| `severidad_origen` | `RF-55.2` o `RF-55.1`: cuál fijó `severidad` |

Sin prevalencia salen los dos eventos, cada uno con su severidad, y no llevan
estos campos.

## 5. Límites duros

Se rechazan al **recibir** la regla (ACK `ERROR`), no al evaluarla:

| Límite | Valor |
|---|---|
| Reglas activas simultáneas | ≤ 5 |
| Variables correlacionadas por regla (Tipo B) | 2–3 requeridas |
| Ventana de correlación (Tipo B) | ≤ 30 s |
| `lectura_confirmacion_max` (Tipo A) | 1–100 |

**Cardinalidad (TC-M03-086/107).** Cuenta las variables **requeridas** de la
fórmula; las opcionales solo modifican el resultado y no cuentan (THI: 2
requeridas + 2 opcionales, válida). Con 1 o con 4+ requeridas la regla se
rechaza con `ACK_REGLA` `ERROR` (`motivo`: `… correlaciona N variable(s)
(mínimo 2, máximo 3)`) y nunca se evalúa; si llegara guardada por otro
firmware, da `ERROR_CONFIGURACION`. El catálogo cerrado no se amplía por red,
así que hoy solo se puede provocar en `pytest` inyectando una fórmula
(`test_formula_con_1_variable_se_rechaza`, `test_formula_con_4_variables_se_rechaza`).
Las reglas Tipo A no correlacionan (confirman **una** variable en el tiempo) y
no tienen mínimo.

La ventana de confirmación del Tipo A va en minutos y no tiene el tope de
30 s. Latencia medida: RF-55.1 ≤ 500 ms por lectura y RF-55.2 ≤ 200 ms
adicionales (en la práctica, menos de 1 ms; medición completa en §6.2). Una regla guardada que no se puede
interpretar produce `ERROR_CONFIGURACION` una sola vez, nunca una excepción.

## 6. Perfil de hardware

Al arrancar se verifica CPU ≥ 160 MHz, RAM disponible ≥ 256 KB y
almacenamiento libre ≥ 1 MB. Si algo no alcanza, RF-55.2 se apaga (WARNING con
el recurso), RF-55.1 sigue y el arranque nunca falla. `EDGE_RF55_2_DESHABILITAR=1`
lo apaga a mano. El heartbeat lo informa en `sensores_operativos`:
`{"sensor": "rf55_2", "operativo": true|false}`.

## 6.0 Fallas de RF-55.2 en ejecución (TC-M03-088 / TC-M03-097)

Una **falla** es una excepción al evaluar las reglas compuestas (ventana,
confirmaciones o fórmula). Una regla mal formada no lo es: da
`ERROR_CONFIGURACION` y no cuenta.

| Situación | Qué hace el Edge | Evidencia |
|---|---|---|
| Falla en una captura (1.ª o 2.ª seguida) | Descarta lo de RF-55.2 en esa captura y la clasifica entera con RF-55.1 (sin prevalencia). RF-55.2 sigue activo | WARNING `RF-55.2 falló en esta captura (n/3 seguidas): se clasifica con RF-55.1` |
| Captura sin falla | Reinicia la cuenta | — |
| 3.ª falla seguida (`EDGE_RF55_2_FALLOS_MAX`) | Deshabilita RF-55.2; RF-55.1 sigue en todas las capturas | ERROR `RF-55.2 DESHABILITADO tras 3 fallas seguidas…`; heartbeat inmediato con `{"sensor": "rf55_2", "operativo": false}` |
| Deshabilitado, antes de 10 min (`EDGE_RF55_2_REINTENTO_S`) | No evalúa RF-55.2 | — |
| Primera captura pasados 10 min | Reintenta RF-55.2 | Si evalúa bien: WARNING `RF-55.2 REACTIVADO` y heartbeat `operativo: true`. Si falla: WARNING `sigue DESHABILITADO: el reintento falló` y otros 10 min |

Al deshabilitar y al reactivar se vacía la ventana de correlación. Lo que se
apagó al arrancar por hardware o por `EDGE_RF55_2_DESHABILITAR` (§6) no se
reactiva solo. El firmware no tiene ningún interruptor para fallar a propósito:
las fallas controladas se inyectan en el simulador (`"fallo_rf55_2": true`, §7).

## 6.1 Buffer, enrutamiento e historial local (RF-54 / RF-56)

**Ruta de salida.** Todo evento entra al buffer (`buffer.db`) y la fila se borra
recién con el PUBACK del broker, también con conexión: así un corte a mitad de
envío no lo pierde.

| Conexión al clasificar | Qué pasa | `origen` | `estado_conectividad` | `ruta` en historial |
|---|---|---|---|---|
| Sí | Sale en el mismo ciclo hacia RF-56 | `TIEMPO_REAL` | `true` | `DIRECTA` |
| No | Queda retenido en orden cronológico hasta reconectar | `BUFFER_LOCAL` (más de 60 s retenido) | `false` | `BUFFER` |

Al reconectar el buffer se vacía en orden (`due_at`, `id`). Nunca se descarta un
evento por buffer lleno; solo se descarta, con WARNING, si pasó más de 72 h
(el backend lo rechazaría con `TIMESTAMP_EXPIRADO`).

**Historial local** (`historial.db`, junto al buffer; independiente del
backend). Guarda las últimas `EDGE_HISTORIAL_MAX_FILAS` (20 000) clasificaciones:

| `clasificacion` | `ruta` | `estado` |
|---|---|---|
| `NORMAL` (lectura en banda normal de su umbral; no se publica) | `LOCAL` | `LOCAL` |
| `DESVIACION_SIMPLE`, `DESVIACION_COMPUESTA`, `ERROR_CONFIGURACION` | `DIRECTA` o `BUFFER` | `PENDIENTE` → `ENVIADO` (PUBACK) o `DESCARTADO` (72 h) |
| `LECTURA_INVALIDA` (dato nulo, malformado o fuera de rango físico; §2.1) | `LOCAL` | `LOCAL` |

Cada fila trae `registrado_en`, `serial`, `severidad`, `regla_aplicada`,
`id_evento`, `timestamp_captura`, `variables`, `origen` y `enviado_en`. Una
variable sin umbral no genera NORMAL (no hubo clasificación).

**Inspección (solo lectura, con el servicio en marcha):**

```bash
sudo -u sgpmp-edge /opt/sgpmp-edge/venv/bin/python -m edge_agent.inspeccionar buffer --tipo evento
sudo -u sgpmp-edge /opt/sgpmp-edge/venv/bin/python -m edge_agent.inspeccionar historial --limite 50 --json
```

## 6.2 Rendimiento: latencia y ráfaga (TC-M03-082 / TC-M03-102)

El clasificador es el agente de `raspberry/` (`edge_agent/reglas/`); corre en
la Raspberry del Gateway, no en el backend, que recibe `clasificacion_rf55` ya
calculada. `scripts/medir_rendimiento.py` arma el agente real (mismo motor,
mismo buffer SQLite con `synchronous=FULL`, mismo historial) leyendo del
gateway LoRa real, al que entrega tramas TELEMETRIA sintéticas como si llegaran
de la radio. No usa radio ni broker.

```bash
cd raspberry
python scripts/medir_rendimiento.py todo           # latencia + ráfaga, sale con 1 si algo falla
python scripts/medir_rendimiento.py rafaga --json  # evidencia en JSON
```

Escenarios:

| Escenario | Parámetros |
|---|---|
| Latencia (TC-M03-082) | 1 nodo, 4 variables por captura, 500 capturas, 5 reglas activas (2 Tipo B + 3 Tipo A) y umbral en las 4 variables |
| Ráfaga (TC-M03-102) | 20 nodos vaciando a la vez su memoria llena (32 capturas × 4 variables = 2560 lecturas), las mismas 5 reglas y, **mientras el agente clasifica**, otra ráfaga igual llegando por el hilo de radio |

Criterios de aceptación:

| Criterio | Cómo se mide | Límite |
|---|---|---|
| Latencia por lectura | Desde que el agente toma las lecturas de un nodo hasta que sus eventos están en el buffer (incluye SQLite y fsync) | ≤ 500 ms |
| Ninguna lectura perdida | Telemetría en el buffer + confirmada por PUBACK = lecturas recibidas (las dos ráfagas) | 100 % |
| Recepción LoRa sin bloqueo | Lo que tarda cada trama en entrar al gateway mientras el agente clasifica | ≤ 50 ms |
| El loop vuelve a atender MQTT | Duración de cada vuelta del loop del agente (comandos y PUBACK esperan, como máximo, eso) | ≤ 1 s |

Además se informa, sin criterio, el tiempo desde el inicio de la ráfaga hasta
que se clasificó cada nodo, y el `tiempo_procesamiento_ms` de cada evento.

**Cómo se cumple.** La radio tiene su propio hilo y solo toma el lock del
gateway para agregar la lectura. paho atiende la red en su hilo (`loop_start`),
así que el keepalive no depende del loop. Las escrituras de una captura
(telemetría, eventos e historial) van en una sola transacción, con un fsync en
vez de uno por fila, y cada racha de PUBACK seguidos se confirma igual. Cada
vuelta del loop captura durante a lo sumo 200 ms (`PRESUPUESTO_CAPTURAS_S`,
siempre al menos un nodo). Los nodos que no alcanzan quedan vencidos, con su
agenda intacta, para la vuelta siguiente, y el loop no espera a la red mientras
quedan pendientes.

Medición en la PC de desarrollo (Windows, SSD). En la Raspberry los tiempos son
mayores; **el resultado que vale es el del nodo del ambiente de test**:

| | Antes | Después |
|---|---|---|
| Captura de un nodo de la ráfaga (128 lecturas) | 958 ms | 52 ms |
| Vuelta más larga del loop | 17 872 ms (una sola) | 212 ms (4 vueltas) |
| Ráfaga completa clasificada | 17,9 s | 0,75 s |
| Captura normal (4 lecturas), máximo | 45 ms | 6,4 ms |
| Trama LoRa entrando durante la ráfaga, máximo | 1,2 ms | 0,4 ms |

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
| S8 | `humedad_suelo` = 99 (TC-M03-083) | Sin umbral | `ERROR_CONFIGURACION` | — | `esperado_sin_umbral`: `humedad_suelo` |
| C1 | T = 32.5 °C, HR = 78 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` | THI ≈ 86.57 |
| C2 | T = 72 °F, HR = 100 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `LEVE` | THI = 72.0 |
| C3 | T = 78 °F, HR = 100 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `MODERADO` | THI = 78.0 |
| C4 | T = 84 °F, HR = 100 % | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` | THI = 84.0 |
| C5 | T = 80 °F, HR = 100 %, T corporal = 41 °C | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` (escalada) | THI = 80.0 |
| C6 | T = 72 °F, HR = 100 %, FR = 50 rpm (precaución); solo umbral de FR | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` (sin simple de FR) | `MODERADO` (escalada) | THI = 72.0 |
| C7 | Solo T = 32.5 °C | `ESTRES_TERMICO` | (sin evento, sin error) | — | — |
| C8 | T en t = 0 s, HR en t = 31 s | `ESTRES_TERMICO` | (sin evento) | — | — |
| C9 | T en t = 0 s, HR en t = 29 s | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA`, HR primera | `CRITICO` | THI ≈ 86.57 |
| C10 | T = 32.5 °C (crítico por umbral), HR = 78 % | Umbral + `ESTRES_TERMICO` | solo `DESVIACION_COMPUESTA` | `CRITICO` | THI ≈ 86.57 |
| C11 | T = 25 °C, HR = 60 % (TC-M03-084) | `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `LEVE` | THI = 72.82 |
| H1 | pH = 5.8 en t = 0 s, OD = 3.9 mg/L en t = 1 s | `CALIDAD_HIDRICA` | `DESVIACION_COMPUESTA`, OD primera | `CRITICO` | — |
| H2 | pH = 5.8, OD = 6.5 mg/L (solo pH) | `CALIDAD_HIDRICA` | (sin evento) | — | — |
| H3 | pH = 7.2, OD = 3.9 mg/L (solo OD) | `CALIDAD_HIDRICA` | (sin evento) | — | — |
| H4 | pH = 8.5 (borde), OD = 3.0 mg/L | `CALIDAD_HIDRICA` | (sin evento) | — | — |
| H5 | pH = 9.0, OD = 5.0 mg/L (borde) | `CALIDAD_HIDRICA` | (sin evento) | — | — |
| A1 | T = 31 °C en 0, 60 y 120 s | Tipo A: máx 30, 3 en 5 min | `DESVIACION_COMPUESTA` (a la 3.ª) | `MODERADO` | — |
| A2 | T = 31 °C en 0, 3, 6 y 9 min | Tipo A: máx 30, 3 en 5 min | (sin evento) | — | — |
| L1 | 6 reglas activas | — | sexta: `ACK_REGLA` `ERROR` | — | — |
| L2 | `ventana_correlacion_s` = 45 | `ESTRES_TERMICO` | `ACK_REGLA` `ERROR` | — | — |
| G1 | NH3 = 30 ppm, CO2 = 2200 ppm a 1 s (TC-M03-085) | `CALIDAD_AIRE` | `DESVIACION_COMPUESTA`, CO2 primera | `MODERADO` | — |
| G2 | NH3 = 30, CO2 = 1800 ppm (TC-M03-105) | `CALIDAD_AIRE` | (sin evento) | — | — |
| G3 | NH3 = 20, CO2 = 2200 ppm (TC-M03-105) | `CALIDAD_AIRE` | (sin evento) | — | — |
| G4 | NH3 = 25 ppm (borde), CO2 = 2200 ppm | `CALIDAD_AIRE` | (sin evento) | — | — |
| G5 | NH3 en t = 0 s, CO2 en t = 29 s | `CALIDAD_AIRE` | `DESVIACION_COMPUESTA` | `MODERADO` | — |
| G6 | NH3 en t = 0 s, CO2 en t = 31 s | `CALIDAD_AIRE` | (sin evento) | — | — |
| F1 | T = 32.5 °C, HR = 78 % en t = 0/60/120 s; RF-55.2 falla en t = 60 (TC-M03-097) | Umbral + `ESTRES_TERMICO` | compuesta, **simple**, compuesta | `CRITICO` | t = 60: `fallos_consecutivos` 1, sigue `ACTIVO` |
| F2 | Falla en t = 0, 60, 120; sana en 180 (TC-M03-088) | Umbral + `ESTRES_TERMICO` | 4 × `DESVIACION_SIMPLE` | `CRITICO` | t = 120: `DESHABILITADO`, `operativo: false`; t = 180 no se evalúa |
| F3 | Como F2, más t = 700 (antes del reintento), 730 (reintento falla) y 1400 (reintento sano) | Umbral + `ESTRES_TERMICO` | 5 × simple y compuesta en t = 1400 | `CRITICO` | t = 730: `fallos_consecutivos` 4; t = 1400: `ACTIVO` |
| B1 | Sin conexión: T = 21, 26 y 30 °C; reconecta a los 120 s (TC-M03-098) | Umbral | 2 × `DESVIACION_SIMPLE` al reconectar, `BUFFER_LOCAL` | `MODERADO`, `CRITICO` | buffer: 2 eventos + 3 lecturas en orden; historial: NORMAL + 2 `BUFFER`/`ENVIADO` |
| B2 | Con conexión: T = 26 °C (TC-M03-099) | Umbral | `DESVIACION_SIMPLE`, `TIEMPO_REAL` | `MODERADO` | buffer vacío tras el PUBACK; historial `DIRECTA`/`ENVIADO` |
| P1 | T = 32.5 °C, HR = `null` (TC-M03-081) | `ESTRES_TERMICO` | (sin evento) | — | historial: `LECTURA_INVALIDA` |
| P2 | pH = `"abc"`, OD = `true` | `CALIDAD_HIDRICA` | (sin evento) | — | historial: 2 × `LECTURA_INVALIDA` |
| P3 | HR = 150 %, pH = 20 | `ESTRES_TERMICO`, `CALIDAD_HIDRICA` | (sin evento) | — | historial: 2 × `LECTURA_INVALIDA` |
| P4 | T = 30 °C, HR = `null` | Umbral + `ESTRES_TERMICO` | `DESVIACION_SIMPLE` de T | `CRITICO` | la HR nula no impide clasificar la T |
| B3 | Sin conexión todo el caso: T = 21, 30 y 22 °C (TC-M03-100) | Umbral | (no se publica nada) | — | historial: NORMAL, `CRITICO` `PENDIENTE`, NORMAL |
| M1 | T = 32.5 °C (TC-M03-089) | Umbral + Tipo A (máx 30, 1 lectura, `MODERADO`) | `DESVIACION_COMPUESTA` | `CRITICO` | `severidad_regla` `MODERADO`, `severidad_rf55_1` `CRITICO`, `severidad_origen` `RF-55.1` |
| M2 | T = 25 °C (precaución), HR = 60 % | Umbral + `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `MODERADO` | THI = 72.82 (`LEVE`); origen `RF-55.1` |
| M3 | T = 32.5 °C (precaución con su umbral), HR = 78 % | Umbral propio + `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` | THI ≈ 86.57; `severidad_rf55_1` `MODERADO`, origen `RF-55.2` |
| M4 | T = 32 °C (crítico), HR = 78 % | Umbral + `ESTRES_TERMICO` | `DESVIACION_COMPUESTA` | `CRITICO` | THI ≈ 85.78; empate, origen `RF-55.2` |

| E1 | T = 30 °C sin umbral y FR = 50 rpm con umbral, 3 capturas (TC-M03-083) | Umbral de FR | 3 × `DESVIACION_SIMPLE` de FR | `MODERADO` | T: un `ERROR_CONFIGURACION` publicado; historial: después 2 × `LOCAL` |
| E2 | Umbral de T con `valor_min` = `valor_max` = 0 y bandas 0–0 (TC-M03-095) | — | `ERROR_CONFIGURACION` (T queda sin umbral) | — | `ACK_UMBRAL` `ERROR` |
| E3 | Umbral de T con `precaucion` 24–24 (TC-M03-095) | — | `ERROR_CONFIGURACION` (sin umbral) | — | `ACK_UMBRAL` `ERROR` |
| E4 | Umbral de T 0–10 (normal 0–5, precaución 5–8, crítico 8–10); T = 0 y 9 °C | Umbral | `DESVIACION_SIMPLE` solo con T = 9 | `CRITICO` | un límite en 0 es válido |
| E5 | Umbral de T solo con banda `normal` (TC-M03-122) | — | `ERROR_CONFIGURACION` (sin umbral) | — | `ACK_UMBRAL` `ERROR` |
| E6 | El mismo umbral ya guardado (`umbrales_guardados`); T = 35 y 36 °C (TC-M03-122) | — | un `ERROR_CONFIGURACION`, `severidad_invalida: true` | — | nunca `CRITICO`; historial: `DIRECTA` y luego `LOCAL` |

Un caso que no configura umbral para alguna de sus variables (los de reglas
compuestas, por ejemplo HR, pH, OD, NH3 o CO2) recibe también su
`ERROR_CONFIGURACION` de §3. No entra en `esperado`: se declara en
`esperado_sin_umbral` y el inyector lo compara aparte.

Para un caso nuevo, agregarlo a `scripts/casos_rf55.json` (formato en el
encabezado de `inyectar_lecturas.py`) y a esta tabla.

## 8. Pendiente fuera del Edge

- **BE-1:** persistir `sensor`/`serial` de `variables_involucradas` (hoy
  `VariableInvolucradaDTO` los ignora sin error).
- **BE-5:** modelar reglas multivariable en `modulo3.reglas_alertas`; hasta
  entonces las fórmulas viven en el catálogo del firmware.
- Merge a `main`: después del broker y del `BE-0` del backend (§7 del documento
  de contratos). Si el firmware llega primero, los eventos se rechazan.
