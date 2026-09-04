"""Process simulator service: runs the physics and speaks MQTT.

    python -m sentinel.plant.run

Subscribes : plant/command   (control commands, from anybody)
             plant/sim       (attack-simulator hooks, e.g. telemetry hold)
Publishes  : plant/telemetry (2 Hz sensor frames)
             plant/event     (physical/process events)
"""
from __future__ import annotations

import logging
import threading
import time

from .. import config
from ..bus import Bus
from ..models import Event, now_ms
from .simulator import ALL_ACTIONS, Plant

log = logging.getLogger("sentinel.plant")


class PlantService:
    def __init__(self) -> None:
        self.plant = Plant()
        self.bus = Bus("plant")
        self.lock = threading.Lock()
        self.telemetry_hold_until = 0.0     # attacker-controlled telemetry blackout
        self.running = True

    # ------------------------------------------------------------------ inputs
    def on_command(self, topic: str, payload: dict) -> None:
        action = payload.get("action")
        if action not in ALL_ACTIONS:
            self._publish_event(Event("COMMAND_REJECTED", payload.get("source", "unknown"),
                                      {"detail": f"Unknown action '{action}'"}))
            return
        with self.lock:
            events = self.plant.apply(action, payload.get("value"))
        source = payload.get("source", "unknown")
        value = payload.get("value")
        label = f"{action}" + (f" = {value}" if value is not None else "")
        self._publish_event(Event("COMMAND_ACCEPTED", source, {
            "detail": f"Controller accepted {label}",
            "action": action,
            "value": value,
            "command_id": payload.get("id"),
        }))
        for ev in events:
            self._publish_event(Event(ev.type, "plant", {"detail": ev.detail, "severity": ev.severity}))

    def on_sim_control(self, topic: str, payload: dict) -> None:
        """Hooks used only by the attack simulator to model a compromised link."""
        if "telemetry_hold_s" in payload:
            seconds = float(payload["telemetry_hold_s"])
            self.telemetry_hold_until = time.time() + seconds
            self._publish_event(Event("TELEMETRY_PATH", payload.get("source", "attacker"), {
                "detail": f"Plant telemetry publication suppressed for {seconds:.0f}s",
                "severity": "HIGH",
            }))
        if payload.get("reset"):
            with self.lock:
                self.plant = Plant()
            self.telemetry_hold_until = 0.0
            self._publish_event(Event("SIM_RESET", "operator", {"detail": "Process simulator reset to nominal"}))

    # ----------------------------------------------------------------- outputs
    def _publish_event(self, event: Event) -> None:
        self.bus.publish(config.TOPIC_EVENT, event.to_dict())

    def _publish_state(self) -> None:
        with self.lock:
            snapshot = self.plant.snapshot()
        self.bus.publish(config.TOPIC_MODE, {
            "ts": now_ms(),
            "mode": snapshot["mode"],
            "maintenance": snapshot["maintenance"],
            "setpoint": snapshot["setpoint"],
        }, retain=True)

    # ------------------------------------------------------------------- loops
    def run(self) -> None:
        self.bus.connect()
        self.bus.subscribe(config.TOPIC_COMMAND, self.on_command)
        self.bus.subscribe("plant/sim", self.on_sim_control)
        self.bus.subscribe(config.TOPIC_CONTROL, self.on_sim_control)
        log.info("process simulator running — telemetry every %.1fs", config.TELEMETRY_PERIOD_S)

        last_telemetry = 0.0
        last_mode = 0.0
        last_tick = time.monotonic()
        while self.running:
            time.sleep(config.SIM_TICK_S)
            # Step by the time that actually elapsed, so simulation time == wall
            # time: pump runtimes, dead-head durations and trend spans in the
            # logs are real seconds, not loop iterations.
            tick = time.monotonic()
            dt = min(0.5, tick - last_tick)
            last_tick = tick
            with self.lock:
                events = self.plant.step(dt)
            for ev in events:
                self._publish_event(Event(ev.type, "plant", {"detail": ev.detail, "severity": ev.severity}))

            now = time.time()
            if now - last_telemetry >= config.TELEMETRY_PERIOD_S:
                last_telemetry = now
                # The controller keeps producing frames even when the attacker has cut the
                # path to the network — so its sequence counter keeps advancing.
                with self.lock:
                    frame = self.plant.telemetry().to_dict()
                if now >= self.telemetry_hold_until:
                    self.bus.publish(config.TOPIC_TELEMETRY, frame)
            if now - last_mode >= 2.0:
                last_mode = now
                self._publish_state()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(name)-16s %(levelname)-7s %(message)s")
    service = PlantService()
    try:
        service.run()
    except KeyboardInterrupt:
        service.running = False
        service.bus.stop()


if __name__ == "__main__":
    main()
