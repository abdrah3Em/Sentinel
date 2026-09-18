"""Transports behind one event schema.

Sentinel's processes exchange three kinds of message — telemetry frames,
commands and events — as plain dicts on named channels.  ``Envelope`` is that
schema.  The MQTT adapter carries envelopes over the broker; the Modbus
adapter turns RTU traffic into envelopes (writes become commands, decoded
frames become events).  Nothing above this package knows which wire was used.
"""
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


from .mqtt import MqttTransport, channel_kind  # noqa: E402
from .modbus import ModbusTransport  # noqa: E402

__all__ = ["Envelope", "KINDS", "MqttTransport", "ModbusTransport", "channel_kind"]
