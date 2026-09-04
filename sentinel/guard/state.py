"""What the guard knows: the live process picture and the command history.

The guard is a passive observer.  It never writes to the plant — it only
reconstructs the plant's state from the same telemetry an operator sees, which
is exactly why telemetry integrity is part of the security problem.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from .. import config
from ..models import Command, Telemetry

CLEAN_RUN_TO_CLEAR = 20      # consecutive good frames that clear a past regression


@dataclass
class TelemetryIntegrity:
    """Detection layer 7 — can we trust the state we are about to reason about?"""

    last_seq: Optional[int] = None
    last_ts: Optional[int] = None
    last_arrival: float = 0.0
    repeat_count: int = 0
    regressions: int = 0
    frames: int = 0
    replay_frames: int = 0
    restarts: int = 0
    clean_run: int = 0

    def observe(self, frame: Telemetry, arrival: float | None = None) -> None:
        arrival = arrival if arrival is not None else time.time()
        self.frames += 1
        if self.last_seq is not None:
            if frame.seq == self.last_seq:
                self.repeat_count += 1
                self.replay_frames += 1
                self.clean_run = 0
            elif frame.seq < self.last_seq:
                if frame.ts > (self.last_ts or 0):
                    # Lower sequence but a *newer* timestamp: the controller restarted
                    # its counter. A replay carries an old timestamp instead.
                    self.restarts += 1
                    self.repeat_count = 0
                    self.regressions = 0
                    self.clean_run = 0
                else:
                    self.regressions += 1
                    self.replay_frames += 1
                    self.repeat_count = 0
                    self.clean_run = 0
            else:
                self.repeat_count = 0
                self.clean_run += 1
                if self.clean_run >= CLEAN_RUN_TO_CLEAR:
                    self.regressions = 0        # condition has cleared, stop alerting
        self.last_seq = frame.seq
        self.last_ts = frame.ts
        self.last_arrival = arrival

    def age_s(self, now: float | None = None) -> float:
        """Age of the newest frame, measured on the frame's own timestamp."""
        if self.last_ts is None:
            return float("inf")
        now = now if now is not None else time.time()
        return max(0.0, now - self.last_ts / 1000.0)

    def gap_s(self, now: float | None = None) -> float:
        """Wall-clock time since any frame arrived at all."""
        if not self.last_arrival:
            return float("inf")
        now = now if now is not None else time.time()
        return max(0.0, now - self.last_arrival)

    def trusted(self, now: float | None = None) -> bool:
        return (self.age_s(now) < config.TELEMETRY_STALE_S
                and self.repeat_count < config.REPLAY_REPEAT_COUNT
                and self.regressions == 0)

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        return {
            "seq": self.last_seq,
            "age_s": round(self.age_s(now), 1) if self.last_ts else None,
            "gap_s": round(self.gap_s(now), 1) if self.last_arrival else None,
            "repeat_count": self.repeat_count,
            "regressions": self.regressions,
            "restarts": self.restarts,
            "frames": self.frames,
            "replay_frames": self.replay_frames,
            "trusted": self.trusted(now),
        }


@dataclass
class CommandHistory:
    """Detection layers 2 and 3 — recent commands, with timing."""

    items: Deque[Command] = field(default_factory=lambda: deque(maxlen=config.COMMAND_HISTORY))
    ids: Deque[str] = field(default_factory=lambda: deque(maxlen=500))

    def add(self, command: Command) -> None:
        self.items.append(command)
        self.ids.append(command.id)

    def times_seen(self, command_id: str) -> int:
        return sum(1 for i in self.ids if i == command_id)

    def recent(self, window_s: float, now_ms: Optional[int] = None,
               actions: Optional[set[str]] = None) -> list[Command]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now_ms - int(window_s * 1000)
        return [c for c in self.items
                if c.ts >= cutoff and (actions is None or c.action in actions)]

    def last(self, n: int = 1) -> list[Command]:
        return list(self.items)[-n:]

    def last_action(self, action: str) -> Optional[Command]:
        for command in reversed(self.items):
            if command.action == action:
                return command
        return None

    def seconds_since(self, action: str, now_ms: Optional[int] = None) -> float:
        command = self.last_action(action)
        if command is None:
            return float("inf")
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        return (now_ms - command.ts) / 1000.0


@dataclass
class ProcessState:
    """The guard's mirror of the plant, rebuilt from telemetry."""

    telemetry: Optional[Telemetry] = None
    integrity: TelemetryIntegrity = field(default_factory=TelemetryIntegrity)
    history: CommandHistory = field(default_factory=CommandHistory)
    residual_since: Optional[float] = None
    setpoint_samples: Deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=4000))

    def update(self, frame: Telemetry, arrival: float | None = None) -> None:
        # A replayed frame must not overwrite a newer picture of the plant.
        if self.telemetry is None or frame.ts >= self.telemetry.ts:
            self.telemetry = frame
        self.integrity.observe(frame, arrival)
        self.setpoint_samples.append((frame.ts, frame.setpoint))
        # Instrument lag is normal for a second or two after a state change; only a
        # persistent mismatch between physics and telemetry is meaningful.
        arrival = arrival if arrival is not None else time.time()
        if self.flow_residual() > config.PHYSICS_RESIDUAL_LPM:
            self.residual_since = self.residual_since or arrival
        else:
            self.residual_since = None

    def setpoint_baseline(self, now_ms: int, window_s: float) -> Optional[float]:
        """The setpoint the plant was holding at the start of the window."""
        cutoff = now_ms - int(window_s * 1000)
        for ts, setpoint in self.setpoint_samples:
            if ts >= cutoff:
                return setpoint
        return self.setpoint_samples[-1][1] if self.setpoint_samples else None

    def residual_duration(self, now: float | None = None) -> float:
        if self.residual_since is None:
            return 0.0
        now = now if now is not None else time.time()
        return max(0.0, now - self.residual_since)

    @property
    def known(self) -> bool:
        return self.telemetry is not None

    def expected_flow(self) -> float:
        """Digital twin: the flow the physics says we should be seeing."""
        t = self.telemetry
        if t is None or not t.pump or not t.outlet_valve:
            return 0.0
        suction = 1.0 if t.tank_level >= config.PUMP_MIN_SUCTION_LEVEL else max(
            0.0, t.tank_level / config.PUMP_MIN_SUCTION_LEVEL) ** 1.5
        head_factor = 0.82 + 0.18 * (t.tank_level / 100.0)
        return config.PUMP_RATED_FLOW_LPM * head_factor * suction

    def flow_residual(self) -> float:
        if self.telemetry is None:
            return 0.0
        return abs(self.expected_flow() - self.telemetry.flow)

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        data = self.telemetry.to_dict() if self.telemetry else {}
        data["integrity"] = self.integrity.to_dict(now)
        data["expected_flow"] = round(self.expected_flow(), 1)
        data["flow_residual"] = round(self.flow_residual(), 1)
        return data
