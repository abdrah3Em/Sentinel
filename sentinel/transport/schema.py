"""The one event schema every transport carries."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import now_ms

KINDS = ("telemetry", "command", "event", "alert", "assessment", "status", "modbus", "control", "sim", "mode")


@dataclass
class Envelope:
    kind: str                       # one of KINDS
    channel: str                    # the transport's name for it (MQTT topic, Modbus port)
    payload: dict[str, Any]
    transport: str = "mqtt"
    ts: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown envelope kind {self.kind!r}")
