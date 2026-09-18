"""MQTT as an adapter: envelopes in, envelopes out, over the Bus wrapper."""
from __future__ import annotations

from typing import Any, Callable

from .. import config
from ..bus import Bus

Handler = Callable[["Envelope"], None]

_KINDS = {
    config.TOPIC_TELEMETRY: "telemetry", config.TOPIC_COMMAND: "command", config.TOPIC_EVENT: "event",
    config.TOPIC_ALERT: "alert", config.TOPIC_ASSESSMENT: "assessment", config.TOPIC_STATUS: "status",
    config.TOPIC_MODBUS: "modbus", config.TOPIC_CONTROL: "control", config.TOPIC_SIM: "sim", config.TOPIC_MODE: "mode",
}
_TOPICS = {v: k for k, v in _KINDS.items()}


def channel_kind(topic: str) -> str:
    return _KINDS.get(topic, "event")


class MqttTransport:
    def __init__(self, client_id: str) -> None:
        self.bus = Bus(client_id)

    def connect(self) -> "MqttTransport":
        self.bus.connect()
        return self

    def stop(self) -> None:
        self.bus.stop()

    def publish(self, envelope: "Envelope", retain: bool = False) -> None:
        self.bus.publish(envelope.channel or _TOPICS[envelope.kind], envelope.payload, retain=retain)

    def send(self, kind: str, payload: dict[str, Any], retain: bool = False) -> None:
        from . import Envelope
        self.publish(Envelope(kind, _TOPICS[kind], payload, "mqtt"), retain=retain)

    def subscribe(self, kind: str, handler: Handler) -> None:
        from . import Envelope
        topic = _TOPICS[kind]
        self.bus.subscribe(topic, lambda t, p: handler(Envelope(kind, t, p, "mqtt")))
