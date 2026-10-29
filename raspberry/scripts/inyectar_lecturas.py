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
        "umbrales_guardados": [...],                 # opcional: ya en umbrales.json
        "lecturas": [{"t": 0, "variable": "temperatura_ambiente", "valor": 32.5,
                      "unidad": "C", "serial": "TC-...", "sensor": "dht22"}],
        "esperado": [{"clasificacion_rf55": "...", "severidad": "...",
                      "regla_aplicada": "...", "primera_variable": "...",
                      "severidad_origen": "RF-55.1",   # y severidad_regla/_rf55_1
                      "valores_derivados": {"THI": 86.57}}],
        "acks_error": 0,                             # opcional: rechazos esperados
        "esperado_sin_umbral": ["humedad_relativa"], # variables sin umbral RF-17
        "esperado_buffer": [{"evento": 2, "telemetry": 3}],   # opcional (RF-54)
        "esperado_historial": [{"clasificacion": "NORMAL", "ruta": "LOCAL"}]  # opcional
      }]
    }
`t` son segundos relativos al inicio del caso; las lecturas con el mismo `t`
llegan en la misma captura.

Control de conectividad e inspección (TC-M03-098/099/100), como entradas de
`lecturas`:
    {"t": 0, "conectividad": false}        # se aplica antes de las lecturas de ese t
    {"t": 5, "inspeccionar_buffer": true}  # foto del buffer tras esa captura
    {"t": 0, "fallo_rf55_2": true}         # RF-55.2 falla en esa captura (TC-M03-088/097)
Antes de cada reconexión se toma una foto del buffer automáticamente. Cada foto
cuenta las filas por tipo y se compara, en orden, con `esperado_buffer`; los
eventos de cada foto deben estar en orden cronológico. `esperado_historial` se
compara, en orden y por las claves dadas, con el historial local al terminar.
Tras cada captura se anota el estado de RF-55.2 (`Agent.estado_rf55_2`);
`esperado_rf55_2` lo compara por `t`: [{"t": 0, "estado": "ACTIVO",
"fallos_consecutivos": 1, "operativo": true}]. La falla se inyecta solo acá, en
el simulador: el firmware no tiene ningún interruptor para fallar a propósito.

Una variable sin umbral RF-17 da un ERROR_CONFIGURACION (TC-M03-083) que no
entra en `esperado`: se compara aparte, como conjunto de variables, con
`esperado_sin_umbral` (vacío si falta).

`umbrales_guardados` escribe umbrales directo en `umbrales.json` antes de
arrancar, sin pasar por la validación del ACK: así se reproduce un umbral que
dejó guardado un firmware anterior (TC-M03-095/122).

Cada caso arranca con estado limpio. Sale con 1 si algún caso no produce
exactamente los eventos esperados.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
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
from edge_agent.historial import Historial  # noqa: E402
from edge_agent.inspeccionar import abrir_solo_lectura, filas_buffer  # noqa: E402
from edge_agent.mqtt_client import Connected, Message, PubAck  # noqa: E402
from edge_agent.reglas import evaluador  # noqa: E402
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
    def __init__(self) -> None:
        self.connected = True
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
    fotos_buffer: list[list[dict[str, Any]]] = field(default_factory=list)
    estados_rf55_2: list[dict[str, Any]] = field(default_factory=list)
    historial: list[dict[str, Any]] = field(default_factory=list)

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
        historial = Historial(settings.historial_path, settings.historial_max_filas)
        fotos: list[list[dict[str, Any]]] = []
        estados: list[dict[str, Any]] = []
        fuente = FuenteGuionada()
        inicio = time.time()
        if caso.get("umbrales_guardados"):
            guardados = {str(u["id_umbral_ambiental"]): u for u in caso["umbrales_guardados"]}
            (estado / "umbrales.json").write_text(
                json.dumps({"umbrales": guardados}), encoding="utf-8"
            )
        agente = Agent(
            settings,
            link,
            ConfigStore(settings.state_path, DeviceConfig(1, 1)),
            buffer,
            fuente,
            umbrales=UmbralStore(estado / "umbrales.json"),
            reglas=ReglaStore(estado / "reglas.json"),
            historial=historial,
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
            for t, grupo_iter in groupby(lecturas, key=lambda lec: lec.get("t", 0)):
                ahora = inicio + t
                grupo = list(grupo_iter)
                for control in (e for e in grupo if "conectividad" in e):
                    if control["conectividad"] and not link.connected:
                        fotos.append(_foto(settings.buffer_path))
                        link.connected = True
                        agente.handle_event(Connected(session_present=True), ahora)
                        link.confirmar(agente, ahora)
                    elif not control["conectividad"]:
                        link.connected = False
                medidas = [e for e in grupo if "variable" in e]
                if medidas:
                    fuente.cargar([_reading(lec, ahora) for lec in medidas])
                    if any(e.get("fallo_rf55_2") for e in grupo):
                        with _rf55_2_falla():
                            agente.capturar_ahora(GATEWAY, ahora)
                    else:
                        agente.capturar_ahora(GATEWAY, ahora)
                    estados.append({"t": t, **agente.estado_rf55_2})
                if link.connected:
                    link.confirmar(agente, ahora)
                if any(e.get("inspeccionar_buffer") for e in grupo):
                    fotos.append(_foto(settings.buffer_path))
            registros = historial.recientes(settings.historial_max_filas)
        finally:
            buffer.close()
            historial.close()

    acks_error = [a for a in link.sufijo("status") if a.get("resultado") != "OK"]
    resultado = Resultado(caso["nombre"], link.sufijo("evento"), acks_error)
    resultado.fotos_buffer, resultado.historial = fotos, registros
    resultado.estados_rf55_2 = estados
    sin_umbral = sorted({_variable_sin_umbral(e) for e in resultado.eventos} - {None})
    eventos = [e for e in resultado.eventos if _variable_sin_umbral(e) is None]
    resultado.fallas = _comparar(eventos, caso.get("esperado", []))
    if sin_umbral != sorted(caso.get("esperado_sin_umbral", [])):
        resultado.fallas.append(
            f"variables sin umbral {sin_umbral} (esperado {caso.get('esperado_sin_umbral', [])})"
        )
    resultado.fallas += trazabilidad(resultado.eventos)
    resultado.fallas += _comparar_buffer(fotos, caso.get("esperado_buffer"))
    resultado.fallas += _comparar_historial(registros, caso.get("esperado_historial"))
    resultado.fallas += _comparar_rf55_2(estados, caso.get("esperado_rf55_2"))
    # Un caso puede esperar rechazos (ej. la sexta regla activa): `acks_error`.
    if len(acks_error) != caso.get("acks_error", 0):
        resultado.fallas += [f"ACK de error al configurar: {a.get('motivo')}" for a in acks_error]
        resultado.fallas.append(f"se esperaban {caso.get('acks_error', 0)} ACK de error")
    return resultado


def _variable_sin_umbral(evento: dict[str, Any]) -> str | None:
    meta = evento.get("metadata_edge", {})
    return meta.get("variable") if meta.get("motivo") == evaluador.SIN_UMBRAL else None


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
            # Máxima severidad de una compuesta que prevalece (TC-M03-089).
            "severidad_regla": meta.get("severidad_regla"),
            "severidad_rf55_1": meta.get("severidad_rf55_1"),
            "severidad_origen": meta.get("severidad_origen"),
            "primera_variable": evento["variables_involucradas"][0]["tipo_variable"],
            "origen": evento.get("origen"),
            "estado_conectividad": evento.get("estado_conectividad"),
            # Umbral inutilizable (TC-M03-095/122).
            "motivo": meta.get("motivo"),
            "severidad_invalida": meta.get("severidad_invalida"),
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


def trazabilidad(eventos: list[dict[str, Any]]) -> list[str]:
    """TC-M03-199: todo DESVIACION_COMPUESTA publicado identifica su regla.

    Se exige siempre, en todos los casos, sin que el caso lo declare.
    """
    fallas = []
    for i, evento in enumerate(eventos):
        if evento["clasificacion_rf55"] != "DESVIACION_COMPUESTA":
            continue
        meta = evento.get("metadata_edge") or {}
        for campo in ("regla_aplicada", "nombre_regla"):
            valor = meta.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                fallas.append(f"evento {i}: {campo}={valor!r} (texto no vacío obligatorio)")
        for campo in ("version_regla", "id_regla_alerta"):
            valor = meta.get(campo)
            if not isinstance(valor, int) or isinstance(valor, bool) or valor < 1:
                fallas.append(f"evento {i}: {campo}={valor!r} (entero ≥ 1 obligatorio)")
    return fallas


@contextmanager
def _rf55_2_falla() -> Iterator[None]:
    """Hace fallar la evaluación de RF-55.2 durante una captura (solo simulador)."""
    original = evaluador._evaluar_rf55_2

    def falla(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("falla de RF-55.2 inyectada por QA")

    evaluador._evaluar_rf55_2 = falla
    try:
        yield
    finally:
        evaluador._evaluar_rf55_2 = original


def _comparar_rf55_2(
    estados: list[dict[str, Any]], esperado: list[dict[str, Any]] | None
) -> list[str]:
    fallas = []
    por_t = {e["t"]: e for e in estados}
    for esperada in esperado or []:
        obtenido = por_t.get(esperada["t"])
        if obtenido is None:
            fallas.append(f"rf55_2 t={esperada['t']}: no hubo captura")
            continue
        for clave, valor in esperada.items():
            if obtenido.get(clave) != valor:
                real = obtenido.get(clave)
                fallas.append(f"rf55_2 t={esperada['t']}: {clave}={real!r} (esperado {valor!r})")
    return fallas


def _foto(buffer_path: Path) -> list[dict[str, Any]]:
    with closing(abrir_solo_lectura(buffer_path)) as db:
        return filas_buffer(db)


def _comparar_buffer(
    fotos: list[list[dict[str, Any]]], esperado: list[dict[str, int]] | None
) -> list[str]:
    fallas = []
    for i, foto in enumerate(fotos):
        capturas = [f["timestamp_captura"] for f in foto if f["tipo"] == "evento"]
        if capturas != sorted(capturas):
            fallas.append(f"buffer {i}: eventos fuera de orden cronológico {capturas}")
    if esperado is None:
        return fallas
    if len(fotos) != len(esperado):
        return [*fallas, f"se esperaban {len(esperado)} fotos del buffer y hubo {len(fotos)}"]
    for i, (foto, conteo) in enumerate(zip(fotos, esperado, strict=True)):
        for tipo, n in conteo.items():
            obtenido = sum(1 for f in foto if f["tipo"] == tipo)
            if obtenido != n:
                fallas.append(f"buffer {i}: {obtenido} fila(s) {tipo} (esperado {n})")
    return fallas


def _comparar_historial(
    registros: list[dict[str, Any]], esperado: list[dict[str, Any]] | None
) -> list[str]:
    if esperado is None:
        return []
    if len(registros) != len(esperado):
        return [f"se esperaban {len(esperado)} filas en el historial y hay {len(registros)}"]
    fallas = []
    for i, (fila, esperada) in enumerate(zip(registros, esperado, strict=True)):
        for clave, valor in esperada.items():
            if fila.get(clave) != valor:
                fallas.append(f"historial {i}: {clave}={fila.get(clave)!r} (esperado {valor!r})")
    return fallas


def _comando(agente: Agent, data: dict[str, Any], id_comando: str, ahora: float) -> None:
    payload = json.dumps({"id_comando": id_comando, **data}).encode()
    agente.handle_event(Message(f"sgpmp/{GATEWAY}/command", payload), ahora)


def _valor(valor: Any) -> Any:
    """Número como float; `null`, texto o booleano tal cual (casos de TC-M03-081)."""
    if isinstance(valor, int | float) and not isinstance(valor, bool):
        return float(valor)
    return valor


def _reading(lectura: dict[str, Any], ahora: float) -> Reading:
    return Reading(
        serial=lectura.get("serial", SERIAL_DEFECTO),
        variable=lectura["variable"],
        valor_crudo=_valor(lectura["valor"]),
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
            for i, foto in enumerate(r.fotos_buffer):
                print(f"        buffer {i} ({len(foto)} fila(s)):")
                for fila in foto:
                    print("          " + json.dumps(fila, ensure_ascii=False))
            for estado in r.estados_rf55_2:
                print("        rf55_2 " + json.dumps(estado, ensure_ascii=False))
            if r.historial:
                print(f"        historial local ({len(r.historial)} fila(s)):")
                for fila in r.historial:
                    print("          " + json.dumps(fila, ensure_ascii=False))
    fallidos = sum(not r.ok for r in resultados)
    print(f"\n{len(resultados) - fallidos}/{len(resultados)} casos OK")
    return 1 if fallidos or not resultados else 0


if __name__ == "__main__":
    sys.exit(main())
