"""RF-55.2: reglas compuestas (Tipo A ventana de confirmación, Tipo B fórmula THI)."""

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SERIAL, T0, Harness

from edge_agent.commands import handle_regla
from edge_agent.config import ConfigError, Settings
from edge_agent.config_store import Umbral
from edge_agent.mqtt_client import Message
from edge_agent.reglas import formulas
from edge_agent.reglas.evaluador import evaluar_lote, validar_regla
from edge_agent.reglas.evento import a_payload
from edge_agent.reglas.store import Regla
from edge_agent.reglas.ventana import EstadoCompuestas, Ventana
from edge_agent.sources import Reading

AHORA = datetime(2026, 10, 26, 14, 30, 0, tzinfo=UTC)
THI = {
    "nombre": "Estres termico por THI",
    "tipo_sensor": "TEMPERATURA",
    "categoria_variable": "AMBIENTAL",
    "prevalece_sobre_regla_simple": True,
}
VENTANA_A = {
    "nombre": "Temperatura alta sostenida",
    "tipo_sensor": "TEMPERATURA",
    "categoria_variable": "AMBIENTAL",
    "umbral_min": "18.0",
    "umbral_max": "30.0",
    "ventana_confirmacion_min": "5",
    "lectura_confirmacion_max": "3",
    "prevalece_sobre_regla_simple": True,
}


def lectura(variable, valor, unidad="C", segundos=0.0, serial="TC-M09-XXXX-0001") -> Reading:
    return Reading(
        serial=serial,
        variable=variable,
        valor_crudo=valor,
        unidad=unidad,
        timestamp_captura=AHORA + timedelta(seconds=segundos),
        sensor="dht22_ambiente",
    )


def ambiente(t=32.5, hr=78.0, segundos=0.0, unidad="C") -> list[Reading]:
    return [
        lectura("temperatura_ambiente", t, unidad, segundos),
        lectura("humedad_relativa", hr, "%", segundos + 2),
    ]


def regla(cuerpo=THI, id_regla=12, version=3) -> Regla:
    return Regla(id_regla_alerta=id_regla, version_regla=version, regla=cuerpo)


def umbral(variable, niveles, id_umbral=7) -> Umbral:
    return Umbral(
        id_umbral,
        None,
        variable,
        "C",
        niveles[0][1],
        niveles[-1][2],
        tuple({"nivel": n, "limite_inferior": i, "limite_superior": s} for n, i, s in niveles),
    )


# La temperatura ambiente de 32.5 °C cae en `critico` con este umbral simple.
UMBRAL_T = umbral(
    "temperatura_ambiente", [("normal", 10, 25), ("precaucion", 25, 30), ("critico", 30, 35)]
)
UMBRAL_FR = umbral(
    "frecuencia_respiratoria", [("normal", 10, 40), ("precaucion", 40, 60), ("critico", 60, 80)], 8
)


def evaluar(lecturas, reglas=(), umbrales=(), estado=None, ahora=AHORA, variables=None):
    return evaluar_lote(
        lecturas,
        list(umbrales),
        list(reglas),
        estado=EstadoCompuestas() if estado is None else estado,
        variables=variables or {},
        ahora=ahora,
        version_firmware="v0.3.0",
    )


def compuestas(eventos):
    return [e for e in eventos if e.clasificacion == "DESVIACION_COMPUESTA"]


# --- Tipo B: ESTRES_TERMICO --------------------------------------------------


def test_vector_thi_de_referencia_es_moderado():
    (evento,) = evaluar(ambiente(32.5, 78.0), [regla()])

    assert (evento.clasificacion, evento.severidad) == ("DESVIACION_COMPUESTA", "MODERADO")
    assert evento.metadata["valores_derivados"]["THI"] == pytest.approx(81.3, abs=0.01)
    payload = a_payload(evento)
    meta = payload["metadata_edge"]
    assert meta["regla_aplicada"] == "ESTRES_TERMICO"
    assert meta["nombre_regla"] == "Estres termico por THI"
    assert (meta["id_regla_alerta"], meta["version_regla"]) == (12, 3)
    assert meta["ventana_temporal_ms"] == 2000
    assert [v["tipo_variable"] for v in payload["variables_involucradas"]] == [
        "temperatura_ambiente",
        "humedad_relativa",
    ]


@pytest.mark.parametrize(
    ("thi", "severidad"),
    [
        (71.99, None),
        (72.0, "LEVE"),
        (78.99, "LEVE"),
        (79.0, "MODERADO"),
        (83.99, "MODERADO"),
        (84.0, "CRITICO"),
        (95.0, "CRITICO"),
    ],
)
def test_cada_corte_cae_en_una_sola_banda(thi, severidad):
    # Con HR=100 % el THI es exactamente la temperatura en °F.
    eventos = compuestas(evaluar(ambiente(thi, 100.0, unidad="F"), [regla()]))
    assert (eventos[0].severidad if eventos else None) == severidad


def test_umbrales_severidad_parametriza_los_cortes():
    cuerpo = {**THI, "umbrales_severidad": {"LEVE": "70", "MODERADO": 75, "CRITICO": "80.5"}}
    (evento,) = evaluar(ambiente(32.5, 78.0), [regla(cuerpo)])  # THI 81.3
    assert evento.severidad == "CRITICO"


@pytest.mark.parametrize(
    ("t_f", "extras", "esperada"),
    [
        (75.0, [("temperatura_corporal", 40.5, "C")], "MODERADO"),  # LEVE → MODERADO
        (80.0, [("temperatura_corporal", 41.0, "C")], "CRITICO"),  # MODERADO → CRITICO
        (90.0, [("temperatura_corporal", 41.0, "C")], "CRITICO"),  # CRITICO es tope
        (75.0, [("frecuencia_respiratoria", 50.0, "rpm")], "MODERADO"),
        # Ambas condiciones: sube exactamente un nivel, no dos.
        (
            75.0,
            [("temperatura_corporal", 41.0, "C"), ("frecuencia_respiratoria", 50.0, "rpm")],
            "MODERADO",
        ),
        (75.0, [("temperatura_corporal", 40.0, "C")], "LEVE"),  # > 40, no ≥ 40
        (75.0, [("frecuencia_respiratoria", 30.0, "rpm")], "LEVE"),  # FR en rango normal
        (70.0, [("temperatura_corporal", 41.0, "C")], None),  # sin THI no hay escalada
    ],
)
def test_escalada(t_f, extras, esperada):
    lecturas = ambiente(t_f, 100.0, unidad="F")
    lecturas += [lectura(v, x, u, 1, serial="COLLAR-01") for v, x, u in extras]
    eventos = compuestas(evaluar(lecturas, [regla()], [UMBRAL_FR]))
    assert (eventos[0].severidad if eventos else None) == esperada
    if eventos:
        # Las que escalaron se agregan detrás de las de la fórmula.
        nombres = [r.variable for r in eventos[0].lecturas]
        assert nombres[:2] == ["temperatura_ambiente", "humedad_relativa"]
        assert set(nombres[2:]) <= {v for v, _, _ in extras}
        if esperada == "LEVE":  # no escaló: no se listan
            assert nombres[2:] == []


def test_variable_faltante_no_dispara_ni_es_error():
    eventos = evaluar([lectura("temperatura_ambiente", 40.0)], [regla()])
    assert eventos == []


def test_lectura_fuera_de_la_ventana_de_30_s_no_correlaciona():
    estado = EstadoCompuestas()
    evaluar([lectura("temperatura_ambiente", 32.5)], [regla()], estado=estado)

    tarde = AHORA + timedelta(seconds=31)
    hr = [lectura("humedad_relativa", 78.0, "%", 31)]
    assert evaluar(hr, [regla()], estado=estado, ahora=tarde) == []

    estado = EstadoCompuestas()
    evaluar([lectura("temperatura_ambiente", 32.5)], [regla()], estado=estado)
    a_tiempo = AHORA + timedelta(seconds=29)
    hr = [lectura("humedad_relativa", 78.0, "%", 29)]
    (evento,) = evaluar(hr, [regla()], estado=estado, ahora=a_tiempo)
    # La variable que disparó (llegó ahora) va primera.
    assert [r.variable for r in evento.lecturas] == ["humedad_relativa", "temperatura_ambiente"]
    assert evento.metadata["ventana_temporal_ms"] == 29000


def test_ventana_correlacion_configurable_por_regla():
    estado = EstadoCompuestas()
    cuerpo = {**THI, "ventana_correlacion_s": 5}
    evaluar([lectura("temperatura_ambiente", 32.5)], [regla(cuerpo)], estado=estado)
    hr = [lectura("humedad_relativa", 78.0, "%", 10)]
    assert evaluar(hr, [regla(cuerpo)], estado=estado, ahora=AHORA + timedelta(seconds=10)) == []


def test_compuesta_que_dispara_prevalece_sobre_el_simple():
    eventos = evaluar(ambiente(), [regla()], [UMBRAL_T])
    assert [e.clasificacion for e in eventos] == ["DESVIACION_COMPUESTA"]

    sin_prevalencia = {**THI, "prevalece_sobre_regla_simple": False}
    eventos = evaluar(ambiente(), [regla(sin_prevalencia)], [UMBRAL_T])
    assert sorted(e.clasificacion for e in eventos) == ["DESVIACION_COMPUESTA", "DESVIACION_SIMPLE"]


def test_compuesta_que_no_dispara_deja_pasar_el_simple():
    t = umbral(
        "temperatura_ambiente", [("normal", 0, 10), ("precaucion", 10, 15), ("critico", 15, 18)]
    )
    eventos = evaluar(ambiente(20.0, 30.0), [regla()], [t])  # THI bajo, T fuera de rango
    assert [e.clasificacion for e in eventos] == ["DESVIACION_SIMPLE"]


def test_mapa_de_variables_configurable():
    lecturas = [lectura("temp_aire", 32.5), lectura("hr_aire", 78.0, "%", 1)]
    assert evaluar(lecturas, [regla()]) == []
    mapa = {"TEMPERATURA_AMBIENTAL": "temp_aire", "HUMEDAD_RELATIVA": "hr_aire"}
    (evento,) = evaluar(lecturas, [regla()], variables=mapa)
    assert evento.severidad == "MODERADO"


def test_una_evaluacion_por_nodo_y_captura():
    lecturas = ambiente() + [lectura("temperatura_ambiente", 32.6, segundos=3)]
    assert len(evaluar(lecturas, [regla()])) == 1


# --- Tipo A: ventana de confirmación ------------------------------------------


def test_nombre_fuera_del_catalogo_se_evalua_como_tipo_a():
    assert validar_regla(VENTANA_A) is None
    (evento,) = evaluar(
        [lectura("temperatura_ambiente", 31.0)],
        [regla({**VENTANA_A, "lectura_confirmacion_max": 1})],
    )
    assert evento.clasificacion == "DESVIACION_COMPUESTA"
    assert evento.metadata["regla_aplicada"] == "VENTANA_CONFIRMACION"


def test_tipo_a_confirma_tras_n_repeticiones_dentro_de_la_ventana():
    estado = EstadoCompuestas()
    resultados = []
    for minuto in range(4):
        ahora = AHORA + timedelta(minutes=minuto)
        lecturas = [lectura("temperatura_ambiente", 31.0, segundos=minuto * 60)]
        resultados.append(
            compuestas(evaluar(lecturas, [regla(VENTANA_A)], estado=estado, ahora=ahora))
        )
    assert [len(r) for r in resultados] == [0, 0, 1, 0]  # confirma con la 3.ª y reinicia
    assert resultados[2][0].severidad == "MODERADO"


def test_tipo_a_no_confirma_si_las_repeticiones_se_salen_de_la_ventana():
    estado = EstadoCompuestas()
    disparos = 0
    for minuto in (0, 3, 6, 9):  # nunca 3 dentro de 5 min
        ahora = AHORA + timedelta(minutes=minuto)
        lecturas = [lectura("temperatura_ambiente", 31.0, segundos=minuto * 60)]
        disparos += len(
            compuestas(evaluar(lecturas, [regla(VENTANA_A)], estado=estado, ahora=ahora))
        )
    assert disparos == 0


def test_tipo_a_en_rango_no_cuenta():
    estado = EstadoCompuestas()
    for minuto in range(5):
        lecturas = [lectura("temperatura_ambiente", 25.0, segundos=minuto * 60)]
        ahora = AHORA + timedelta(minutes=minuto)
        assert evaluar(lecturas, [regla(VENTANA_A)], estado=estado, ahora=ahora) == []


def test_tipo_a_ventana_cero_con_una_lectura_es_sin_ventana():
    # Así llega del CRUD del backend una regla sin ventana (columna NOT NULL).
    cuerpo = {
        **VENTANA_A,
        "ventana_confirmacion_min": "0.0000",
        "lectura_confirmacion_max": "1.0000",
    }
    assert validar_regla(cuerpo) is None
    assert handle_regla(comando(cuerpo), None).ack["resultado"] == "OK"
    (evento,) = evaluar([lectura("temperatura_ambiente", 31.0)], [regla(cuerpo)])
    assert evento.clasificacion == "DESVIACION_COMPUESTA"
    assert evento.metadata["ventana_confirmacion_min"] is None


def test_tipo_a_severidad_desde_umbrales_severidad():
    cuerpo = {
        **VENTANA_A,
        "lectura_confirmacion_max": 1,
        "umbrales_severidad": {"severidad": "CRITICO"},
    }
    (evento,) = evaluar([lectura("temperatura_ambiente", 10.0)], [regla(cuerpo)])
    assert evento.severidad == "CRITICO"


# --- Límites y reglas mal formadas --------------------------------------------


def comando(cuerpo, **overrides) -> dict:
    return {
        "id_comando": "r-1",
        "tipo_comando": "REGLA_COMPUESTA",
        "id_regla_alerta": 12,
        "version_regla": 1,
        "es_activa": True,
        "regla": cuerpo,
        **overrides,
    }


def test_sexta_regla_activa_se_rechaza():
    resultado = handle_regla(comando(THI), None, activas=5)
    assert resultado.regla is None
    assert resultado.ack["resultado"] == "ERROR"
    assert "5 reglas" in resultado.ack["motivo"]
    # Actualizar una de las 5 vigentes, o retirar una, sí se acepta.
    assert handle_regla(comando(THI, version_regla=4), regla(), activas=5).regla is not None
    assert handle_regla(comando(THI, es_activa=False), None, activas=5).regla is not None


def test_formula_con_4_variables_se_rechaza(monkeypatch):
    cuatro = formulas.Formula(
        codigo="CUATRO",
        alias=frozenset(),
        variables=("A", "B", "C", "D"),
        opcionales=(),
        parametros=lambda _: {},
        evaluar=lambda *_: (None, {}, ()),
    )
    monkeypatch.setitem(formulas.FORMULAS, "CUATRO", cuatro)
    resultado = handle_regla(comando({"nombre": "cuatro"}), None)
    assert resultado.ack["resultado"] == "ERROR"
    assert "máximo 3" in resultado.ack["motivo"]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {**THI, "ventana_correlacion_s": 31},
        {**THI, "ventana_correlacion_s": 0},
        {**THI, "umbrales_severidad": {"LEVE": 80, "MODERADO": 79}},
        {**THI, "umbrales_severidad": {"LEVE": "setenta"}},
        {**THI, "umbrales_severidad": ["no", "dict"]},
        {"nombre": "x", "tipo_sensor": ""},
        {"nombre": "x", "tipo_sensor": "TEMPERATURA"},  # sin umbrales
        {**VENTANA_A, "umbral_min": "40", "umbral_max": "30"},
        {**VENTANA_A, "lectura_confirmacion_max": "2.5"},
        {**VENTANA_A, "lectura_confirmacion_max": 0},
        {**VENTANA_A, "lectura_confirmacion_max": 1000},
        {**VENTANA_A, "ventana_confirmacion_min": None},
        {**VENTANA_A, "ventana_confirmacion_min": "0.0000"},  # 0 con 3 lecturas
        {**VENTANA_A, "ventana_confirmacion_min": "-5"},
        {**VENTANA_A, "umbrales_severidad": {"severidad": "GRAVE"}},
        {**VENTANA_A, "umbral_max": True},
    ],
)
def test_regla_mal_formada_se_rechaza_al_recibir(cuerpo):
    resultado = handle_regla(comando(cuerpo), None)
    assert (resultado.ack["tipo_mensaje"], resultado.ack["resultado"]) == ("ACK_REGLA", "ERROR")
    assert resultado.ack["motivo"].startswith("regla inválida")


def test_regla_guardada_mal_formada_es_error_de_configuracion_sin_excepcion():
    # Ej. guardada por otra versión del firmware con otras validaciones.
    rota = regla({"nombre": "x", "tipo_sensor": "TEMPERATURA", "umbral_min": "abc"})
    (evento,) = evaluar(ambiente(), [rota])
    assert evento.clasificacion == "ERROR_CONFIGURACION"
    assert evento.metadata["id_regla_alerta"] == 12
    assert "umbral_min" in evento.metadata["motivo"]


def test_excepcion_dentro_de_una_formula_es_error_de_configuracion(monkeypatch):
    def explota(*_):
        raise ZeroDivisionError("bug")

    rota = formulas.Formula(
        "ROTA", frozenset(), formulas.ESTRES_TERMICO.variables, (), lambda _: {}, explota
    )
    monkeypatch.setitem(formulas.FORMULAS, "ROTA", rota)
    eventos = evaluar(ambiente(), [regla({"nombre": "rota"})])
    assert [e.clasificacion for e in eventos] == ["ERROR_CONFIGURACION"]


def test_rf55_2_deshabilitado_solo_evalua_simples():
    eventos = evaluar_lote(
        ambiente(),
        [UMBRAL_T],
        [regla()],
        estado=None,
        variables={},
        ahora=AHORA,
        version_firmware="v",
    )
    assert [e.clasificacion for e in eventos] == ["DESVIACION_SIMPLE"]


# --- Ventana acotada y latencia -------------------------------------------------


def test_la_ventana_purga_y_no_crece_sin_limite():
    ventana = Ventana()
    for i in range(10_000):
        ahora = AHORA + timedelta(seconds=i)
        ventana.agregar(lectura(f"v{i % 7}", 1.0, segundos=i, serial=f"S{i % 3}"), ahora)
    assert len(ventana) <= 7 * 31  # solo lo de los últimos 30 s


def test_latencia_adicional_rf55_2_bajo_200_ms():
    estado = EstadoCompuestas()
    reglas = [regla(THI, 1), regla(VENTANA_A, 2), regla({**THI, "nombre": "THI"}, 3)]
    peor = 0.0
    for i in range(300):
        ahora = AHORA + timedelta(seconds=i)
        lecturas = ambiente(30 + i % 5, 70.0, segundos=i)
        inicio = time.perf_counter()
        evaluar(lecturas, reglas, [UMBRAL_T, UMBRAL_FR], estado=estado, ahora=ahora)
        peor = max(peor, time.perf_counter() - inicio)
    assert peor < 0.2


# --- Integración con el agente ----------------------------------------------


class AmbienteSource:
    def read(self, serial, now):
        return [
            Reading(serial, "temperatura_ambiente", 32.5, "C", now, sensor="dht22"),
            Reading(serial, "humedad_relativa", 78.0, "%", now, sensor="dht22"),
        ]

    def is_alive(self, serial, now, max_silence_s):
        return True

    def status(self, serial):
        return {}

    def apply_config(self, serial, config):
        pass


def test_agente_recibe_la_regla_y_publica_el_evento_compuesto(tmp_path):
    from edge_agent.agent import Agent

    h = Harness(tmp_path)
    h.agent = Agent(
        h.settings,
        h.link,
        h.store,
        h.buffer,
        AmbienteSource(),
        umbrales=h.umbrales,
        reglas=h.reglas,
        reloj_sincronizado=lambda: True,
        clock=lambda: h.now,
    )
    emitido = datetime.fromtimestamp(T0, UTC).isoformat()
    cmd = comando(THI, emitido_en=emitido)
    h.agent.handle_event(Message(f"sgpmp/{SERIAL}/command", json.dumps(cmd).encode()), h.now)
    assert h.link.on("status")[-1]["tipo_mensaje"] == "ACK_REGLA"

    h.advance(0)
    (evento,) = h.link.on("evento")
    assert evento["clasificacion_rf55"] == "DESVIACION_COMPUESTA"
    assert evento["severidad"] == "MODERADO"
    assert evento["metadata_edge"]["valores_derivados"]["THI"] == pytest.approx(81.3, abs=0.01)
    h.buffer.close()


def test_mapa_de_variables_desde_el_entorno():
    base = {
        "EDGE_SERIALS": "S",
        "EDGE_MQTT_HOST": "h",
        "EDGE_MQTT_USERNAME": "u",
        "EDGE_MQTT_PASSWORD": "p",
        "EDGE_HEARTBEAT_INTERVAL_S": "60",
        "EDGE_FRECUENCIA_CAPTURA_MIN": "1",
        "EDGE_INTERVALO_TRANSMISION_MIN": "1",
    }
    assert Settings.from_env(base).rf55_variables == {}
    env = {**base, "EDGE_RF55_VARIABLES": "temperatura_ambiental=temp_aire;HUMEDAD_RELATIVA=hr"}
    assert Settings.from_env(env).rf55_variables == {
        "TEMPERATURA_AMBIENTAL": "temp_aire",
        "HUMEDAD_RELATIVA": "hr",
    }
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "EDGE_RF55_VARIABLES": "sin_igual"})
