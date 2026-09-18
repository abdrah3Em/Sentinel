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
