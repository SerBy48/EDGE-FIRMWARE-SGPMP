# | Fecha      | Nombre   | Que se hizo                                                     |
# |------------|----------|-----------------------------------------------------------------|
# | 2026-11-02 | Arekkazu | V1 RF-53: seguimiento y métricas de la cascada de visión         |
# | 2026-11-02 | Arekkazu | V1 RF-53: individuos observados = máximo de la ventana           |
"""Cascada de visión con cajas sintéticas: sin modelo ni OpenCV."""

from edge_vision.cascada import Seguidor, Ventana, iou


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1
    assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0
    assert round(iou((0, 0, 10, 10), (5, 0, 10, 10)), 3) == 0.333


def test_un_animal_que_camina_y_otro_quieto_dan_actividad_media():
    seg, ventana = Seguidor(), Ventana(diagonal=100.0, umbral_activo=0.02)
    for s in range(11):  # 10 s a 1 cuadro por segundo
        cajas = [(10 + 3 * s, 10, 10, 10), (70, 70, 10, 10)]  # uno avanza 3 px/s, el otro quieto
        seg.actualizar(float(s), cajas)
        ventana.agregar_cuadro(cajas)
    v = ventana.cerrar(seg, 0.0, 10.0)
    assert v["n_individuos_observados"] == 2
    assert v["calidad"] == {
        "cobertura_ventana": 1.0,
        "n_tracks": 2,
        "n_tracks_perdidos": 0,
        "fps_efectivo": 1.1,
    }
    # El que camina va a 0,03 D/s y el quieto a 0: media 0,015 y la mitad activos.
    assert v["componentes"]["tasa_movimiento"] == 0.015
    assert v["componentes"]["densidad_actividad"] == 0.5
    assert v["componentes"]["dispersion_espacial"] > 0


def test_individuos_observados_es_el_maximo_visto_a_la_vez():
    seg, ventana = Seguidor(), Ventana(diagonal=100.0)
    for s, n in enumerate((3, 5, 2, 4)):
        cajas = [(20.0 * k, 0.0, 10.0, 10.0) for k in range(n)]
        seg.actualizar(float(s), cajas)
        ventana.agregar_cuadro(cajas)
    assert ventana.cerrar(seg, 0.0, 3.0)["n_individuos_observados"] == 5


def test_un_track_que_desaparece_cuenta_como_perdido_y_sin_cuadros_todo_es_cero():
    seg, ventana = Seguidor(max_sin_ver=2), Ventana(diagonal=100.0)
    for s in range(3):
        seg.actualizar(float(s), [(0, 0, 10, 10)])
        ventana.agregar_cuadro([(0, 0, 10, 10)])
    for s in range(3, 7):
        seg.actualizar(float(s), [])
        ventana.agregar_cuadro([])
    v = ventana.cerrar(seg, 0.0, 6.0)
    assert (v["calidad"]["n_tracks"], v["calidad"]["n_tracks_perdidos"]) == (1, 1)
    assert v["calidad"]["cobertura_ventana"] == round(3 / 7, 4)
    assert Ventana(diagonal=1.0).cerrar(Seguidor(), 0.0, 1.0)["componentes"] == {
        "densidad_actividad": 0.0,
        "dispersion_espacial": 0.0,
        "tasa_movimiento": 0.0,
    }
