"""Gateway LoRa del sitio: implementa `DataSource` con el SX1276 real (Fase 2).

Un hilo propio es el único que toca la radio: escucha en RX continuo,
decodifica tramas (docs/PROTOCOLO_LORA.md) y, cuando un nodo abre su ventana
RX tras un ESTADO con config desactualizada, le transmite un CONFIG. El hilo
del agente solo lee resultados y deja la config deseada, bajo un lock.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from edge_agent.config_store import DeviceConfig
from edge_agent.lora import keys
from edge_agent.lora import protocol as p
from edge_agent.lora.estado import LoraEstado
from edge_agent.lora.sx1276 import SX1276, Packet, RadioConfig, RadioError
from edge_agent.sources import Reading

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoraVariable:
    nombre: str
    unidad: str


@dataclass
class _Desired:
    cfg_version: int
    frecuencia_captura_min: int
    intervalo_transmision_min: int

    def matches(self, frecuencia: int, intervalo: int) -> bool:
        return (self.frecuencia_captura_min, self.intervalo_transmision_min) == (
            frecuencia,
            intervalo,
        )


@dataclass
class _Node:
    node_id: int
    serial: str
    last_seq: int | None = None
    perdidas: int = 0
    duplicadas: int = 0
    last_seen: float | None = None
    rssi_dbm: float | None = None
    snr_db: float | None = None
    bateria_pct: int | None = None
    reported: tuple[int, int] | None = None  # (frecuencia, intervalo) del último ESTADO
    sensores: tuple[int, int] | None = None  # (detectados, fallando) del último ESTADO
    downlink_seq: int = 0
    desired: _Desired | None = None
    clave: bytes | None = None  # v2; None = nodo v1 (EDGE_LORA_NODES)
    mic_invalidos: int = 0
    repetidas: int = 0


class LoraGateway:
    def __init__(
        self,
        radio: SX1276,
        radio_config: RadioConfig,
        net_id: int,
        nodes: dict[int, str],
        variables: dict[int, LoraVariable],
        *,
        nodos_v2: dict[int, tuple[str, bytes]] | None = None,
        aceptar_v1: bool = True,
        estado: LoraEstado | None = None,
        downlink_delay_s: float = 0.05,
        poll_interval_s: float = 0.01,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`nodes`: nodos v1 (node_id -> serial). `nodos_v2`: node_id derivado ->
        (serial, clave), ver lora/keys.py. Un mismo serial puede estar en ambos
        mientras su nodo migra."""
        self._radio = radio
        self._radio_config = radio_config
        self._net_id = net_id
        self._variables = variables
        self._downlink_delay_s = downlink_delay_s
        self._poll_interval_s = poll_interval_s
        self._clock = clock
        self._sleep = sleep
        self._aceptar_v1 = aceptar_v1
        self._estado = estado if estado is not None else LoraEstado(None)
        self._nodes = {nid: _Node(nid, serial) for nid, serial in nodes.items()}
        self._nodes_v2 = {
            nid: _Node(nid, serial, clave=clave)
            for nid, (serial, clave) in (nodos_v2 or {}).items()
        }
        self._by_serial: dict[str, list[_Node]] = defaultdict(list)
        for node in [*self._nodes.values(), *self._nodes_v2.values()]:
            self._by_serial[node.serial].append(node)
        self._readings: dict[str, list[Reading]] = defaultdict(list)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_version = int(clock()) & 0xFFFF
        self.descartadas = 0

    # --- ciclo de vida --------------------------------------------------

    def start(self) -> None:
        self._radio.init(self._radio_config)
        self._radio.receive()
        self._thread = threading.Thread(target=self._run, name="lora-gateway", daemon=True)
        self._thread.start()
        logger.info(
            "Gateway LoRa escuchando en %.3f MHz SF%d (net_id=0x%02x, %d nodos v2, %d nodos v1%s)",
            self._radio_config.freq_hz / 1e6,
            self._radio_config.sf,
            self._net_id,
            len(self._nodes_v2),
            len(self._nodes),
            "" if self._aceptar_v1 else ", v1 desactivado",
        )
        for node in self._nodes_v2.values():
            # La huella permite comparar con la que muestra el nodo al arrancar.
            logger.info(
                "Nodo v2 0x%04x = %s, huella de clave %s",
                node.node_id,
                node.serial,
                keys.huella(node.clave),
            )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._radio.standby()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                packet = self._radio.poll()
                if packet is None:
                    self._sleep(self._poll_interval_s)
                    continue
                self.handle_packet(packet)
            except Exception:
                # Un error de SPI transitorio no debe matar el gateway; si
                # persiste, el watchdog de systemd no se ve afectado pero los
                # heartbeats de los nodos se cortan y el servidor lo ve.
                logger.exception("Error en el hilo LoRa; se reinicia la radio")
                self._sleep(1)
                try:
                    self._radio.init(self._radio_config)
                    self._radio.receive()
                except Exception:
                    logger.exception("No se pudo reiniciar la radio")

    # --- recepción (hilo LoRa) ------------------------------------------

    def handle_packet(self, packet: Packet) -> None:
        data = packet.payload
        if data[:1] == bytes([p.PROTOCOL_VERSION_V2]):
            self._handle_v2(packet)
        else:
            self._handle_v1(packet)

    def _handle_v1(self, packet: Packet) -> None:
        now = self._clock()
        try:
            frame, message = p.parse(packet.payload)
        except p.FrameError as exc:
            self.descartadas += 1
            logger.debug("Trama descartada: %s (%s)", exc, packet.payload.hex())
            return
        if frame.net_id != self._net_id:
            self.descartadas += 1
            return
        node = self._nodes.get(frame.node_id)
        if node is None:
            self.descartadas += 1
            logger.info("Trama v1 de node_id no configurado: 0x%04x", frame.node_id)
            return
        # Anti-degradación: un nodo que ya habló v2 no vuelve a v1, y con v1
        # desactivado no se acepta ninguno. Una trama v1 así es sospechosa.
        if not self._aceptar_v1 or self._estado.migrado(node.serial):
            self.descartadas += 1
            logger.warning(
                "Trama v1 rechazada del nodo 0x%04x (%s): %s",
                node.node_id,
                node.serial,
                "ya usa v2" if self._aceptar_v1 else "v1 desactivado",
            )
            return

        with self._lock:
            if not self._track_seq(node, frame.seq):
                return
            downlink = self._accept(node, packet, frame.seq, message, now)

        if downlink is not None:
            self._send_downlink(node, downlink)

    def _handle_v2(self, packet: Packet) -> None:
        """Orden de PROTOCOLO_LORA.md §11: cabecera, nodo, fcnt, MIC; solo entonces se procesa."""
        now = self._clock()
        try:
            header = p.peek_v2(packet.payload)
        except p.FrameError as exc:
            self.descartadas += 1
            logger.debug("Trama v2 descartada: %s (%s)", exc, packet.payload.hex())
            return
        if header.net_id != self._net_id:
            self.descartadas += 1
            return
        node = self._nodes_v2.get(header.node_id)
        if node is None:
            self.descartadas += 1
            logger.info("Trama v2 de node_id no configurado: 0x%04x", header.node_id)
            return
        ultimo = self._estado.ultimo_fcnt(node.serial)
        if ultimo is not None and header.fcnt <= ultimo:
            self.descartadas += 1
            node.repetidas += 1
            logger.warning(
                "Nodo 0x%04x: trama repetida o vieja (fcnt %d, último %d)",
                node.node_id,
                header.fcnt,
                ultimo,
            )
            return
        try:
            _, message = p.parse_v2(node.clave, p.Direccion.SUBIDA, packet.payload)
        except p.MicInvalido:
            self.descartadas += 1
            node.mic_invalidos += 1
            logger.warning(
                "Nodo 0x%04x (%s): MIC inválido (%d en total): clave distinta o trama alterada",
                node.node_id,
                node.serial,
                node.mic_invalidos,
            )
            return
        except p.FrameError as exc:
            self.descartadas += 1
            logger.warning("Nodo 0x%04x: payload v2 inválido: %s", node.node_id, exc)
            return

        self._estado.aceptar_fcnt(node.serial, header.fcnt)  # en disco antes de procesar
        with self._lock:
            # Un salto grande es un reinicio del nodo (salta al siguiente bloque
            # de contadores), no pérdida.
            if ultimo is not None and header.fcnt - ultimo - 1 < 256:
                node.perdidas += header.fcnt - ultimo - 1
            downlink = self._accept(node, packet, header.fcnt, message, now)

        if downlink is not None:
            self._send_downlink(node, downlink)

    def _accept(
        self, node: _Node, packet: Packet, seq: int, message: p.Message, now: float
    ) -> p.ConfigDownlink | None:
        node.last_seen = now
        node.rssi_dbm = packet.rssi_dbm
        node.snr_db = packet.snr_db
        return self._dispatch(node, seq, message, now)

    def _track_seq(self, node: _Node, seq: int) -> bool:
        if node.last_seq is not None:
            if seq == node.last_seq:
                node.duplicadas += 1
                return False
            gap = (seq - node.last_seq - 1) % 256
            # Un salto enorme es un reinicio del nodo (seq vuelve a 0), no pérdida.
            if gap < 128:
                node.perdidas += gap
        node.last_seq = seq
        return True

    def _dispatch(
        self, node: _Node, seq: int, message: p.Message, now: float
    ) -> p.ConfigDownlink | None:
        match message:
            case p.Telemetria():
                self._store_telemetry(node, seq, message, now)
            case p.Estado():
                node.bateria_pct = message.bateria_pct
                node.reported = (message.frecuencia_captura_min, message.intervalo_transmision_min)
                if message.flags & p.FlagsEstado.REINICIO:
                    logger.info(
                        "Nodo 0x%04x reinició (fw %d.%d)",
                        node.node_id,
                        message.fw_major,
                        message.fw_minor,
                    )
                self._track_sensores(node, message)
                desired = node.desired
                if desired is not None and not desired.matches(*node.reported):
                    return p.ConfigDownlink(
                        desired.cfg_version,
                        desired.frecuencia_captura_min,
                        desired.intervalo_transmision_min,
                    )
            case p.AckConfig():
                desired = node.desired
                if (
                    message.resultado == p.ResultadoConfig.OK
                    and desired is not None
                    and (message.cfg_version == desired.cfg_version)
                ):
                    node.reported = (
                        desired.frecuencia_captura_min,
                        desired.intervalo_transmision_min,
                    )
                    logger.info("Nodo 0x%04x aplicó config v%d", node.node_id, message.cfg_version)
                else:
                    logger.warning("Nodo 0x%04x respondió %s", node.node_id, message)
            case _:
                logger.info("Nodo 0x%04x envió un tipo solo-downlink: %s", node.node_id, message)
        return None

    def _track_sensores(self, node: _Node, message: p.Estado) -> None:
        """Registra en el log solo los cambios: un sensor que aparece, se desconecta o falla."""
        if message.sensores_detectados is None:
            return  # firmware anterior: no informa sensores
        sensores = (message.sensores_detectados, message.sensores_fallando or 0)
        if sensores == node.sensores:
            return
        node.sensores = sensores
        detectados, fallando = sensores
        log = logger.warning if fallando else logger.info
        log(
            "Nodo 0x%04x sensores detectados: %s; fallando: %s",
            node.node_id,
            p.Sensor.describe(detectados),
            p.Sensor.describe(fallando),
        )

    def _store_telemetry(self, node: _Node, seq: int, message: p.Telemetria, now: float) -> None:
        captured = datetime.fromtimestamp(now - message.age_s, UTC)
        lora_meta = {
            "node_id": node.node_id,
            "seq": seq,
            "rssi": node.rssi_dbm,
            "snr": node.snr_db,
            "perdidas": node.perdidas,
        }
        for measurement in message.measurements:
            variable = self._variables.get(measurement.code)
            if variable is None:
                logger.warning(
                    "Nodo 0x%04x: code de variable %d sin mapear (EDGE_LORA_VARIABLES)",
                    node.node_id,
                    measurement.code,
                )
                continue
            if not math.isfinite(measurement.value):
                # El float de la trama puede ser NaN/Inf aunque el CRC o el MIC
                # sean válidos: no es una medición y no es JSON válido.
                logger.warning(
                    "Nodo 0x%04x: %s = %r descartada (valor no finito)",
                    node.node_id,
                    variable.nombre,
                    measurement.value,
                )
                continue
            self._readings[node.serial].append(
                Reading(
                    serial=node.serial,
                    variable=variable.nombre,
                    valor_crudo=round(measurement.value, 4),
                    unidad=variable.unidad,
                    timestamp_captura=captured,
                    nivel_bateria_pct=node.bateria_pct,
                    calidad_senal_rssi=node.rssi_dbm,
                    calidad_senal_snr=node.snr_db,
                    metadatos={"lora": lora_meta},
                )
            )

    def _send_downlink(self, node: _Node, message: p.ConfigDownlink) -> None:
        # El nodo abre su ventana RX al terminar el ESTADO; se le da un
        # margen para que su radio pase de TX a RX.
        self._sleep(self._downlink_delay_s)
        if node.clave is not None:
            # El fcnt queda en disco antes de transmitir: un reinicio nunca repite nonce.
            fcnt = self._estado.siguiente_bajada(node.serial)
            data = p.build_v2(
                node.clave, p.Direccion.BAJADA, self._net_id, node.node_id, fcnt, message
            )
        else:
            node.downlink_seq = (node.downlink_seq + 1) % 256
            data = p.build(self._net_id, node.node_id, node.downlink_seq, message)
        try:
            self._radio.transmit(data)
            logger.info("CONFIG v%d enviado a nodo 0x%04x", message.cfg_version, node.node_id)
        except RadioError:
            logger.exception("Falló el downlink al nodo 0x%04x", node.node_id)
        finally:
            self._radio.receive()

    # --- DataSource (hilo del agente) -----------------------------------

    def read(self, serial: str, now: datetime) -> list[Reading]:
        with self._lock:
            readings = self._readings.pop(serial, [])
        return readings

    def is_alive(self, serial: str, now: datetime, max_silence_s: float) -> bool:
        nodes = self._by_serial.get(serial)
        if not nodes:
            return True  # serial propio de la Raspberry, sin nodos detrás
        limit = now.timestamp() - max_silence_s
        with self._lock:
            return any(n.last_seen is not None and n.last_seen >= limit for n in nodes)

    def status(self, serial: str) -> dict[str, Any]:
        with self._lock:
            nodes = [n for n in self._by_serial.get(serial, []) if n.last_seen is not None]
            if not nodes:
                return {}
            latest = max(nodes, key=lambda n: n.last_seen)
            baterias = [n.bateria_pct for n in nodes if n.bateria_pct is not None]
            result: dict[str, Any] = {
                "calidad_senal_rssi": latest.rssi_dbm,
                "calidad_senal_snr": latest.snr_db,
            }
            if baterias:
                result["nivel_bateria_pct"] = float(min(baterias))
            return result

    def apply_config(self, serial: str, config: DeviceConfig) -> None:
        with self._lock:
            for node in self._by_serial.get(serial, []):
                if node.desired is not None and node.desired.matches(
                    config.frecuencia_captura_min, config.intervalo_transmision_min
                ):
                    continue
                node.desired = _Desired(
                    self._next_version,
                    config.frecuencia_captura_min,
                    config.intervalo_transmision_min,
                )
                self._next_version = (self._next_version + 1) & 0xFFFF
