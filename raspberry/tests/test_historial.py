"""Historial local de clasificaciones (TC-M03-100) e inspección del buffer (TC-M03-098)."""

import json
from datetime import UTC, datetime

from conftest import Harness
from test_agent import ack_all, con_umbral

from edge_agent import inspeccionar
from edge_agent.historial import Historial
from edge_agent.mqtt_client import Connected
from edge_agent.sources import Reading

# La fuente falsa mide entre 20 y 30 °C: con estos niveles todo es NORMAL.
NIVELES_NORMALES = [
    {"nivel": "normal", "limite_inferior": 0.0, "limite_superior": 40.0},
    {"nivel": "precaucion", "limite_inferior": 40.0, "limite_superior": 45.0},
    {"nivel": "critico", "limite_inferior": 45.0, "limite_superior": 50.0},
]


def test_sin_conexion_el_evento_queda_en_buffer_y_pendiente(tmp_path):
    h = Harness(tmp_path, connected=False, historial=True)
    con_umbral(h)
    h.advance(0)

    (fila,) = h.historial.recientes()
    assert fila["clasificacion"] == "DESVIACION_SIMPLE"
    assert (fila["ruta"], fila["estado"], fila["origen"]) == ("BUFFER", "PENDIENTE", None)
    assert fila["id_evento"].startswith("edge-evt-")

    h.now += 120
    h.link.connected = True
    h.agent.handle_event(Connected(session_present=True), h.now)
    ack_all(h)

    (fila,) = h.historial.recientes()
    assert (fila["estado"], fila["origen"]) == ("ENVIADO", "BUFFER_LOCAL")
    assert len(h.link.on("evento")) == 1
    # Sale el evento; solo queda la telemetría, que espera su ventana de transmisión.
    assert h.buffer.total() == 1
    h.buffer.close()


def test_con_conexion_la_ruta_es_directa_y_tiempo_real(tmp_path):
    h = Harness(tmp_path, historial=True)
    con_umbral(h)
    h.advance(0)
    ack_all(h)

    (fila,) = h.historial.recientes()
    assert (fila["ruta"], fila["estado"], fila["origen"]) == ("DIRECTA", "ENVIADO", "TIEMPO_REAL")
    h.buffer.close()


def test_normal_se_registra_localmente_y_no_se_publica(tmp_path):
    h = Harness(tmp_path, historial=True)
    con_umbral(h, NIVELES_NORMALES)
    h.advance(0)

    assert h.link.on("evento") == []
    (fila,) = h.historial.recientes()
    assert (fila["clasificacion"], fila["ruta"], fila["estado"]) == ("NORMAL", "LOCAL", "LOCAL")
    assert fila["detalle"] == {"id_umbral_ambiental": 7}
    assert fila["variables"][0]["tipo_variable"] == "temperatura_ambiente"
    h.buffer.close()


def test_sin_umbral_cada_lectura_queda_en_el_historial_sin_clasificar(tmp_path):
    """TC-M03-083: el error se publica una vez; las lecturas siguientes quedan LOCAL."""
    h = Harness(tmp_path, historial=True)
    h.advance(0)
    h.advance(15 * 60)
    publicada, local = h.historial.recientes()
    assert (publicada["clasificacion"], publicada["ruta"]) == ("ERROR_CONFIGURACION", "DIRECTA")
    assert publicada["id_evento"] is not None
    assert (local["clasificacion"], local["ruta"], local["estado"]) == (
        "ERROR_CONFIGURACION",
        "LOCAL",
        "LOCAL",
    )
    assert local["id_evento"] is None  # nunca llega al backend
    assert local["severidad"] is None
    assert local["detalle"]["severidad_invalida"] is True
    assert local["detalle"]["variable"] == "temperatura_ambiente"
    h.buffer.close()


def test_evento_de_mas_de_72_horas_queda_descartado(tmp_path):
    h = Harness(tmp_path, connected=False, historial=True)
    con_umbral(h)
    h.advance(0)

    h.now += 73 * 3600
    h.link.connected = True
    h.agent.handle_event(Connected(session_present=True), h.now)

    (fila,) = h.historial.recientes()
    assert fila["estado"] == "DESCARTADO"
    assert fila["detalle"] == {"motivo": "TIMESTAMP_EXPIRADO"}
    h.buffer.close()


def test_retencion_conserva_las_filas_mas_recientes(tmp_path):
    historial = Historial(tmp_path / "historial.db", max_filas=3)
    for i in range(5):
        historial.registrar_normal(_lectura(float(i)), id_umbral=7, ahora=1_000.0 + i)

    filas = historial.recientes()
    assert [f["variables"][0]["valor"] for f in filas] == [2.0, 3.0, 4.0]  # cronológico
    historial.close()


def test_el_historial_sobrevive_a_un_reinicio(tmp_path):
    historial = Historial(tmp_path / "historial.db", max_filas=10)
    historial.registrar_normal(_lectura(21.0), id_umbral=7, ahora=1_000.0)
    historial.close()

    reabierto = Historial(tmp_path / "historial.db", max_filas=10)
    assert len(reabierto.recientes()) == 1
    reabierto.close()


def test_inspeccionar_lista_buffer_en_orden_de_envio(tmp_path, capsys):
    h = Harness(tmp_path, connected=False, historial=True)
    con_umbral(h)
    h.advance(0)
    h.advance(10 * 60)  # segunda captura
    h.buffer.close()
    h.historial.close()

    assert inspeccionar.main(["--dir", str(tmp_path), "--json", "buffer", "--tipo", "evento"]) == 0
    filas = json.loads(capsys.readouterr().out)
    assert [f["clasificacion"] for f in filas] == ["DESVIACION_SIMPLE"] * 2
    capturas = [f["timestamp_captura"] for f in filas]
    assert capturas == sorted(capturas)
    assert all(f["estado_conectividad"] is False for f in filas)

    assert inspeccionar.main(["--dir", str(tmp_path), "--json", "historial"]) == 0
    filas = json.loads(capsys.readouterr().out)
    assert [f["ruta"] for f in filas] == ["BUFFER", "BUFFER"]


def test_inspeccionar_no_modifica_el_buffer(tmp_path, capsys):
    h = Harness(tmp_path, connected=False)
    con_umbral(h)
    h.advance(0)
    antes = h.buffer.total()

    assert inspeccionar.main(["--dir", str(tmp_path), "buffer"]) == 0
    assert h.buffer.total() == antes
    assert "fila(s)" in capsys.readouterr().err
    h.buffer.close()


def test_inspeccionar_sin_base_devuelve_error(tmp_path, capsys):
    assert inspeccionar.main(["--dir", str(tmp_path), "historial"]) == 1
    assert "No existe" in capsys.readouterr().err


def _lectura(valor: float) -> Reading:
    return Reading(
        serial="TC-M09-XXXX-0001",
        variable="temperatura_ambiente",
        valor_crudo=valor,
        unidad="C",
        timestamp_captura=datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
    )
