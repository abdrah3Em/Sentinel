"""Minimal Modbus TCP ingress for the simulated plant (stdlib only).

The brief's controllers speak Modbus TCP: a correctly formed write is obeyed,
nothing checks whether it makes sense.  This gateway does exactly that — every
coil or register write becomes a plant command on the MQTT command topic with
source ``modbus:<client ip>``, so the guard sees Modbus traffic the same way it
sees any other command.  Reads mirror live telemetry into input registers.

Supported function codes: 1 read coils, 3 read holding registers,
4 read input registers, 5 write single coil, 6 write single register,
16 write multiple registers.  Exception codes 1 (illegal function) and
2 (illegal address) are returned as the protocol requires.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import Any, Callable, Optional

log = logging.getLogger("sentinel.modbus")

WriteHandler = Callable[[str, Optional[float], str], None]     # action, value, client label
StateReader = Callable[[], dict[str, Any]]


class ModbusMap:
    """How registers and coils map onto plant commands and telemetry."""

    def __init__(self, coils: dict[int, tuple[str, Optional[str]]], registers: dict[int, tuple[str, float]],
                 inputs: list[tuple[str, float]]) -> None:
        self.coils = coils            # address -> (action when set, action when cleared)
        self.registers = registers    # address -> (action, scale: value = register * scale)
        self.inputs = inputs          # index -> (telemetry key, scale: register = value * scale)
        self.holding: dict[int, int] = {a: 0 for a in registers}
        self.coil_state: dict[int, bool] = {a: False for a in coils}


class ModbusIngress:
    def __init__(self, port: int, mapping: ModbusMap, on_write: WriteHandler, read_state: StateReader,
                 host: str = "0.0.0.0") -> None:
        self.port, self.map, self.on_write, self.read_state, self.host = port, mapping, on_write, read_state, host
        self._sock: Optional[socket.socket] = None
        self.running = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "ModbusIngress":
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(8)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()
        log.info("modbus tcp ingress listening on %s:%s", self.host, self.port)
        return self

    def stop(self) -> None:
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def _accept(self) -> None:
        while self.running:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn, addr[0]), daemon=True).start()

    def _serve(self, conn: socket.socket, client: str) -> None:
        with conn:
            buf = b""
            while self.running:
                try:
                    chunk = conn.recv(260)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while len(buf) >= 7:
                    tid, pid, length, uid = struct.unpack(">HHHB", buf[:7])
                    if len(buf) < 6 + length:
                        break
                    pdu, buf = buf[7:6 + length], buf[6 + length:]
                    reply = self._handle(pdu, client)
                    conn.sendall(struct.pack(">HHHB", tid, pid, len(reply) + 1, uid) + reply)

    # ------------------------------------------------------------------ protocol
    def _handle(self, pdu: bytes, client: str) -> bytes:
        fc = pdu[0]
        try:
            if fc == 5:
                addr, value = struct.unpack(">HH", pdu[1:5])
                self._write_coil(addr, value == 0xFF00, client)
                return pdu
            if fc == 6:
                addr, value = struct.unpack(">HH", pdu[1:5])
                self._write_register(addr, value, client)
                return pdu
            if fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                values = struct.unpack(f">{count}H", pdu[6:6 + 2 * count])
                for i, value in enumerate(values):
                    self._write_register(addr + i, value, client)
                return pdu[:5]
            if fc == 1:
                addr, count = struct.unpack(">HH", pdu[1:5])
                bits = [self.map.coil_state.get(addr + i, False) for i in range(count)]
                out = bytearray((count + 7) // 8)
                for i, bit in enumerate(bits):
                    if bit:
                        out[i // 8] |= 1 << (i % 8)
                return bytes([fc, len(out)]) + bytes(out)
            if fc in (3, 4):
                addr, count = struct.unpack(">HH", pdu[1:5])
                regs = self._holding(addr, count) if fc == 3 else self._inputs(addr, count)
                return bytes([fc, 2 * count]) + struct.pack(f">{count}H", *regs)
            return bytes([fc | 0x80, 1])
        except KeyError:
            return bytes([fc | 0x80, 2])

    def _write_coil(self, addr: int, on: bool, client: str) -> None:
        set_action, clear_action = self.map.coils[addr]
        self.map.coil_state[addr] = on
        action = set_action if on else clear_action
        if action:
            self.on_write(action, None, client)

    def _write_register(self, addr: int, raw: int, client: str) -> None:
        action, scale = self.map.registers[addr]
        self.map.holding[addr] = raw
        signed = raw - 0x10000 if raw >= 0x8000 else raw
        self.on_write(action, round(signed * scale, 4), client)

    def _holding(self, addr: int, count: int) -> list[int]:
        return [self.map.holding[addr + i] for i in range(count)]

    def _inputs(self, addr: int, count: int) -> list[int]:
        state = self.read_state()
        out = []
        for i in range(count):
            key, scale = self.map.inputs[addr + i]          # IndexError -> treated as KeyError below
            value = state.get(key, 0)
            value = (1 if value else 0) if isinstance(value, bool) else float(value or 0)
            out.append(int(round(value * scale)) & 0xFFFF)
        return out


# ---------------------------------------------------------------------------- client helper
def write(host: str, port: int, kind: str, address: int, value: int, unit: int = 1, timeout: float = 3.0) -> bool:
    """Send one Modbus TCP write (kind 'coil' or 'register').  Used by the attack CLI."""
    if kind == "coil":
        pdu = struct.pack(">BHH", 5, address, 0xFF00 if value else 0x0000)
    else:
        pdu = struct.pack(">BHH", 6, address, int(value) & 0xFFFF)
    frame = struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(frame)
        reply = s.recv(260)
    return len(reply) >= 8 and reply[7] == pdu[0]


def read_inputs(host: str, port: int, address: int, count: int, unit: int = 1, timeout: float = 3.0) -> list[int]:
    pdu = struct.pack(">BHH", 4, address, count)
    frame = struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(frame)
        reply = s.recv(260)
    n = reply[8]
    return list(struct.unpack(f">{n // 2}H", reply[9:9 + n]))
