# Plan de pruebas finales con hardware real (RF-55, rama de incidencias QA)

Este plan es para el equipo de desarrollo IoT/AIOT, QA y quien opere la
Raspberry del ambiente de test. Cubre cómo llevar a la Pi los cambios de las
incidencias de QA, verificar que todo funciona en el hardware real y cerrar
los casos que solo se pueden comprobar en el nodo. No repite lo que ya está
en otras guías; las enlaza:

| Guía | Para qué se usa acá |
|---|---|
| [`INSTALACION_RASPBERRY.md`](INSTALACION_RASPBERRY.md) | Instalación, verificación de las etapas A y B, aceptación M3 (§8) y resiliencia (§7.3) |
| [`PRUEBAS_CAMPO.md`](PRUEBAS_CAMPO.md) | Enlace LoRa (L1–L5) y extremo a extremo (E2E-1 a E2E-10) |
| [`RELEASES.md`](RELEASES.md) | Release candidate en `develop`, Pi de laboratorio sin auto-actualización y release final |
| [`CONTRATO_EVENTO_EDGE.md`](CONTRATO_EVENTO_EDGE.md) | Comportamiento esperado de RF-55, inyector (§7) y rendimiento (§6.2) |
| [`incidencias/`](incidencias/) | Diagnóstico, cambios y verificación de cada incidencia |

## 1. Alcance

**Entra:** las incidencias resueltas en la rama `fix/inc-mm03-159-thi-nrc`:

| Incidencia | Tema | Qué falta verificar en el hardware |
|---|---|---|
| G37 | Latencia y ráfaga | La medición en la Pi (los números actuales son de la PC de desarrollo) y una ráfaga real de un nodo |
| G38 | Errores de configuración | Que el backend acepte el `ERROR_CONFIGURACION` sin severidad; variable sin umbral con un nodo real |
| G39 | Fallas de RF-55.2 | El estado `rf55_2` en el heartbeat real; las fallas inyectadas, con el inyector corriendo en la Pi |
| G40 | Buffer, enrutamiento e historial | **Toda la verificación pendiente** (TC-098/099/100), con un corte real de conectividad |
| G45 | Entradas inválidas | Un sensor desconectado o fallando en el nodo real |
| G46, G47, G48, G49, G50, G54 | Severidad, THI, aire, hídrica, cardinalidad, trazabilidad | THI y trazabilidad con el DHT22 real; lo demás, con el inyector en la Pi |

**No entra:**
- **INC-M04-195-G024 (RF-66):** el motor de IA no está en el Edge; el bloqueo se reasignó.
- **Sensores de pH, OD, NH3 y CO2:** no hay hardware. `CALIDAD_HIDRICA` y
  `CALIDAD_AIRE` se verifican en la Pi con el inyector, que corre el agente
  real con lecturas sintéticas.

## 2. Prerrequisitos (bloquean el inicio)

| # | Qué | Responsable | Para qué fase |
|---|---|---|---|
| R1 | Acceso a la Pi de test: host/IP, vía (LAN/VPN), usuario SSH con `sudo` | Infra / líder IoT | Todas |
| R2 | Serial del Gateway Edge y de cada nodo, registrados en `modulo9.dispositivos_iot` y asignados al Gateway | Plataforma | 2–4 |
| R3 | Host y puerto MQTT de test, y la credencial MQTT del Gateway | Plataforma | 2–4 |
| R4 | URL del API del broker y token Bearer de servicio (aceptación M3) | Plataforma | 3 |
| R5 | Al menos un nodo NodeMCU/ESP8266 con DHT22 flasheado y su clave LoRa (`lora-key`); antena en la Pi y en el nodo | IoT | 4 |
| R6 | Frecuencia y potencia de radio confirmadas (las mismas en `edge-agent.env` y `platformio.ini`) | IoT | 4 |
| R7 | **Umbral RF-17 en la plataforma para cada variable que mide el nodo** (`temperatura_ambiente`, `humedad_relativa`, …), con la forma de §3 del contrato | QA / Plataforma | 4 |
| R8 | Regla THI (`ESTRES_TERMICO`) creada en `modulo3.reglas_alertas` y sincronizable al Gateway | QA / Plataforma | 4 |
| R9 | Backend de test con `POST /iot/eventos-edge` (BE-0) que acepte `ERROR_CONFIGURACION` **sin `severidad`** y persista `metadata_edge` íntegro | Backend | 4 |
| R10 | Procedimiento acordado para cortar la conectividad Pi → broker sin afectar a otros equipos (bloquear el puerto MQTT con `iptables` en la Pi o desconectar el uplink) | Infra + QA | 4 |
| R11 | Respaldo de `/var/lib/sgpmp-edge/` y de `/etc/sgpmp/edge-agent.env` de la Pi | Quien opera la Pi | 2 |

Sin R7, cada variable sin umbral publica un `ERROR_CONFIGURACION` (es el
comportamiento nuevo de G38). Sin R9, el backend puede rechazar los eventos
nuevos. Ninguna de las dos cosas rompe el Edge, pero ensucia las pruebas.

## 3. Fases

| Fase | Qué | Dónde | Duración aprox. |
|---|---|---|---|
| 1 | Release candidate | GitHub | 0,5 día |
| 2 | Instalación en la Pi de test | Pi | 1 h |
| 3 | Regresión automática en la Pi | Pi | 1 h |
| 4 | Pruebas manuales con el nodo real | Pi + nodo + plataforma | 1–1,5 días |
| 5 | Estabilidad de 24 h | Pi + nodo | 1 día (desatendido) |
| 6 | Cierre y release final | GitHub + QA | 0,5 día |

### Fase 1 — Release candidate

1. Commits por incidencia con Conventional Commits (`fix(rf55): …`), para que
   el changelog los liste. La rama trae `feat` (historial, disyuntor,
   reglas nuevas, medidor), así que la versión final será **0.4.0**.
2. CI verde en la rama: `ruff check`, `ruff format --check`, `pytest`.
3. PR a `develop` y merge: el CI publica `v0.4.0-rc.N`. Las Pi de campo lo ignoran.
4. **No abrir el PR `develop → main` hasta terminar la fase 5:** fusionar en
   `main` despliega a todas las Pi en ≤ 3,5 h (`RELEASES.md`).

Criterio de salida: existe el tag `v0.4.0-rc.N` y el CI está verde.

### Fase 2 — Instalación en la Pi de test

```bash
# Respaldo (R11)
sudo tar czf ~/respaldo-edge-$(date +%F).tgz /var/lib/sgpmp-edge /etc/sgpmp/edge-agent.env

cd ~/EDGE-FIRMWARE-SGPMP
git fetch --tags && git checkout v0.4.0-rc.N
sudo raspberry/scripts/install.sh --env /etc/sgpmp/edge-agent.env --no-auto-update
sudo -u sgpmp-edge /opt/sgpmp-edge/venv/bin/python -m edge_agent --check-config
journalctl -u edge-agent -f
```

`--no-auto-update` evita que el updater cambie la versión a mitad de las pruebas.

| # | Verificación | Esperado |
|---|---|---|
| I1 | Arranque | Log `edge_agent v0.4.0-rc.N arrancando` y `Conectado (session_present=True)` |
| I2 | Estado anterior conservado | `config.json`, `umbrales.json`, `reglas.json` y `buffer.db` siguen; aparece `historial.db` nuevo |
| I3 | Perfil de hardware | Sin WARNING de RF-55.2 deshabilitado (Pi 3B: CPU, RAM y disco alcanzan) |
| I4 | Heartbeat | `version_firmware` = `v0.4.0-rc.N`; en `sensores_operativos`, `{"sensor": "rf55_2", "operativo": true}` |
| I5 | Variables nuevas del entorno | No hace falta tocar el `.env`: `EDGE_HISTORIAL_MAX_FILAS`, `EDGE_RF55_2_FALLOS_MAX` y `EDGE_RF55_2_REINTENTO_S` tienen valores por defecto |
| I6 | Inspección | `sudo -u sgpmp-edge /opt/sgpmp-edge/venv/bin/python -m edge_agent.inspeccionar historial --limite 5` responde sin error |

**Vuelta atrás**, si algo de la fase falla: `git checkout v0.3.0` y el mismo
`install.sh`. La v0.3.0 lee el `buffer.db` actual y no usa `historial.db`. Si
hiciera falta, restaurar el respaldo.

### Fase 3 — Regresión automática en la Pi

Desde el clon y con un venv propio (`INSTALACION_RASPBERRY.md` §8):

```bash
cd ~/EDGE-FIRMWARE-SGPMP/raspberry
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/pytest                                                   # A1
.venv/bin/python scripts/inyectar_lecturas.py scripts/casos_rf55.json  # A2

sudo systemctl stop edge-agent                                     # A3 y A4: radio y client_id libres
TMPDIR=/var/tmp .venv/bin/python scripts/medir_rendimiento.py todo --json > ~/rendimiento-pi.json
set -a; . <(sudo cat /etc/sgpmp/edge-agent.env); set +a
export EDGE_IT_API_URL=https://<host de test>/v1
read -rs -p "Token del API: " EDGE_IT_API_TOKEN; export EDGE_IT_API_TOKEN; echo
.venv/bin/pytest -m integration -v
sudo systemctl start edge-agent
```

| # | Prueba | Criterio | Incidencias |
|---|---|---|---|
| A1 | `pytest` completo | Todo en verde, **incluida `test_systemd.py`**, que en Windows se omite | Todas |
| A2 | Inyector | 54/54 casos OK (S, C, A, H, G, F, B, P, E, L y M) | G38, G39, G45–G50, G54 |
| A3 | `medir_rendimiento.py todo` en la tarjeta SD de la Pi (`TMPDIR=/var/tmp`) | Sale con 0. Latencia ≤ 500 ms por lectura; ninguna lectura perdida; trama LoRa ≤ 50 ms; vuelta del loop ≤ 1 s. Adjuntar `rendimiento-pi.json` | G37 |
| A4 | Aceptación M3 contra test | Todos `PASSED` (P6/E1 pueden salir `SKIPPED` sin la credencial del gateway) | Regresión MQTT |

Si A3 falla en la Pi, la palanca es `PRESUPUESTO_CAPTURAS_S` en
`edge_agent/agent.py`: con valores más bajos baja la vuelta del loop, aunque la
ráfaga tarda más en clasificarse entera. Se ajusta, se mide de nuevo y se
documenta en G37 antes de seguir.

### Fase 4 — Pruebas manuales con el nodo real

Servicio corriendo con `EDGE_SOURCE=lora`. Para acelerar, el nodo de prueba con
`frecuencia_captura` = 1 min. Cada prueba deja evidencia: log
(`journalctl -u edge-agent --since …`), salida de `inspeccionar` (`--json`) y
captura de la UI o de la BD.

Abreviaturas:
- `INSP` = `sudo -u sgpmp-edge /opt/sgpmp-edge/venv/bin/python -m edge_agent.inspeccionar`.
- `CORTE` = el procedimiento de R10, por ejemplo
  `sudo iptables -I OUTPUT -p tcp --dport <puerto MQTT> -j DROP`, que se
  revierte con `-D` en lugar de `-I`.

#### 4.1 Base (ya documentadas, se repiten como regresión)

| # | Referencia | Criterio resumido |
|---|---|---|
| M1 | `PRUEBAS_CAMPO.md` L1 (30 min a 5 m) | ≥ 99 % de tramas |
| M2 | E2E-1, E2E-2 | Telemetría real en la BD con `timestamp_captura` correcto; serial `ACTIVO` |
| M3 | E2E-3, E2E-4 | Cambio de config desde la UI `APLICADA`; sobrevive a un corte del nodo |
| M4 | E2E-5 | Nodo apagado → `SIN_SEÑAL` |
| M5 | E2E-7, E2E-8 / `INSTALACION_RASPBERRY.md` §7.3 | Corte de energía y proceso trabado: vuelve solo; watchdog ≤ 60 s |

#### 4.2 RF-55 con el nodo real

| # | Caso | Procedimiento | Criterio | Inc. |
|---|---|---|---|---|
| R1 | Umbral desde la plataforma | Sincronizar el umbral RF-17 de cada variable del nodo (R7) | `ACK_UMBRAL` OK y queda en `umbrales.json` | G38 |
| R2 | Umbral con forma inválida | Intentar en la plataforma un umbral con banda de ancho 0 o sin `critico` | Lo rechaza el backend (`SOLAPAMIENTO_NIVELES`) o, si llega al Edge, `ACK_UMBRAL` `ERROR` con el `motivo`. Nunca `CRITICO` inventado | G38 (TC-095/122) |
| R3 | Variable sin umbral (TC-083) | Agregar al nodo un DS18B20 cuya variable no tenga umbral, o quitar uno en un nodo de prueba | **Un** `ERROR_CONFIGURACION` en `POST /iot/eventos-edge`, sin `severidad`, con `severidad_invalida: true`; el backend lo persiste (R9); `INSP historial` muestra las lecturas siguientes `LOCAL`; las demás variables se siguen clasificando | G38 |
| R4 | Regla THI | Sincronizar la regla THI (E2E-9) | `ACK_REGLA` OK; `reglas.json` la contiene | G47, G54 |
| R5 | THI con el DHT22 | Calentar y humidificar el sensor (secador y vapor, o cámara) hasta THI ≥ 72 | `DESVIACION_COMPUESTA` en el backend con `valores_derivados.THI` coherente con la T y la HR medidas (fórmula NRC) y la severidad de los cortes 72/78/84 | G47 |
| R6 | Trazabilidad | Revisar en la BD el evento de R5 | `metadata_edge`: `regla_aplicada` `ESTRES_TERMICO`, `nombre_regla` exacto, `version_regla` e `id_regla_alerta` enteros. Editar la regla (versión nueva) y repetir: el evento trae la versión nueva | G54 |
| R7 | Máxima severidad | Con R5 activo, que la T quede en `critico` de su umbral simple y el THI en un corte menor | El evento sale `CRITICO`, `severidad_origen` `RF-55.1` | G46 |
| R8 | Sensor desconectado | Desconectar el DATA del DHT22 durante 3 capturas y reconectar | Sin telemetría NaN ni clasificaciones falsas; el ESTADO marca `FLAG_ERROR_SENSOR`; al reconectar se vuelve a clasificar | G45 |
| R9 | RF-55.2 en el heartbeat | Revisar los heartbeats durante toda la fase | `rf55_2 operativo: true`; sin `RF-55.2 falló` en el log (las fallas se prueban en A2, casos F1–F3) | G39 |

#### 4.3 Buffer, enrutamiento e historial (G40, TC-098/099/100)

Procedimiento de `INC-M03-108-G40.md` §2, con el corte de R10:

| # | Caso | Pasos | Criterio |
|---|---|---|---|
| B1 | TC-099 enrutamiento directo | Con conexión, provocar una lectura fuera de umbral (calentar el DHT22); `INSP historial --limite 5` y `INSP buffer --tipo evento` | Fila `DIRECTA` / `ENVIADO` / `TIEMPO_REAL`; buffer de eventos vacío; el evento está en el backend |
| B2 | TC-098 buffer sin conectividad | `CORTE`; 10 min con lecturas normales y fuera de umbral; `INSP buffer --tipo evento --json` | Eventos en orden, con `estado_conectividad: false`; heartbeat local con buffer `ACTIVO` |
| B3 | TC-100 historial sin backend | Durante B2: `INSP historial --json`; luego `sudo systemctl restart edge-agent` y repetir | `NORMAL` y `DESVIACION_*` con ruta `BUFFER`/`PENDIENTE`; todo sigue después del reinicio |
| B4 | Reconexión | Revertir el `CORTE` | El buffer de eventos se vacía en orden y sin duplicados; el historial pasa a `ENVIADO` / `BUFFER_LOCAL`; los eventos están en el backend con `origen: BUFFER_LOCAL`; heartbeat `INACTIVO` |
| B5 | Corte largo | E2E-6 y E2E-10 (30 min) | Sin pérdida de telemetría ni de eventos |

#### 4.4 Ráfaga real (G37)

| # | Caso | Pasos | Criterio |
|---|---|---|---|
| G1 | Memoria llena del nodo | Configurar el nodo con `frecuencia_captura` 1 min e `intervalo_transmision` 30 min: al llenar `MAX_SAMPLES` (10 en el ESP8266) transmite en ráfaga | Llegan las 10 capturas (log y `INSP historial`), todas clasificadas; el heartbeat sigue en hora; un cambio de config enviado durante la ráfaga se confirma en ≤ 30 s |
| G2 | Ráfaga con red cortada | Repetir G1 con `CORTE`, después revertirlo | Igual que B4, sin bloqueos visibles en el log |

La ráfaga de 20 nodos no se puede montar con el hardware disponible: queda
cubierta por A3, que corre el mismo agente y el mismo gateway en la Pi.

### Fase 5 — Estabilidad de 24 h

El nodo transmitiendo normal y la regla THI activa:

| # | Qué | Criterio |
|---|---|---|
| S1 | Reinicios | `systemctl show edge-agent -p NRestarts` = 0; sin `Watchdog timeout` |
| S2 | Memoria | El RSS (`ps -o rss= -C python`, cada hora) no crece de forma sostenida |
| S3 | Disco | `historial.db` se mantiene en su tope de retención (`EDGE_HISTORIAL_MAX_FILAS`); `buffer.db` vuelve a su tamaño base |
| S4 | Heartbeats | Sin huecos en el backend mayores que el intervalo |
| S5 | Log | Sin `ERROR` ni `Traceback` fuera de los provocados |

### Fase 6 — Cierre

1. Completar la sección de verificación de cada incidencia (G37 §2.3, G40 §3.3)
   con los resultados y la evidencia de la Pi.
2. QA vuelve a ejecutar sus casos (TC-M03-082, 083, 095, 098, 099, 100, 102,
   122, 088, 097, 089, 084/104, 085/105, 106, 086/107, 199, 081) contra la Pi
   o con el inyector, y cierra en el dashboard.
3. PR `develop → main`: el CI publica `v0.4.0` y las Pi de campo se actualizan
   solas. Seguimiento: `version_firmware` = `v0.4.0` en el heartbeat de cada
   equipo en ≤ 3,5 h.
4. En la Pi de test, volver a la auto-actualización: reinstalar sin
   `--no-auto-update`.

## 4. Decisión de salida (go / no-go a `main`)

**Go** si se cumplen todas:
- I1 a I6, A1 a A4, R1 a R9, B1 a B5, G1, G2 y S1 a S5 OK o justificados.
- Ningún evento rechazado por el backend en `POST /iot/eventos-edge`.
- La evidencia de cada incidencia queda adjunta.

**No-go** con cualquiera de estas:
- Pérdida de telemetría o de eventos.
- Un reinicio no provocado.
- A3 fuera de criterio sin un ajuste aplicado y medido.
- El backend rechaza `ERROR_CONFIGURACION` o pierde campos de `metadata_edge`.

## 5. Registro de resultados

Una fila por prueba en el documento de resultados:

| Campo | Ejemplo |
|---|---|
| ID | B2 |
| Fecha y hora | 2026-11-03 10:15 |
| Versión | `version_firmware` del heartbeat (`v0.4.0-rc.N`) y firmware del nodo |
| Condiciones | Nodo a 5 m, `frecuencia_captura` 1 min, corte por `iptables` |
| Evidencia | `inspeccionar buffer --json`, log, captura de la BD |
| Resultado | OK / FALLA (con la incidencia nueva, si corresponde) |
