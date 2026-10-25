# Mejora futura — Lista de dispositivos del Edge desde la plataforma

| | |
|---|---|
| **Estado** | Propuesta, pendiente. No se implementa con el protocolo LoRa v2. |
| **Depende de** | Protocolo LoRa v2 con identidad por serial y claves derivadas (`PROTOCOLO_LORA.md`) |
| **Repos** | BROKER-MQTT-SGPMP (topic nuevo y ACL), EDGE-FIRMWARE-SGPMP (la Pi lo consume) |

## 1. Problema

Para dar de alta un nodo sensor hoy hay que hacerlo dos veces:

1. En la plataforma: registrar el dispositivo y asignarlo a su Gateway Edge
   (`modulo9.dispositivos_iot.id_dispositivo_gateway`).
2. En la Raspberry, por SSH: agregar su serial a `EDGE_SERIALS` en
   `/etc/sgpmp/edge-agent.env` y reiniciar el servicio.

El broker ya conoce esa lista. Con cada conexión, y con cada aviso
`POST /v1/devices/{serial}/credential/sync` del backend, lee de modulo9 qué
dispositivos activos atiende cada Edge para armar sus roles `serial-<S>`
(`app/services/credenciales_mqtt.py`, `_seriales_de_la_credencial`). El paso 2
repite a mano un dato que el sistema ya tiene, y si se olvida, la Pi descarta
las tramas del nodo nuevo.

## 2. Propuesta

El broker le publica a cada Edge la lista de dispositivos que atiende, en un
topic retenido propio. La Pi se suscribe y arma su lista de seriales sola. Con
el protocolo v2, de esa lista también salen el `node_id` y la clave de cada
nodo, así que dar de alta un nodo en la plataforma basta para que la Pi lo
reconozca; solo falta flashearlo.

### 2.1 Topic y payload

```
sgpmp/<serial_edge>/dispositivos      broker -> Edge, retenido, QoS 1
```

```json
{
  "version": 7,
  "emitido_en": "2026-10-25T14:03:00Z",
  "dispositivos": [
    {"serial": "TC-M09-...-0001", "tipo": "NODO_SENSOR"},
    {"serial": "TC-M09-...-0002", "tipo": "CAMARA"}
  ]
}
```

- `dispositivos`: los activos que apuntan a ese Edge, los mismos que ya entran
  a su ACL.
- `tipo`: para que la Pi derive `node_id` y clave LoRa solo de los nodos de
  radio y no de las cámaras.
- `version`: crece con cada cambio; la Pi ignora una versión menor o igual a la
  que ya aplicó.
- Retenido: una Pi que se reinicia o reconecta recibe la lista vigente sin
  esperar un cambio.

### 2.2 Cambios en BROKER-MQTT-SGPMP

| Pieza | Cambio |
|---|---|
| `credenciales_mqtt._topics_hacia_el_nodo` | Agregar `dispositivos`: entra solo al rol `serial-<S>` del Edge (lectura) y al rol `gateway` (escritura), con el mismo mecanismo que `command` |
| `sincronizar_credencial` y `sincronizar` | Tras alinear los roles de un Edge, publicar su lista retenida si cambió |
| Roles existentes | `_alinear_roles` solo crea los roles que faltan; los `serial-<S>` ya creados necesitan un `modifyRole` una vez para sumar el topic. `modifyRole` desconecta a quien tenga el rol (medido en 2.1.2), así que conviene hacerlo en una ventana de mantenimiento o aceptar una reconexión por Edge |
| `registry.mapa_dispositivos` | Incluir el tipo de dispositivo, si hoy no lo trae |
| Tests | `tests/test_credenciales_mqtt.py` y `scripts/e2e_credenciales_mqtt.py`: la lista llega al Edge correcto, otro Edge no puede suscribirse a ella y un cambio de asignación la republica |
| Docs | `GUIA_CONEXION_IOT.md` (topic nuevo) y el registro de trazabilidad |

### 2.3 Cambios en EDGE-FIRMWARE-SGPMP

- Suscribirse a `sgpmp/<EDGE_MQTT_USERNAME>/dispositivos` y persistir la lista
  en el estado local (`/var/lib/sgpmp-edge/`), para arrancar con la última
  conocida aunque no haya red.
- `EDGE_SERIALS` pasa a ser opcional: si está, se une a la lista recibida
  (útil sin conexión o en pruebas); si no, manda la plataforma.
- Al cambiar la lista: derivar `node_id` y clave de los nodos nuevos, dejar de
  aceptar tramas de los dados de baja y registrar el cambio en el log.
- Validar cada serial con el mismo patrón que usa el broker (`PATRON_SERIAL`)
  antes de usarlo.

## 3. Lo que no cambia

- La credencial MQTT del Edge y su rotación.
- El secreto LoRa del sitio: nunca sale de la Pi. La plataforma solo informa
  seriales, no claves.
- El flasheo de cada nodo, que sigue necesitando su clave desde la Pi
  (`lora-key <serial>`).

## 4. Preguntas abiertas

1. ¿El tipo de dispositivo en modulo9 distingue nodos LoRa de cámaras, o hace
   falta un campo nuevo? Si hace falta, es una migración de sgpmp-backend con
   autorización del DBA.
2. ¿El alta de este topic requiere RFC formal (`GUIA_CONEXION_IOT.md` §5), o
   basta con la trazabilidad del broker?
3. ¿Cómo se aplica el `modifyRole` a los roles existentes en `test` y `prod`
   (ventana de mantenimiento o reconexión aceptada)?
