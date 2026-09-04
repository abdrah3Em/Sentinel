"""Shared harness: a plant + guard pair driven without MQTT or wall-clock waits."""
from __future__ import annotations

import time
from typing import Optional

from sentinel.guard.engine import CommandGuard
from sentinel.models import Command
from sentinel.plant.simulator import Plant


class Rig:
    """Drives the simulator and feeds the guard exactly what MQTT would."""

    def __init__(self, seconds_of_warmup: float = 60.0, **plant_kwargs):
        self.plant = Plant(noise=False, **plant_kwargs)
        self.guard = CommandGuard()
        self.now = time.time()
        self.advance(seconds_of_warmup)

    def advance(self, seconds: float, publish: bool = True) -> None:
        steps = int(seconds / 0.1)
        for i in range(steps):
            self.plant.step(0.1)
            self.now += 0.1
            if publish and i % 5 == 4:          # telemetry at 2 Hz
                self.publish_telemetry()

    def publish_telemetry(self) -> dict:
        frame = self.plant.telemetry().to_dict()
        frame["ts"] = int(self.now * 1000)
        self.guard.observe_telemetry(frame, now=self.now)
        return frame

    def send(self, action: str, value: Optional[float] = None, source: str = "operator-hmi",
             settle: float = 1.0):
        """Issue a command: the guard sees it, then the plant executes it."""
        command = Command(action=action, source=source, value=value)
        command.ts = int(self.now * 1000)
        alert = self.guard.observe_command(command.to_dict(), now=self.now)
        self.plant.apply(action, value)
        if settle:
            self.advance(settle)
        return alert

    def alerts(self, level: Optional[str] = None):
        return [a for a in self.guard.alerts if level is None or a.level == level]

    def worst(self):
        return max(self.guard.alerts, key=lambda a: a.score, default=None)
