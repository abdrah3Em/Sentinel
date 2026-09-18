"""Modbus TCP for the simulated RTUs (stdlib only): the server the plant runs, a passive
wire tap that decodes any Modbus/TCP byte stream, and a small client used by the injector.

    python -m sentinel.plant.modbus --map            # the active process's register map
    python -m sentinel.plant.modbus --tap 5030 5020  # transparent tap: listen 5030, forward to 5020

The brief's controllers speak Modbus TCP: a correctly formed write is obeyed,
nothing checks whether it makes sense.  The server does exactly that — every
coil or register write becomes a plant command on the command topic with
source ``modbus:<client ip>``, so the guard sees Modbus traffic the same way
it sees any other command.  Reads mirror live telemetry.

Function codes: 1 read coils, 2 read discrete inputs, 3 read holding
registers, 4 read input registers, 5 write single coil, 6 write single
register, 15 write multiple coils, 16 write multiple registers.  Exception
codes 1 (illegal function) and 2 (illegal address) are returned as the
protocol requires.

The tap (``ModbusTap``) parses both directions of a TCP stream into frames
{transaction, unit, function, name, address, count, values, direction} and
hands each to a callback; the plant publishes them on ``<ns>/modbus/frames``
and the proxy mode lets it sit in front of any RTU without touching traffic.
"""
from __future__ import annotations

import argparse
import logging
import socket
import struct
import threading
from typing import Any, Callable, Optional

log = logging.getLogger("sentinel.modbus")

WriteHandler = Callable[[str, Optional[float], str], None]     # action, value, client label
StateReader = Callable[[], dict[str, Any]]
FrameHandler = Callable[[dict[str, Any]], None]

FC_NAMES = {1: "read_coils", 2: "read_discrete_inputs", 3: "read_holding_registers", 4: "read_input_registers",
            5: "write_single_coil", 6: "write_single_register", 15: "write_multiple_coils",
            16: "write_multiple_registers"}
WRITES = {5, 6, 15, 16}


class ModbusMap:
    """How registers and coils map onto plant commands and telemetry."""

    def __init__(self, coils: dict[int, tuple[str, Optional[str]]], registers: dict[int, tuple[str, float]],
                 inputs: list[tuple[str, float]], discrete: Optional[list[str]] = None) -> None:
        self.coils = coils            # address -> (action when set, action when cleared)
        self.registers = registers    # address -> (action, scale: value = register * scale)
        self.inputs = inputs          # index -> (telemetry key, scale: register = value * scale)
        self.discrete = discrete or []  # index -> telemetry key (boolean)
        self.holding: dict[int, int] = {a: 0 for a in registers}
        self.coil_state: dict[int, bool] = {a: False for a in coils}

    def describe(self) -> str:
        lines = ["coils (FC 1/5/15):"]
        for a, (on, off) in sorted(self.coils.items()):
            lines.append(f"  {a:>3}  ON -> {on:<22} OFF -> {off or '-'}")
        lines.append("holding registers (FC 3/6/16):")
        for a, (action, scale) in sorted(self.registers.items()):
            lines.append(f"  {a:>3}  {action:<22} value = register x {scale:g}")
        lines.append("input registers (FC 4), telemetry mirror:")
        for i, (key, scale) in enumerate(self.inputs):
            lines.append(f"  {i:>3}  {key:<22} register = value x {scale:g}")
        if self.discrete:
            lines.append("discrete inputs (FC 2):")
            for i, key in enumerate(self.discrete):
                lines.append(f"  {i:>3}  {key}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------- frame parsing
def parse_pdu(pdu: bytes, direction: str = "request", request: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Decode one PDU (function code + data) into a frame dict.  Never raises on garbage."""
    if not pdu:
        return {"function": None, "name": "empty", "direction": direction}
    fc = pdu[0]
    frame: dict[str, Any] = {"function": fc & 0x7F, "name": FC_NAMES.get(fc & 0x7F, f"fc{fc & 0x7F}"),
                             "direction": direction, "exception": None, "address": None, "count": None, "values": None}
    try:
        if fc & 0x80:
            frame["exception"] = pdu[1] if len(pdu) > 1 else None
            return frame
        if direction == "request":
            if fc in (1, 2, 3, 4):
                frame["address"], frame["count"] = struct.unpack(">HH", pdu[1:5])
            elif fc == 5:
                addr, value = struct.unpack(">HH", pdu[1:5])
                frame.update(address=addr, count=1, values=[value == 0xFF00])
            elif fc == 6:
                addr, value = struct.unpack(">HH", pdu[1:5])
                frame.update(address=addr, count=1, values=[value])
            elif fc == 15:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                bits = [bool(pdu[6 + i // 8] >> (i % 8) & 1) for i in range(count)]
                frame.update(address=addr, count=count, values=bits)
            elif fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                frame.update(address=addr, count=count, values=list(struct.unpack(f">{count}H", pdu[6:6 + 2 * count])))
        else:
            if fc in (1, 2):
                n = pdu[1]
                bits = [bool(pdu[2 + i // 8] >> (i % 8) & 1) for i in range(n * 8)]
                count = request["count"] if request and request.get("count") else n * 8
                frame.update(address=request.get("address") if request else None, count=count, values=bits[:count])
            elif fc in (3, 4):
                n = pdu[1]
                frame.update(address=request.get("address") if request else None, count=n // 2,
                             values=list(struct.unpack(f">{n // 2}H", pdu[2:2 + n])))
            elif fc in (5, 6):
                addr, value = struct.unpack(">HH", pdu[1:5])
                frame.update(address=addr, count=1, values=[value == 0xFF00 if fc == 5 else value])
            elif fc in (15, 16):
                addr, count = struct.unpack(">HH", pdu[1:5])
                frame.update(address=addr, count=count)
    except (struct.error, IndexError):
        frame["name"] = frame["name"] + "?"
    return frame


class ModbusTap:
    """Reassembles a Modbus/TCP byte stream (either direction) into decoded frames.

    Feed bytes with ``feed(data, direction)``; each complete MBAP+PDU becomes one
    call to the frame handler.  Requests are remembered per transaction id so
    replies can be labelled with the address/count they answer."""

    def __init__(self, on_frame: FrameHandler, client: str = "") -> None:
        self.on_frame = on_frame
        self.client = client
        self._buf = {"request": b"", "response": b""}
        self._pending: dict[int, dict[str, Any]] = {}

    def feed(self, data: bytes, direction: str) -> list[dict[str, Any]]:
        buf = self._buf[direction] + data
        out: list[dict[str, Any]] = []
        while len(buf) >= 7:
            tid, pid, length, uid = struct.unpack(">HHHB", buf[:7])
            if pid != 0 or length < 1 or length > 260:      # not Modbus/TCP: resync by dropping a byte
                buf = buf[1:]
                continue
            if len(buf) < 6 + length:
                break
            pdu, buf = buf[7:6 + length], buf[6 + length:]
            frame = parse_pdu(pdu, direction, self._pending.get(tid) if direction == "response" else None)
            frame.update(transaction=tid, unit=uid, client=self.client, write=frame["function"] in WRITES)
            if direction == "request":
                self._pending[tid] = frame
                if len(self._pending) > 64:
                    self._pending.pop(next(iter(self._pending)))
            else:
                self._pending.pop(tid, None)
            self.on_frame(frame)
            out.append(frame)
        self._buf[direction] = buf
        return out


# ---------------------------------------------------------------------------- the RTU
class ModbusIngress:
    def __init__(self, port: int, mapping: ModbusMap, on_write: WriteHandler, read_state: StateReader,
                 host: str = "0.0.0.0", on_frame: Optional[FrameHandler] = None) -> None:
        self.port, self.map, self.on_write, self.read_state, self.host = port, mapping, on_write, read_state, host
        self.on_frame = on_frame
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
        log.info("modbus tcp RTU listening on %s:%s", self.host, self.port)
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
        tap = ModbusTap(self.on_frame, client) if self.on_frame else None
        with conn:
            buf = b""
            while self.running:
                try:
                    chunk = conn.recv(260)
                except OSError:
                    return
                if not chunk:
                    return
                if tap:
                    tap.feed(chunk, "request")
                buf += chunk
                while len(buf) >= 7:
                    tid, pid, length, uid = struct.unpack(">HHHB", buf[:7])
                    if len(buf) < 6 + length:
                        break
                    pdu, buf = buf[7:6 + length], buf[6 + length:]
                    reply = self._handle(pdu, client)
                    wire = struct.pack(">HHHB", tid, pid, len(reply) + 1, uid) + reply
                    if tap:
                        tap.feed(wire, "response")
                    conn.sendall(wire)

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
            if fc == 15:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                for i in range(count):
                    if addr + i not in self.map.coils:
                        raise KeyError(addr + i)
                for i in range(count):
                    self._write_coil(addr + i, bool(pdu[6 + i // 8] >> (i % 8) & 1), client)
                return pdu[:5]
            if fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                values = struct.unpack(f">{count}H", pdu[6:6 + 2 * count])
                for i in range(count):
                    if addr + i not in self.map.registers:
                        raise KeyError(addr + i)
                for i, value in enumerate(values):
                    self._write_register(addr + i, value, client)
                return pdu[:5]
            if fc in (1, 2):
                addr, count = struct.unpack(">HH", pdu[1:5])
                if fc == 1:
                    bits = [self.map.coil_state[addr + i] for i in range(count)]
                else:
                    state = self.read_state()
                    bits = [bool(state.get(self.map.discrete[addr + i])) for i in range(count)]
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
        except (KeyError, IndexError):
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
            key, scale = self.map.inputs[addr + i]          # IndexError -> illegal address
            value = state.get(key, 0)
            value = (1 if value else 0) if isinstance(value, bool) else float(value or 0)
            out.append(int(round(value * scale)) & 0xFFFF)
        return out


# ---------------------------------------------------------------------------- transparent tap
class ModbusProxyTap:
    """A passive tap for any RTU: listens on one port, forwards byte-for-byte to the real
    device, and decodes both directions into frames for the handler.  Nothing is altered."""

    def __init__(self, listen_port: int, upstream: tuple[str, int], on_frame: FrameHandler,
                 host: str = "0.0.0.0") -> None:
        self.listen_port, self.upstream, self.on_frame, self.host = listen_port, upstream, on_frame, host
        self._sock: Optional[socket.socket] = None
        self.running = False

    def start(self) -> "ModbusProxyTap":
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.listen_port))
        self.listen_port = self._sock.getsockname()[1]
        self._sock.listen(8)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()
        log.info("modbus tap on %s:%s -> %s:%s", self.host, self.listen_port, *self.upstream)
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
            threading.Thread(target=self._pipe, args=(conn, addr[0]), daemon=True).start()

    def _pipe(self, client_sock: socket.socket, client: str) -> None:
        tap = ModbusTap(self.on_frame, client)
        try:
            up = socket.create_connection(self.upstream, timeout=5)
        except OSError as e:
            log.warning("tap: upstream %s:%s unreachable: %s", *self.upstream, e)
            client_sock.close()
            return

        def forward(src: socket.socket, dst: socket.socket, direction: str) -> None:
            try:
                while self.running:
                    data = src.recv(4096)
                    if not data:
                        break
                    tap.feed(data, direction)
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        threading.Thread(target=forward, args=(up, client_sock, "response"), daemon=True).start()
        forward(client_sock, up, "request")


# ---------------------------------------------------------------------------- client helper
def _exchange(host: str, port: int, pdu: bytes, unit: int, timeout: float) -> bytes:
    frame = struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(frame)
        return s.recv(260)


def write(host: str, port: int, kind: str, address: int, value: int, unit: int = 1, timeout: float = 3.0) -> bool:
    """Send one Modbus TCP write (kind 'coil' or 'register').  Used by the attack CLI."""
    if kind == "coil":
        pdu = struct.pack(">BHH", 5, address, 0xFF00 if value else 0x0000)
    else:
        pdu = struct.pack(">BHH", 6, address, int(value) & 0xFFFF)
    reply = _exchange(host, port, pdu, unit, timeout)
    return len(reply) >= 8 and reply[7] == pdu[0]


def write_coils(host: str, port: int, address: int, bits: list[bool], unit: int = 1, timeout: float = 3.0) -> bool:
    out = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i // 8] |= 1 << (i % 8)
    pdu = struct.pack(">BHHB", 15, address, len(bits), len(out)) + bytes(out)
    reply = _exchange(host, port, pdu, unit, timeout)
    return len(reply) >= 8 and reply[7] == 15


def read_inputs(host: str, port: int, address: int, count: int, unit: int = 1, timeout: float = 3.0) -> list[int]:
    reply = _exchange(host, port, struct.pack(">BHH", 4, address, count), unit, timeout)
    n = reply[8]
    return list(struct.unpack(f">{n // 2}H", reply[9:9 + n]))


def read_discrete(host: str, port: int, address: int, count: int, unit: int = 1, timeout: float = 3.0) -> list[bool]:
    reply = _exchange(host, port, struct.pack(">BHH", 2, address, count), unit, timeout)
    return [bool(reply[9 + i // 8] >> (i % 8) & 1) for i in range(count)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Modbus TCP register map and passive tap")
    parser.add_argument("--map", action="store_true", help="print the active process's register map")
    parser.add_argument("--tap", nargs=2, metavar=("LISTEN", "UPSTREAM"),
                        help="transparent tap: LISTEN port, UPSTREAM host:port (e.g. 5030 127.0.0.1:5020)")
    args = parser.parse_args()
    if args.tap:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
        host, _, port = args.tap[1].rpartition(":")
        tap = ModbusProxyTap(int(args.tap[0]), (host or "127.0.0.1", int(port)),
                             lambda f: print(f"{f['direction']:<8} tid={f['transaction']} unit={f['unit']} "
                                             f"{f['name']} addr={f['address']} count={f['count']} values={f['values']}"))
        tap.start()
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            tap.stop()
        return 0
    from .. import process
    m = process.domain().MODBUS
    print(f"{process.domain().TITLE}\nModbus TCP unit 1 on port {process.domain().MODBUS_PORT}\n")
    print(ModbusMap(m["coils"], m["registers"], m["inputs"], m.get("discrete")).describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
