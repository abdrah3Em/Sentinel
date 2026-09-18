"""Modbus TCP as an adapter: the RTU's writes become command envelopes, its decoded frames
become 'modbus' envelopes.  The plant service owns the RTU; this wraps it in the schema."""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..models import Command
from ..plant.modbus import ModbusIngress, ModbusMap
from .schema import Envelope

Handler = Callable[[Envelope], None]


class ModbusTransport:
    def __init__(self, port: int, mapping: dict[str, Any], read_state: Callable[[], dict[str, Any]],
                 on_envelope: Handler, host: str = "0.0.0.0") -> None:
        self.port, self.on_envelope, self.read_state, self.host = port, on_envelope, read_state, host
        self.map = ModbusMap(mapping["coils"], mapping["registers"], mapping["inputs"], mapping.get("discrete"))
        self.ingress: Optional[ModbusIngress] = None

    def _write(self, action: str, value: Optional[float], client: str) -> None:
        command = Command(action=action, source=f"modbus:{client}", value=value)
        self.on_envelope(Envelope("command", f"modbus:{self.port}", command.to_dict(), "modbus"))

    def _frame(self, frame: dict[str, Any]) -> None:
        self.on_envelope(Envelope("modbus", f"modbus:{self.port}", frame, "modbus"))

    def start(self) -> "ModbusTransport":
        self.ingress = ModbusIngress(self.port, self.map, self._write, self.read_state, host=self.host,
                                     on_frame=self._frame).start()
        self.port = self.ingress.port
        return self

    def stop(self) -> None:
        if self.ingress:
            self.ingress.stop()
