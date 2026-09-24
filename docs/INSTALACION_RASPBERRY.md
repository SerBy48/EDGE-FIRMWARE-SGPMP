# Instalación del edge_agent en la Raspberry Pi

Guía paso a paso para dejar una Raspberry Pi 3 Model B (Raspberry Pi OS Lite
64-bit, el mismo equipo de `DOC_PRUEBA_ACL_EDGE_DEV.docx`) conectada al
broker de forma permanente, sin intervención posterior por SSH.

La instalación se hace en dos etapas:

- **Etapa A — solo MQTT (`EDGE_SOURCE=fake`)**: valida conexión, heartbeat,
  comandos y ACK contra dev con datos sintéticos. No necesita el módulo LoRa.
- **Etapa B — LoRa real (`EDGE_SOURCE=lora`)**: se activa cuando estén
  confirmadas la frecuencia y la potencia con la ANE y haya nodos ESP32
  flasheados.

SSH se usa **una sola vez**, para instalar. Después, el servicio arranca,
se reconecta y se reinicia solo.

---

## 0. Antes de empezar

| Qué | Detalle |
|---|---|
| Hardware | Raspberry Pi 3 Model B, microSD de 16 GB o más (clase A1), fuente oficial 5 V / 2.5 A |
| Red | WiFi 2.4 GHz (la Pi 3 no tiene 5 GHz) o cable Ethernet, con salida a internet |
| Datos del ambiente dev | Host y puerto MQTT (en dev: TCP 1884, sin TLS), contraseña de `sgpmp_devices` (guía privada `GUIA_CONEXION_IOT_DEV.md`) |
| Serial | Un serial registrado en `modulo9.dispositivos_iot` (ej. el del flujo F1: `TC-M09-G64-1789321890010`) |
| Solo etapa B | Módulo SX1276/RFM95 **de 915 MHz** con antena, 7 cables dupont hembra-hembra |
| Solo M3 | URL del API del broker (`https://<host>/v1`) y el token Bearer de servicio |

> **Nunca** encender el módulo LoRa sin la antena conectada: transmitir sin
> antena puede dañar el amplificador del SX1276.

---

## 1. Preparar la tarjeta microSD

1. En el PC, instalar **Raspberry Pi Imager** (raspberrypi.com/software).
2. *Dispositivo*: Raspberry Pi 3. *Sistema*: **Raspberry Pi OS (other) →
   Raspberry Pi OS Lite (64-bit)**. *Almacenamiento*: la microSD.
3. En **Editar ajustes** (engranaje):
   - Hostname: `sgpmp-edge-<sitio>` (ej. `sgpmp-edge-est01`).
   - Usuario y contraseña propios. No usar `pi`/`raspberry`.
   - WiFi: SSID, contraseña y **país `CO`**.
   - Zona horaria `America/Bogota` y teclado `es`/`latam`.
   - Pestaña *Servicios*: **habilitar SSH**, preferiblemente con clave
     pública.
4. Grabar, insertar la microSD en la Pi y encender. El primer arranque tarda
   1–2 minutos.

## 2. Primer acceso y actualización del sistema

Desde el PC:

```bash
ssh <usuario>@sgpmp-edge-<sitio>.local     # o la IP que muestre el router
```

En la Pi:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git
sudo reboot
```

Volver a entrar por SSH después del reinicio y verificar:

```bash
cat /etc/os-release | grep PRETTY     # Debian 12 (bookworm)
uname -m                              # aarch64
timedatectl                           # "System clock synchronized: yes"
```

Si el reloj no está sincronizado, revisar la salida a internet: la Pi 3 no
tiene RTC y `timestamp_captura` depende de NTP.

## 3. Cableado del módulo LoRa (solo etapa B)

**Apagar la Pi antes de cablear.** El módulo usa SPI0 con CE0. El pin DIO0
no hace falta, porque el driver consulta las interrupciones por SPI.

| RFM95 / SX1276 | Raspberry Pi (GPIO BCM) | Pin físico |
|---|---|---|
| 3.3V / VCC | 3V3 | 1 |
| GND | GND | 6 |
| SCK | GPIO11 (SCLK) | 23 |
| MISO | GPIO9 (MISO) | 21 |
| MOSI | GPIO10 (MOSI) | 19 |
| NSS / CS | GPIO8 (CE0) | 24 |
| RESET | GPIO25 | 22 |
| ANT | antena 915 MHz | — |

> Alimentar el módulo con **3.3 V, nunca 5 V**. RESET es opcional pero
> recomendado: si se conecta, configurar `EDGE_LORA_RESET_GPIO=25`.

## 4. Descargar el código

```bash
cd ~
git clone https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP.git
cd EDGE-FIRMWARE-SGPMP
git checkout develop        # mientras el PR #1 no esté fusionado: feature/plan-mqtt-persistente
git log --oneline -1
```

Si el repositorio es privado, `git clone` pide usuario y un **token de acceso
personal de GitHub** (no la contraseña de la cuenta). Crear uno en GitHub →
Settings → Developer settings → Fine-grained tokens, con acceso de lectura a
este repositorio. No guardarlo en la Pi después de clonar.

## 5. Preparar el archivo de configuración

El archivo tiene la contraseña MQTT: se escribe con un editor, no con `echo`
(para que no quede en el historial), y se borra después de instalar.

```bash
cp raspberry/edge-agent.env.example ~/edge-agent.env
chmod 600 ~/edge-agent.env
nano ~/edge-agent.env
```

Valores para la **etapa A contra dev**:

```ini
EDGE_MQTT_HOST=<host de dev>
EDGE_MQTT_PORT=1884
EDGE_MQTT_USERNAME=sgpmp_devices
EDGE_MQTT_PASSWORD=<contraseña de dev>
EDGE_SERIALS=TC-M09-G64-1789321890010
EDGE_HEARTBEAT_INTERVAL_S=300
EDGE_FRECUENCIA_CAPTURA_MIN=10
EDGE_INTERVALO_TRANSMISION_MIN=15
EDGE_SOURCE=fake
EDGE_FAKE_VARIABLES=temperatura_ambiente:C:20:30
```

Notas:

- `EDGE_FAKE_VARIABLES`: el nombre debe existir en
  `modulo9.variables_ambientales`. Si no existe, el broker descarta la
  telemetría, pero heartbeat y comandos funcionan igual. Se puede dejar
  vacío.
- Si la contraseña tiene espacios, `#` o comillas, ponerla entre comillas
  dobles y escapar `"` y `\` con `\`.
- Guardar con `Ctrl+O`, `Enter`, `Ctrl+X`.

## 6. Instalar

```bash
cd ~/EDGE-FIRMWARE-SGPMP
sudo ./raspberry/scripts/install.sh --env ~/edge-agent.env
```

El instalador (tarda 3–6 minutos en una Pi 3):

1. Instala `python3`, `python3-venv`, `python3-dev` y `rsync`.
2. Habilita NTP y el SPI (`raspi-config`).
3. Crea el usuario de servicio `sgpmp-edge` (sin login) y lo agrega a los
   grupos `spi` y `gpio`.
4. Copia el código a `/opt/sgpmp-edge/app` y crea el entorno virtual en
   `/opt/sgpmp-edge/venv`.
5. Copia la configuración a `/etc/sgpmp/edge-agent.env` (0600, root).
6. **Valida la configuración** (`--check-config`). Si hay un error, se
   detiene sin arrancar nada y muestra qué variable falta o es inválida.
7. Instala el servicio `edge-agent` y los logs persistentes de journald.
8. Habilita el arranque automático y arranca el servicio.

Al terminar, **borrar la copia local del archivo**:

```bash
shred -u ~/edge-agent.env
```

Si el SPI se acaba de habilitar, reiniciar una vez para que aparezca
`/dev/spidev0.0`. En la etapa A no hace falta.

```bash
sudo reboot
```

## 7. Verificar la etapa A

### 7.1 En la Pi

```bash
systemctl status edge-agent          # active (running)
journalctl -u edge-agent -f          # logs en vivo (Ctrl+C para salir)
```

Líneas esperadas:

```
edge_agent 0.1.0 arrancando: Settings(host='…', port=1884, serials=('TC-M09-…',))
Conectando a …:1884 como edge-TC-M09-… (TLS=False)
Conectado (session_present=False)
```

La primera vez `session_present=False` es normal. En reconexiones
posteriores debería ser `True`: eso confirma la sesión persistente.

### 7.2 En el servidor

- `GET /v1/devices` (o la UI) muestra el serial en `ACTIVO` en menos de 5
  minutos.
- Cambiar la configuración del dispositivo desde la UI. Debe quedar en
  **APLICADA**, y en el log de la Pi aparece
  `Comando TC-M09-… → OK (aplicado)`.

**Esto reemplaza la prueba manual por SSH del flujo F1.**

### 7.3 Resiliencia (5 minutos)

| Prueba | Comando en la Pi | Resultado esperado |
|---|---|---|
| Reinicio | `sudo reboot` | El servicio vuelve solo; el serial sigue `ACTIVO` |
| Proceso colgado | `sudo systemctl kill -s SIGSTOP edge-agent` | En ≤ 60 s el log muestra `Watchdog timeout` y el servicio se reinicia |
| Corte de red | Desconectar WiFi/cable 5 min y reconectar | Heartbeat con `estado_local_buffer=ACTIVO`, luego `INACTIVO`; no se pierden datos |

## 8. Prueba de aceptación automática (M3)

Corre los escenarios de `DOC_PRUEBA_ACL_EDGE_DEV.docx` sin intervención
manual. **Detener el servicio antes**: el test usa el mismo `client_id` y
dos clientes con el mismo id se desconectan entre sí.

```bash
sudo systemctl stop edge-agent

cd ~/EDGE-FIRMWARE-SGPMP/raspberry
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Cargar la misma config del servicio + datos del API (sin dejarlos en disco)
set -a; . <(sudo cat /etc/sgpmp/edge-agent.env); set +a
export EDGE_IT_API_URL=https://<host de dev>/v1
read -rs -p "Token del API: " EDGE_IT_API_TOKEN; export EDGE_IT_API_TOKEN; echo
# Opcional, para P6/E1: EDGE_IT_GATEWAY_USERNAME / EDGE_IT_GATEWAY_PASSWORD

.venv/bin/pytest -m integration -v      # ~5 minutos

sudo systemctl start edge-agent
```

Resultado esperado: todos los tests en `PASSED`. P6/E1 salen `SKIPPED` si no
se dio la credencial del gateway. El test devuelve el dispositivo a la
config por defecto al terminar. Si `test_comando_entregado_con_agente_desconectado`
falla, revisar la persistencia de sesiones en Mosquitto (plan §4, punto 6).

## 9. Etapa B — activar LoRa

Requisitos: frecuencia y potencia confirmadas con la ANE, módulo cableado
(sección 3) y al menos un nodo ESP32 flasheado (`esp32/README.md`) con el
mismo `NET_ID`, frecuencia y SF.

### 9.1 Comprobar el hardware

```bash
ls /dev/spidev0.*        # debe existir /dev/spidev0.0
```

### 9.2 Agregar la configuración LoRa

```bash
sudo nano /etc/sgpmp/edge-agent.env
```

Cambiar `EDGE_SOURCE=lora` y agregar:

```ini
EDGE_LORA_FREQ_HZ=<frecuencia confirmada, ej. 915000000>
EDGE_LORA_TX_POWER_DBM=<potencia confirmada>
EDGE_LORA_NET_ID=0x01
EDGE_LORA_NODES=1=TC-M09-G64-1789321890010
EDGE_LORA_VARIABLES=1=temperatura_ambiente:C;2=humedad_relativa:%
EDGE_LORA_RESET_GPIO=25
```

- `EDGE_LORA_NODES`: `node_id=serial` por cada ESP32. Con serial por
  ESP32, cada nodo lleva su serial y todos van en `EDGE_SERIALS`. Con serial
  por sitio, todos los nodos apuntan al mismo serial.
- `EDGE_LORA_VARIABLES`: `code=nombre:unidad`. Los codes deben coincidir
  con los del firmware (`SENSOR_CODE_*`).

Validar antes de reiniciar:

```bash
sudo systemd-run --pipe --wait -p EnvironmentFile=/etc/sgpmp/edge-agent.env \
    /opt/sgpmp-edge/venv/bin/python -m edge_agent --check-config
```

### 9.3 Probar el enlace con el monitor

```bash
sudo systemctl stop edge-agent
sudo systemd-run --pipe --wait -p EnvironmentFile=/etc/sgpmp/edge-agent.env \
    /opt/sgpmp-edge/venv/bin/python -m edge_agent.lora.monitor
```

Encender el ESP32: al arrancar transmite enseguida. Deben aparecer líneas
`nodo 0x0001 … TELEMETRIA` y `ESTADO` con RSSI/SNR. `Ctrl+C` muestra el
resumen de pérdidas. Si aparece `SX1276 no responde por SPI`, revisar el
cableado (sección 3) y que exista `/dev/spidev0.0`.

### 9.4 Arrancar con LoRa

```bash
sudo systemctl start edge-agent
journalctl -u edge-agent -f
```

Esperado: `Gateway LoRa escuchando en 915.000 MHz SF9 (net_id=0x01, N nodos)`.
Un cambio de config desde la UI produce `CONFIG vN enviado a nodo 0x0001` en
la próxima ventana del nodo y luego `Nodo 0x0001 aplicó config vN`.

Las pruebas completas con hardware están en `docs/PRUEBAS_CAMPO.md`.

## 10. Operación diaria (sin SSH)

- El servicio arranca con la Pi, se reconecta solo y se reinicia si se cae
  o se traba.
- El estado se ve desde el servidor en cada heartbeat: `version_firmware`,
  `estado_local_buffer`, `datos_pendientes_buffer`, `reloj_sincronizado` y
  batería/RSSI/SNR de los nodos.
- Los logs quedan en la Pi hasta 3 meses (tope de 200 MB), por si hace falta
  diagnóstico en sitio.

**Endurecer el acceso** una vez verificada la instalación: dejar SSH solo con
clave (`PasswordAuthentication no` en `/etc/ssh/sshd_config`) o
deshabilitarlo (`sudo systemctl disable --now ssh`). Mientras no exista el
mecanismo de actualización remota (plan §4, punto 11), deshabilitarlo
implica acceso físico para actualizar.

## 11. Actualizar el software

```bash
cd ~/EDGE-FIRMWARE-SGPMP
git pull
sudo ./raspberry/scripts/install.sh     # sin --env: conserva /etc/sgpmp/edge-agent.env
```

El buffer y la config local (`/var/lib/sgpmp-edge/`) se conservan entre
actualizaciones.

## 12. Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El instalador termina en `configuración inválida` | Falta una variable obligatoria o un valor tiene mal formato | El mensaje dice cuál; corregir el `.env` y reinstalar con `--env` |
| `Broker rechazó la conexión: Not authorized` | Contraseña o usuario incorrectos | Corregir `EDGE_MQTT_PASSWORD` en `/etc/sgpmp/edge-agent.env` y `sudo systemctl restart edge-agent` |
| Se conecta y se desconecta en bucle | Otro cliente usa el mismo `client_id` (ej. el test M3 corriendo) | Detener el otro cliente o cambiar `EDGE_MQTT_CLIENT_ID` |
| El serial no pasa a `ACTIVO` | Serial no registrado, o distinto al de la BD | Comparar `EDGE_SERIALS` con `modulo9.dispositivos_iot` (mayúsculas incluidas) |
| La UI queda en `NO_CONF` | El comando no llegó en 30 s | Revisar el log de la Pi (¿`Comando … → OK`?) y la conexión; reintentar desde la UI |
| `reloj_sincronizado: false` | Sin NTP | `timedatectl`; revisar DNS y salida a internet |
| `status=217/USER` o `216/GROUP` al arrancar | Falta el usuario o un grupo | Reinstalar; en un Linux que no sea Raspberry Pi OS, quitar `SupplementaryGroups` del servicio |
| `SX1276 no responde por SPI (RegVersion=0x00)` | Cableado, alimentación o SPI deshabilitado | Revisar la tabla de la sección 3, `ls /dev/spidev0.*`, reiniciar |
| El monitor no ve tramas | Frecuencia, SF, sync word o `NET_ID` distintos a los del nodo | Igualar `EDGE_LORA_*` con los `build_flags` del ESP32 |
| `code de variable N sin mapear` | Falta ese code en `EDGE_LORA_VARIABLES` | Agregarlo y reiniciar el servicio |

Comandos útiles:

```bash
systemctl status edge-agent
journalctl -u edge-agent --since "1 hour ago"
sudo systemctl restart edge-agent
sudo ls -l /var/lib/sgpmp-edge/        # buffer.db y config.json
```
