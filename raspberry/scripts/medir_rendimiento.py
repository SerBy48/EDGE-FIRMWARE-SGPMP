"""Rendimiento de RF-55 en el Edge: latencia (TC-M03-082) y ráfaga (TC-M03-102).

Corre el agente real (mismo motor de reglas, mismo buffer SQLite con fsync,
mismo historial) leyendo del gateway LoRa real, al que se le entregan tramas
TELEMETRIA sintéticas como si llegaran de la radio. No usa radio ni broker:
el enlace MQTT es un contador. Mide en la máquina donde corre; el resultado
que vale para QA es el de la Raspberry del ambiente de test.

Uso:
    python scripts/medir_rendimiento.py latencia [--capturas 500]
    python scripts/medir_rendimiento.py rafaga [--nodos 20 --capturas 32]
    python scripts/medir_rendimiento.py todo [--json]

Criterios (docs/CONTRATO_EVENTO_EDGE.md §8). Sale con 1 si alguno no se cumple.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import inyectar_lecturas as iny  # noqa: E402

from edge_agent.agent import Agent  # noqa: E402
from edge_agent.buffer import Buffer  # noqa: E402
from edge_agent.config_store import ConfigStore, DeviceConfig, UmbralStore  # noqa: E402
from edge_agent.historial import Historial  # noqa: E402
from edge_agent.lora import protocol as p  # noqa: E402
from edge_agent.lora.gateway import LoraGateway, LoraVariable  # noqa: E402
from edge_agent.lora.sx1276 import Packet, RadioConfig  # noqa: E402
from edge_agent.mqtt_client import PubAck  # noqa: E402
from edge_agent.reglas.evaluador import MAX_REGLAS_ACTIVAS  # noqa: E402
from edge_agent.reglas.store import ReglaStore  # noqa: E402

NET = 0x2A
LATENCIA_MAX_MS = 500.0  # RF-55.1, por lectura
RECEPCION_MAX_MS = 50.0  # una trama LoRa entrando al gateway mientras se clasifica
CICLO_MAX_MS = 1000.0  # el loop del agente vuelve a atender MQTT

# code LoRa -> variable, unidad, rango de valores sintéticos (dentro y fuera de umbral)
VARIABLES = {
    1: ("temperatura_ambiente", "C", (18.0, 36.0)),
    2: ("humedad_relativa", "%", (40.0, 95.0)),
    3: ("nh3", "ppm", (5.0, 40.0)),
    4: ("co2", "ppm", (800.0, 3000.0)),
}


def _niveles(minimo: float, maximo: float) -> list[dict[str, Any]]:
    a, b = minimo + (maximo - minimo) * 0.5, minimo + (maximo - minimo) * 0.75
    return [
        {"nivel": "normal", "limite_inferior": minimo, "limite_superior": a},
        {"nivel": "precaucion", "limite_inferior": a, "limite_superior": b},
        {"nivel": "critico", "limite_inferior": b, "limite_superior": maximo},
    ]


UMBRALES = [
    {"id_umbral_ambiental": i, "version": None, "variable": nombre, "unidad": unidad,
     "valor_min": rango[0], "valor_max": rango[1] - (rango[1] - rango[0]) * 0.1,
     "niveles": _niveles(rango[0], rango[1] - (rango[1] - rango[0]) * 0.1)}
    for i, (nombre, unidad, rango) in VARIABLES.items()
]  # fmt: skip

# Las 5 reglas activas que admite RF-55.2: dos Tipo B y tres Tipo A.
REGLAS = [
    {"nombre": "THI", "prevalece_sobre_regla_simple": True},
    {"nombre": "Calidad del aire"},
    {"nombre": "Temperatura alta", "tipo_sensor": "TEMPERATURA", "categoria_variable": "AMBIENTAL",
     "umbral_max": "30", "prevalece_sobre_regla_simple": True},
    {"nombre": "Temperatura alta sostenida", "tipo_sensor": "TEMPERATURA",
     "categoria_variable": "AMBIENTAL", "umbral_max": "28", "ventana_confirmacion_min": "5",
     "lectura_confirmacion_max": "3"},
    {"nombre": "Humedad alta sostenida", "tipo_sensor": "HUMEDAD", "categoria_variable": "RELATIVA",
     "umbral_max": "85", "ventana_confirmacion_min": "10", "lectura_confirmacion_max": "2"},
]  # fmt: skip
assert len(REGLAS) == MAX_REGLAS_ACTIVAS


class LinkContador:
    """Enlace MQTT conectado que solo cuenta (paho encola y vuelve: no bloquea)."""

    connected = True

    def __init__(self) -> None:
        self.publicados = 0
        self.topics: dict[int, str] = {}
        # id_evento -> tiempo_procesamiento_ms (un reenvío no se cuenta dos veces)
        self.evaluaciones: dict[str, float] = {}

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> int | None:
        self.publicados += 1
        self.topics[self.publicados] = topic
        if topic.endswith("/evento"):
            meta = json.loads(payload)["metadata_edge"]
            self.evaluaciones[meta["id_evento"]] = meta["tiempo_procesamiento_ms"]
        return self.publicados


@dataclass
class Medicion:
    nombre: str
    parametros: dict[str, Any]
    resultados: dict[str, Any] = field(default_factory=dict)
    criterios: list[tuple[str, bool]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(cumple for _, cumple in self.criterios)


class Banco:
    """Agente + gateway sobre un directorio temporal."""

    def __init__(self, directorio: Path, nodos: int) -> None:
        self.serials = tuple(f"TC-M03-PERF-{i:04d}" for i in range(1, nodos + 1))
        # Los comandos llegan por el serial propio de la Raspberry (sin nodos detrás).
        settings = replace(
            iny._settings(directorio),
            serials=(iny.GATEWAY, *self.serials),
            buffer_max_rows=200_000,
        )
        self.reloj = time.time()
        self.gateway = LoraGateway(
            None,  # type: ignore[arg-type]  # sin radio: no se envían downlinks
            RadioConfig(freq_hz=915_000_000, tx_power_dbm=14),
            NET,
            {i: s for i, s in enumerate(self.serials, start=1)},
            {code: LoraVariable(nombre, unidad) for code, (nombre, unidad, _) in VARIABLES.items()},
            downlink_delay_s=0,
            clock=lambda: self.reloj,
            sleep=lambda s: None,
        )
        self.link = LinkContador()
        self.buffer = Buffer(settings.buffer_path, settings.buffer_max_rows)
        self.historial = Historial(settings.historial_path, settings.historial_max_filas)
        self.agente = Agent(
            settings,
            self.link,  # type: ignore[arg-type]
            ConfigStore(settings.state_path, DeviceConfig(1, 1)),
            self.buffer,
            self.gateway,
            umbrales=UmbralStore(directorio / "umbrales.json"),
            reglas=ReglaStore(directorio / "reglas.json"),
            historial=self.historial,
            reloj_sincronizado=lambda: False,
            clock=lambda: self.reloj,
        )
        for i, umbral in enumerate(UMBRALES):
            iny._comando(self.agente, {**umbral, "tipo_comando": "UMBRAL_AMBIENTAL"}, f"u-{i}", 0)
        for i, regla in enumerate(REGLAS, start=1):
            comando = {"id_regla_alerta": i, "version_regla": 1, "regla": regla,
                       "es_activa": True, "tipo_comando": "REGLA_COMPUESTA"}  # fmt: skip
            iny._comando(self.agente, comando, f"r-{i}", 0)
        self._seq: dict[int, int] = {}
        self._confirmados = 0
        self.telemetria_confirmada = 0
        self._azar = random.Random(55)

    def tramas(self, nodo: int, capturas: int, variables: int) -> list[bytes]:
        """Memoria llena de un nodo: una TELEMETRIA por captura, la más vieja primero."""
        tramas = []
        for k in range(capturas):
            medidas = tuple(
                p.Measurement(code, round(self._azar.uniform(*VARIABLES[code][2]), 2))
                for code in range(1, variables + 1)
            )
            seq = self._seq[nodo] = (self._seq.get(nodo, 0) + 1) % 256
            tramas.append(p.build(NET, nodo, seq, p.Telemetria((capturas - 1 - k) * 60, medidas)))
        return tramas

    def recibir(self, trama: bytes) -> float:
        """Entrega una trama al gateway como lo hace su hilo de radio; ms que tardó."""
        inicio = time.perf_counter()
        self.gateway.handle_packet(Packet(trama, -60.0, 9.0))
        return (time.perf_counter() - inicio) * 1000

    def ciclo(self) -> tuple[list[float], list[float], list[float]]:
        """Vence la captura de todos los nodos a la vez y corre `tick` (el camino
        real: lectura → RF-55 → buffer de cada nodo, y el envío) hasta que no
        quedan capturas atrasadas por el presupuesto de la vuelta.

        Devuelve (ms de cada vuelta, ms de la captura de cada nodo, ms desde la
        primera vuelta hasta que terminó la captura de cada nodo).
        """
        vueltas: list[float] = []
        por_nodo: list[float] = []
        desde_inicio: list[float] = []
        original = self.agente._capture

        def medida(serial: str, due_at: float, now: float) -> None:
            antes = time.perf_counter()
            original(serial, due_at, now)
            if serial != iny.GATEWAY:
                por_nodo.append((time.perf_counter() - antes) * 1000)
                desde_inicio.append((time.perf_counter() - inicio) * 1000)

        self.agente._capture = medida  # type: ignore[method-assign]  # solo instrumenta
        self.reloj += 60  # frecuencia de captura de 1 min: vencen todos
        inicio = time.perf_counter()
        try:
            while True:
                antes = time.perf_counter()
                self.agente.tick(self.reloj)
                vueltas.append((time.perf_counter() - antes) * 1000)
                if not self.agente.capturas_pendientes:
                    break
        finally:
            del self.agente._capture
        return vueltas, por_nodo, desde_inicio

    def confirmar(self) -> float:
        """PUBACK de todo lo publicado, como lo entrega paho al loop; ms que tardó."""
        mids = range(self._confirmados + 1, self.link.publicados + 1)
        acks = [PubAck(mid) for mid in mids]
        self.telemetria_confirmada += sum(
            self.link.topics[mid].endswith("/telemetry") for mid in mids
        )
        self._confirmados = self.link.publicados
        inicio = time.perf_counter()
        self.agente.handle_events(acks)
        return (time.perf_counter() - inicio) * 1000

    def filas(self, tipo: str) -> int:
        return int(
            self.buffer._db.execute(
                "SELECT COUNT(*) FROM outbox WHERE kind = ?", (tipo,)
            ).fetchone()[0]
        )

    def cerrar(self) -> None:
        self.buffer.close()
        self.historial.close()


def _percentil(valores: list[float], q: float) -> float:
    if len(valores) < 2:
        return valores[0] if valores else 0.0
    return statistics.quantiles(valores, n=100, method="inclusive")[q - 1]


def _resumen(valores: list[float]) -> dict[str, float]:
    return {
        "p50": round(_percentil(valores, 50), 3),
        "p95": round(_percentil(valores, 95), 3),
        "p99": round(_percentil(valores, 99), 3),
        "max": round(max(valores, default=0.0), 3),
    }


def latencia(capturas: int) -> Medicion:
    """TC-M03-082: una captura normal (1 nodo, 4 variables) repetida `capturas` veces."""
    medicion = Medicion("latencia", {"nodos": 1, "capturas": capturas, "variables": 4,
                                     "reglas_activas": len(REGLAS)})  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="rf55-perf-") as tmp:
        banco = Banco(Path(tmp), nodos=1)
        banco.ciclo()  # la primera vuelta solo arranca las agendas
        por_captura = []
        for _ in range(capturas):
            (trama,) = banco.tramas(1, 1, 4)
            banco.recibir(trama)
            _, (captura,), _ = banco.ciclo()
            banco.confirmar()
            por_captura.append(captura)
        evaluacion = _tiempos_de_evaluacion(banco)
        banco.cerrar()
    medicion.resultados = {
        "captura_ms": _resumen(por_captura),
        "evaluacion_por_evento_ms": _resumen(evaluacion),
        "eventos": len(evaluacion),
    }
    peor = medicion.resultados["captura_ms"]["max"]
    medicion.criterios.append(
        (
            f"latencia por lectura ≤ {LATENCIA_MAX_MS:g} ms (máx {peor:g} ms)",
            peor <= LATENCIA_MAX_MS,
        )
    )
    return medicion


def rafaga(nodos: int, capturas: int, variables: int = 4) -> Medicion:
    """TC-M03-102: `nodos` vacían su memoria a la vez y el agente clasifica todo en un ciclo.

    Mientras el agente clasifica, otro hilo entrega una segunda ráfaga al
    gateway (como el hilo de radio): mide que la recepción no se bloquee.
    """
    total = nodos * capturas * variables
    medicion = Medicion("rafaga", {"nodos": nodos, "capturas_por_nodo": capturas,
                                   "variables": variables, "lecturas": total,
                                   "reglas_activas": len(REGLAS)})  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="rf55-perf-") as tmp:
        banco = Banco(Path(tmp), nodos=nodos)
        primera = [t for n in range(1, nodos + 1) for t in banco.tramas(n, capturas, variables)]
        segunda = [t for n in range(1, nodos + 1) for t in banco.tramas(n, capturas, variables)]
        banco.ciclo()  # la primera vuelta solo arranca las agendas
        for trama in primera:
            banco.recibir(trama)

        recepcion: list[float] = []
        hilo = threading.Thread(target=lambda: recepcion.extend(map(banco.recibir, segunda)))
        hilo.start()
        inicio = time.perf_counter()
        vueltas, por_nodo, desde_inicio = banco.ciclo()
        total_ms = (time.perf_counter() - inicio) * 1000
        hilo.join()
        puback_ms = banco.confirmar()
        # La segunda ráfaga también se clasifica: ninguna lectura queda en el gateway.
        banco.ciclo()
        banco.confirmar()

        telemetria = banco.filas("telemetry") + banco.telemetria_confirmada
        evaluacion = _tiempos_de_evaluacion(banco)
        banco.cerrar()

    medicion.resultados = {
        "vueltas_del_loop": len(vueltas),
        "vuelta_ms": _resumen(vueltas),
        "rafaga_clasificada_en_ms": round(total_ms, 3),
        "puback_de_lo_publicado_ms": round(puback_ms, 3),
        "captura_por_nodo_ms": _resumen(por_nodo),
        "lectura_desde_inicio_de_la_rafaga_ms": _resumen(desde_inicio),
        "evaluacion_por_evento_ms": _resumen(evaluacion),
        "recepcion_lora_ms": _resumen(recepcion),
        "lecturas_recibidas": 2 * total,
        "telemetria_registrada": telemetria,
        "eventos": len(evaluacion),
    }
    r = medicion.resultados
    peor_captura = r["captura_por_nodo_ms"]["max"]
    peor_trama = r["recepcion_lora_ms"]["max"]
    peor_vuelta = r["vuelta_ms"]["max"]
    medicion.criterios += [
        (f"ninguna lectura perdida ({telemetria}/{2 * total})", telemetria == 2 * total),
        (
            f"cada lectura clasificada ≤ {LATENCIA_MAX_MS:g} ms desde que el agente la toma "
            f"(máx {peor_captura:g} ms por nodo)",
            peor_captura <= LATENCIA_MAX_MS,
        ),
        (
            f"recepción LoRa sin bloqueo: trama ≤ {RECEPCION_MAX_MS:g} ms (máx {peor_trama:g})",
            peor_trama <= RECEPCION_MAX_MS,
        ),
        (
            f"el loop vuelve a MQTT en ≤ {CICLO_MAX_MS:g} ms (vuelta máx {peor_vuelta:g} ms)",
            peor_vuelta <= CICLO_MAX_MS,
        ),
    ]
    return medicion


def _tiempos_de_evaluacion(banco: Banco) -> list[float]:
    """`tiempo_procesamiento_ms` de cada evento: los publicados y los que siguen en el buffer."""
    tiempos = dict(banco.link.evaluaciones)
    for (payload,) in banco.buffer._db.execute("SELECT payload FROM outbox WHERE kind = 'evento'"):
        meta = json.loads(payload)["metadata_edge"]
        tiempos[meta["id_evento"]] = meta["tiempo_procesamiento_ms"]
    return list(tiempos.values())


def _imprimir(m: Medicion) -> None:
    print(f"== {m.nombre} {json.dumps(m.parametros, ensure_ascii=False)}")
    for clave, valor in m.resultados.items():
        print(f"   {clave}: {json.dumps(valor, ensure_ascii=False)}")
    for texto, cumple in m.criterios:
        print(f"   [{'OK   ' if cumple else 'FALLA'}] {texto}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("escenario", choices=("latencia", "rafaga", "todo"))
    parser.add_argument("--nodos", type=int, default=20)
    parser.add_argument("--capturas", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="salida en JSON")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consola de Windows (cp1252)

    mediciones = []
    if args.escenario in ("latencia", "todo"):
        mediciones.append(latencia(args.capturas or 500))
    if args.escenario in ("rafaga", "todo"):
        mediciones.append(rafaga(args.nodos, args.capturas or 32))
    if args.json:
        salida = [{"nombre": m.nombre, "parametros": m.parametros, "resultados": m.resultados,
                   "criterios": [{"criterio": t, "cumple": c} for t, c in m.criterios],
                   "ok": m.ok} for m in mediciones]  # fmt: skip
        print(json.dumps(salida, ensure_ascii=False, indent=2))
    else:
        for m in mediciones:
            _imprimir(m)
    return 0 if all(m.ok for m in mediciones) else 1


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.ERROR)
    sys.exit(main())
