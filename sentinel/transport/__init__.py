"""Transports behind one event schema.

Sentinel's processes exchange three kinds of message — telemetry frames,
commands and events — as plain dicts on named channels.  ``Envelope`` is that
schema.  The MQTT adapter carries envelopes over the broker; the Modbus
adapter turns RTU traffic into envelopes (writes become commands, decoded
frames become events).  Nothing above this package knows which wire was used.
"""
from __future__ import annotations

from .schema import KINDS, Envelope  # noqa: E402
from .mqtt import MqttTransport, channel_kind  # noqa: E402
from .modbus import ModbusTransport  # noqa: E402

__all__ = ["Envelope", "KINDS", "MqttTransport", "ModbusTransport", "channel_kind"]
