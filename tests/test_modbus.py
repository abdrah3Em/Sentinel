"""Modbus TCP ingress: well-formed writes become commands; reads mirror telemetry."""
import socket
import struct

from sentinel.plant.modbus import ModbusIngress, ModbusMap, read_inputs, write


def make_server(seen):
    mapping = ModbusMap({0: ("cb_close", "cb_open"), 3: ("protection_reset", None)},
                        {1: ("avc_target", 0.01)}, [("v_bus_kv", 100), ("cb_closed", 1)])
    state = {"v_bus_kv": 11.02, "cb_closed": True}
    server = ModbusIngress(0, mapping, lambda a, v, c: seen.append((a, v, c)), lambda: state, host="127.0.0.1")
    return server.start()


def test_coil_and_register_writes_become_commands():
    seen = []
    server = make_server(seen)
    try:
        assert write("127.0.0.1", server.port, "coil", 0, 0)
        assert write("127.0.0.1", server.port, "register", 1, 1180)
        assert write("127.0.0.1", server.port, "coil", 3, 1)
    finally:
        server.stop()
    assert [(a, v) for a, v, _ in seen] == [("cb_open", None), ("avc_target", 11.8), ("protection_reset", None)]
    assert all(c == "127.0.0.1" for _, _, c in seen)


def test_input_registers_mirror_live_telemetry():
    server = make_server([])
    try:
        assert read_inputs("127.0.0.1", server.port, 0, 2) == [1102, 1]
    finally:
        server.stop()


def test_illegal_function_and_address_get_exception_responses():
    server = make_server([])
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=3) as s:
            pdu = struct.pack(">BHH", 5, 9, 0xFF00)                  # coil 9 is not mapped
            s.sendall(struct.pack(">HHHB", 7, 0, len(pdu) + 1, 1) + pdu)
            reply = s.recv(64)
            assert reply[7] == 0x85 and reply[8] == 2
            pdu = bytes([0x2B, 0, 0])                                 # unsupported function code
            s.sendall(struct.pack(">HHHB", 8, 0, len(pdu) + 1, 1) + pdu)
            reply = s.recv(64)
            assert reply[7] == 0xAB and reply[8] == 1
    finally:
        server.stop()


def test_discrete_inputs_and_multiple_coils():
    from sentinel.plant.modbus import ModbusIngress, ModbusMap, read_discrete, write_coils
    seen = []
    mapping = ModbusMap({0: ("cb_close", "cb_open"), 1: ("sw_close", "sw_open")}, {}, [("v_bus_kv", 100)],
                        discrete=["cb_closed", "protection_tripped"])
    state = {"cb_closed": True, "protection_tripped": False, "v_bus_kv": 11.0}
    server = ModbusIngress(0, mapping, lambda a, v, c: seen.append(a), lambda: state, host="127.0.0.1").start()
    try:
        assert read_discrete("127.0.0.1", server.port, 0, 2) == [True, False]
        assert write_coils("127.0.0.1", server.port, 0, [False, True])
    finally:
        server.stop()
    assert seen == ["cb_open", "sw_close"]


def test_wire_tap_decodes_both_directions_and_survives_junk():
    from sentinel.plant.modbus import ModbusTap
    frames = []
    tap = ModbusTap(frames.append, client="10.0.0.9")
    req = struct.pack(">HHHB", 9, 0, 6, 1) + struct.pack(">BHH", 6, 1, 1180)
    tap.feed(b"\xff\xff" + req[:5], "request")           # junk prefix and a split frame
    tap.feed(req[5:], "request")
    rsp = struct.pack(">HHHB", 9, 0, 6, 1) + struct.pack(">BHH", 6, 1, 1180)
    tap.feed(rsp, "response")
    tap.feed(struct.pack(">HHHB", 10, 0, 6, 1) + struct.pack(">BHH", 4, 0, 3), "request")
    tap.feed(struct.pack(">HHHB", 10, 0, 9, 1) + bytes([4, 6]) + struct.pack(">HHH", 1100, 1060, 180), "response")
    assert [f["name"] for f in frames] == ["write_single_register", "write_single_register",
                                          "read_input_registers", "read_input_registers"]
    assert frames[0]["write"] and frames[0]["values"] == [1180] and frames[0]["client"] == "10.0.0.9"
    assert frames[3]["values"] == [1100, 1060, 180] and frames[3]["address"] == 0 and frames[3]["count"] == 3


def test_transparent_proxy_tap_forwards_untouched_and_decodes():
    from sentinel.plant.modbus import ModbusIngress, ModbusMap, ModbusProxyTap, write, read_inputs
    seen, frames = [], []
    mapping = ModbusMap({0: ("cb_close", "cb_open")}, {1: ("avc_target", 0.01)}, [("v_bus_kv", 100)])
    rtu = ModbusIngress(0, mapping, lambda a, v, c: seen.append((a, v)), lambda: {"v_bus_kv": 11.02}, host="127.0.0.1").start()
    tap = ModbusProxyTap(0, ("127.0.0.1", rtu.port), frames.append, host="127.0.0.1").start()
    try:
        assert write("127.0.0.1", tap.listen_port, "register", 1, 1180)
        assert read_inputs("127.0.0.1", tap.listen_port, 0, 1) == [1102]
    finally:
        tap.stop(); rtu.stop()
    assert seen == [("avc_target", 11.8)]
    names = [(f["direction"], f["name"]) for f in frames]
    assert ("request", "write_single_register") in names and ("response", "read_input_registers") in names


def test_transport_envelopes_wrap_rtu_traffic():
    from sentinel.transport import Envelope, ModbusTransport
    from sentinel.plant.modbus import write
    got = []
    t = ModbusTransport(0, {"coils": {0: ("cb_close", "cb_open")}, "registers": {}, "inputs": [("v_bus_kv", 100)]},
                        lambda: {"v_bus_kv": 11.0}, got.append, host="127.0.0.1").start()
    try:
        assert write("127.0.0.1", t.port, "coil", 0, 0)
    finally:
        t.stop()
    kinds = {e.kind for e in got}
    assert kinds == {"command", "modbus"} and all(isinstance(e, Envelope) and e.transport == "modbus" for e in got)
    assert next(e for e in got if e.kind == "command").payload["action"] == "cb_open"
