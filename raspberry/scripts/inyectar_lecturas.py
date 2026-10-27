"""Inyector de lecturas sintéticas para QA de RF-55 (sin hardware ni broker).

Arma el agente real con una fuente guionada (sobre `FakeLoraSource`, que el
agente no distingue de la radio), le envía los umbrales y reglas por el mismo
camino que el broker (comandos en el topic `command`), inyecta las lecturas de
cada caso y compara los eventos publicados en `sgpmp/<serial>/evento` con los
esperados.

Uso:
    python scripts/inyectar_lecturas.py scripts/casos_rf55.json
    python scripts/inyectar_lecturas.py scripts/casos_rf55.json --caso "THI moderado" -v

Formato del archivo de casos (JSON; contrato en docs/CONTRATO_EVENTO_EDGE.md):
    {
      "umbrales": [ <comando UMBRAL_AMBIENTAL sin tipo_comando> ],
      "reglas":   [ {"id_regla_alerta": 12, "version_regla": 1, "regla": {...}} ],
      "casos": [{
        "nombre": "...",
        "umbrales": [...], "reglas": [...],         # opcional: reemplazan a los globales
        "lecturas": [{"t": 0, "variable": "temperatura_ambiente", "valor": 32.5,
                      "unidad": "C", "serial": "TC-...", "sensor": "dht22"}],
        "esperado": [{"clasificacion_rf55": "...", "severidad": "...",
                      "regla_aplicada": "...", "primera_variable": "...",
                      "valores_derivados": {"THI": 81.3}}],
        "acks_error": 0                              # opcional: rechazos esperados
      }]
    }
`t` son segundos relativos al inicio del caso; las lecturas con el mismo `t`
llegan en la misma captura. Cada caso arranca con estado limpio. Sale con 1
si algún caso no produce exactamente los eventos esperados.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge_agent.agent import Agent  # noqa: E402
from edge_agent.buffer import Buffer  # noqa: E402
from edge_agent.config import Settings  # noqa: E402
from edge_agent.config_store import ConfigStore, DeviceConfig, UmbralStore  # noqa: E402
from edge_agent.mqtt_client import Message, PubAck  # noqa: E402
from edge_agent.reglas.store import ReglaStore  # noqa: E402
from edge_agent.sources import FakeLoraSource, Reading  # noqa: E402

GATEWAY = "EDGE-QA-0001"
SERIAL_DEFECTO = "TC-M09-QA-0001"
_TOLERANCIA = 0.05  # valores derivados (THI con dos decimales)


class FuenteGuionada(FakeLoraSource):
    """`FakeLoraSource` que entrega exactamente las lecturas cargadas."""

    def __init__(self) -> None:
        super().__init__(())
        self._pendientes: list[Reading] = []

    def cargar(self, lecturas: Sequence[Reading]) -> None:
        self._pendientes = list(lecturas)

    def read(self, serial: str, now: datetime) -> list[Reading]:
        lecturas, self._pendientes = self._pendientes, []
        return lecturas


class LinkMemoria:
    connected = True

    def __init__(self) -> None:
        self.publicados: list[tuple[str, dict[str, Any]]] = []
        self.sin_puback: list[int] = []
        self._mid = 0

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> int | None:
        self.publicados.append((topic, json.loads(payload)))
        self._mid += 1
        self.sin_puback.append(self._mid)
        return self._mid

    def confirmar(self, agente: Agent, ahora: float) -> None:
        """PUBACK de todo lo publicado: sin él, el buffer lo reenviaría."""
        mids, self.sin_puback = self.sin_puback, []
        for mid in mids:
            agente.handle_event(PubAck(mid), ahora)

    def sufijo(self, sufijo: str) -> list[dict[str, Any]]:
        return [p for t, p in self.publicados if t.endswith("/" + sufijo)]


@dataclass
class Resultado:
    nombre: str
    eventos: list[dict[str, Any]]
    acks_error: list[dict[str, Any]]
    fallas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fallas


def ejecutar(casos: dict[str, Any], *, solo: str | None = None) -> list[Resultado]:
    resultados = []
    for caso in casos["casos"]:
        if solo is not None and caso["nombre"] != solo:
            continue
        resultados.append(
            _ejecutar_caso(
                caso,
                caso.get("umbrales", casos.get("umbrales", [])),
                caso.get("reglas", casos.get("reglas", [])),
            )
        )
    return resultados


def _ejecutar_caso(caso: dict[str, Any], umbrales: list, reglas: list) -> Resultado:
    with tempfile.TemporaryDirectory(prefix="rf55-qa-") as tmp:
        estado = Path(tmp)
        settings = _settings(estado)
        link = LinkMemoria()
        buffer = Buffer(settings.buffer_path, settings.buffer_max_rows)
        fuente = FuenteGuionada()
        inicio = time.time()
        agente = Agent(
            settings,
            link,
            ConfigStore(settings.state_path, DeviceConfig(1, 1)),
            buffer,
            fuente,
            umbrales=UmbralStore(estado / "umbrales.json"),
            reglas=ReglaStore(estado / "reglas.json"),
            reloj_sincronizado=lambda: False,  # sin juzgar antigüedad de comandos
            clock=lambda: inicio,
        )
        try:
            for i, umbral in enumerate(umbrales):
                _comando(agente, {**umbral, "tipo_comando": "UMBRAL_AMBIENTAL"}, f"u-{i}", inicio)
            for i, regla in enumerate(reglas):
                comando = {"es_activa": True, **regla, "tipo_comando": "REGLA_COMPUESTA"}
                _comando(agente, comando, f"r-{i}", inicio)
            link.confirmar(agente, inicio)

            lecturas = sorted(caso["lecturas"], key=lambda lec: lec.get("t", 0))
            for t, grupo in groupby(lecturas, key=lambda lec: lec.get("t", 0)):
                ahora = inicio + t
                fuente.cargar([_reading(lec, ahora) for lec in grupo])
                agente.capturar_ahora(GATEWAY, ahora)
                link.confirmar(agente, ahora)
        finally:
            buffer.close()

    acks_error = [a for a in link.sufijo("status") if a.get("resultado") != "OK"]
    resultado = Resultado(caso["nombre"], link.sufijo("evento"), acks_error)
    resultado.fallas = _comparar(resultado.eventos, caso.get("esperado", []))
    # Un caso puede esperar rechazos (ej. la sexta regla activa): `acks_error`.
    if len(acks_error) != caso.get("acks_error", 0):
        resultado.fallas += [f"ACK de error al configurar: {a.get('motivo')}" for a in acks_error]
        resultado.fallas.append(f"se esperaban {caso.get('acks_error', 0)} ACK de error")
    return resultado


def _comparar(eventos: list[dict[str, Any]], esperados: list[dict[str, Any]]) -> list[str]:
    if len(eventos) != len(esperados):
        return [f"se esperaban {len(esperados)} eventos y salieron {len(eventos)}"]
    fallas = []
    for i, (evento, esperado) in enumerate(zip(eventos, esperados, strict=True)):
        meta = evento["metadata_edge"]
        obtenido = {
            "clasificacion_rf55": evento["clasificacion_rf55"],
            "severidad": evento.get("severidad"),
            "regla_aplicada": meta.get("regla_aplicada"),
            "primera_variable": evento["variables_involucradas"][0]["tipo_variable"],
        }
        for clave, valor in esperado.items():
            if clave == "valores_derivados":
                derivados = meta.get("valores_derivados", {})
                for nombre, x in valor.items():
                    if abs(derivados.get(nombre, float("nan")) - x) > _TOLERANCIA:
                        fallas.append(
                            f"evento {i}: {nombre}={derivados.get(nombre)} (esperado {x})"
                        )
            elif obtenido.get(clave) != valor:
                fallas.append(f"evento {i}: {clave}={obtenido.get(clave)!r} (esperado {valor!r})")
    return fallas


def _comando(agente: Agent, data: dict[str, Any], id_comando: str, ahora: float) -> None:
    payload = json.dumps({"id_comando": id_comando, **data}).encode()
    agente.handle_event(Message(f"sgpmp/{GATEWAY}/command", payload), ahora)


def _reading(lectura: dict[str, Any], ahora: float) -> Reading:
    return Reading(
        serial=lectura.get("serial", SERIAL_DEFECTO),
        variable=lectura["variable"],
        valor_crudo=float(lectura["valor"]),
        unidad=lectura.get("unidad", ""),
        timestamp_captura=datetime.fromtimestamp(ahora, UTC),
        sensor=lectura.get("sensor"),
    )


def _settings(estado: Path) -> Settings:
    return Settings(
        mqtt_host="qa.invalid",
        mqtt_port=1883,
        mqtt_username="qa",
        mqtt_password="qa",
        mqtt_client_id="edge-qa",
        mqtt_ca_cert=None,
        mqtt_keepalive_s=60,
        topic_prefix="sgpmp",
        topic_telemetry="telemetry",
        topic_heartbeat="heartbeat",
        topic_status="status",
        topic_command="command",
        serials=(GATEWAY,),
        heartbeat_interval_s=300,
        default_frecuencia_captura_min=1,
        default_intervalo_transmision_min=1,
        buffer_path=estado / "buffer.db",
        buffer_max_rows=10_000,
        state_path=estado / "config.json",
        source="fake",
        fake_variables=(),
        firmware_version="qa",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("casos", type=Path, help="archivo JSON de casos")
    parser.add_argument("--caso", help="ejecutar solo el caso con este nombre")
    parser.add_argument("-v", "--verbose", action="store_true", help="imprimir cada evento")
    args = parser.parse_args(argv)

    resultados = ejecutar(json.loads(args.casos.read_text(encoding="utf-8")), solo=args.caso)
    for r in resultados:
        print(f"[{'OK   ' if r.ok else 'FALLA'}] {r.nombre}: {len(r.eventos)} evento(s)")
        for falla in r.fallas:
            print(f"        - {falla}")
        if args.verbose or not r.ok:
            for evento in r.eventos:
                print(json.dumps(evento, ensure_ascii=False, indent=2))
    fallidos = sum(not r.ok for r in resultados)
    print(f"\n{len(resultados) - fallidos}/{len(resultados)} casos OK")
    return 1 if fallidos or not resultados else 0


if __name__ == "__main__":
    sys.exit(main())
