"""Offline harness: a simulator + guard pair driven without MQTT or wall-clock waits.

Used by the tests and by ``python -m sentinel.evaluate`` to replay every
scenario deterministically and measure what the guard concludes.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from . import process
from .guard.engine import CommandGuard
from .models import Command


class Rig:
    """Drives the active process's simulator and feeds the guard exactly what MQTT would."""

    def __init__(self, seconds_of_warmup: float = 60.0, **plant_kwargs):
        self.plant = process.domain().Plant(noise=False, **plant_kwargs)
        self.guard = CommandGuard()
        self.now = time.time()
        self.frozen: Optional[dict] = None          # telemetry replay: the frame the attacker repeats
        self.frozen_until = 0.0
        self.advance(seconds_of_warmup)

    def advance(self, seconds: float, publish: bool = True) -> None:
        steps = int(round(seconds / 0.1))
        for i in range(steps):
            self.plant.step(0.1)
            self.now += 0.1
            if publish and i % 5 == 4:          # telemetry at 2 Hz
                self.publish_telemetry()
            if i % 10 == 9:                     # the guard's periodic integrity/physics check
                self.guard.evaluate_process(now=self.now)

    def publish_telemetry(self) -> dict:
        if self.frozen is not None and self.now < self.frozen_until:
            self.guard.observe_telemetry(dict(self.frozen), now=self.now)   # verbatim replay
            return self.frozen
        self.frozen = None
        frame = self.plant.telemetry().to_dict()
        frame["ts"] = int(self.now * 1000)
        self.guard.observe_telemetry(frame, now=self.now)
        self.last_frame = frame
        return frame

    def hold_and_replay(self, seconds: float) -> None:
        """Silence the controller and replay the last frame for a while (the attacker's move)."""
        self.frozen = dict(getattr(self, "last_frame", self.plant.telemetry().to_dict()))
        self.frozen_until = self.now + seconds

    def send(self, action: str, value: Optional[Any] = None, source: str = "operator-hmi",
             settle: float = 1.0, ts: Optional[int] = None, command_id: Optional[str] = None):
        """Issue a command: the guard sees it, then the plant executes it."""
        command = Command(action=action, source=source, value=value)
        command.ts = ts if ts is not None else int(self.now * 1000)
        if command_id:
            command.id = command_id
        self.last_command = command.to_dict()
        alert = self.guard.observe_command(command.to_dict(), now=self.now)
        self.plant.apply(action, value)
        if settle:
            self.advance(settle)
        return alert

    def replay_command(self, captured: dict, age_s: float):
        """Re-issue a recorded command verbatim: same id, timestamp from when it was captured."""
        replayed = dict(captured, ts=captured["ts"] - int(age_s * 1000))
        alert = self.guard.observe_command(replayed, now=self.now)
        self.plant.apply(replayed["action"], replayed.get("value"))
        return alert

    def sim(self, hook: str, value: Any = True, settle: float = 1.0) -> None:
        """Poke a simulator-only hook (fault inject / clear) — the guard sees only the consequences."""
        self.plant.sim_hook({hook: value})
        if settle:
            self.advance(settle)

    def alerts(self, level: Optional[str] = None):
        return [a for a in self.guard.alerts if level is None or a.level == level]

    def worst(self):
        return max(self.guard.alerts, key=lambda a: a.score, default=None)
