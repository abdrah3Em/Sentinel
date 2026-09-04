"""Thin MQTT wrapper around paho-mqtt v2.

Every Sentinel process talks to the plant through this class, so the
publish/subscribe contract stays in one file.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from typing import Any, Callable

import paho.mqtt.client as mqtt

from . import config

log = logging.getLogger("sentinel.bus")

Handler = Callable[[str, dict[str, Any]], None]


class Bus:
    def __init__(self, client_id: str, host: str | None = None, port: int | None = None):
        self.client_id = client_id
        self.host = host or config.MQTT_HOST
        self.port = port or config.MQTT_PORT
        self._handlers: dict[str, list[Handler]] = {}
        self._connected = threading.Event()
        # Unique per process: two clients sharing an id make the broker drop one,
        # which shows up as mysterious "Unspecified error" disconnects.
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"sentinel-{client_id}-{secrets.token_hex(3)}",
            clean_session=True,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # ---------------------------------------------------------------- lifecycle
    def connect(self, timeout: float = 10.0) -> "Bus":
        self._client.connect_async(self.host, self.port, config.MQTT_KEEPALIVE)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            log.warning("%s: broker %s:%s not reachable yet, retrying in background",
                        self.client_id, self.host, self.port)
        return self

    def stop(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ---------------------------------------------------------------- messaging
    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers.setdefault(topic, []).append(handler)
        if self._connected.is_set():
            self._client.subscribe(topic, qos=0)

    def publish(self, topic: str, payload: dict[str, Any], retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload), qos=0, retain=retain)

    # ---------------------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected.set()
            for topic in self._handlers:
                client.subscribe(topic, qos=0)
            log.info("%s connected to %s:%s", self.client_id, self.host, self.port)
        else:
            log.error("%s connect failed: %s", self.client_id, reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected.clear()
        log.warning("%s disconnected (%s)", self.client_id, reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("%s: undecodable payload on %s", self.client_id, msg.topic)
            return
        for topic, handlers in self._handlers.items():
            if mqtt.topic_matches_sub(topic, msg.topic):
                for handler in handlers:
                    try:
                        handler(msg.topic, payload)
                    except Exception:  # a bad handler must not kill the loop
                        log.exception("%s: handler error on %s", self.client_id, msg.topic)
