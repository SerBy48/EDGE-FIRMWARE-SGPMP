from edge_agent.lora import protocol as p
from edge_agent.lora.monitor import format_line, summary
from edge_agent.lora.sx1276 import Packet


def pkt(seq, node=1, net=0x2A):
    return Packet(p.build(net, node, seq, p.Estado(90, 0, 10, 15, 1, 0, 0)), -80.0, 7.5)


def test_monitor_cuenta_perdidas_y_resume():
    stats = {}
    format_line(pkt(1), stats, 0x2A)
    line = format_line(pkt(4), stats, 0x2A)
    assert "(+2 perdidas)" in line
    assert "DUPLICADA" in format_line(pkt(4), stats, 0x2A)
    assert "otra red" in format_line(pkt(5, net=0x01), stats, 0x2A)
    assert "INVÁLIDA" in format_line(Packet(b"\x00\x01", -100.0, -3.0), stats, 0x2A)

    table = summary(stats)
    assert "0x0001          2         2     50.0%" in table
