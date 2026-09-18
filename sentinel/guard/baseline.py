"""Learned normal (layer 3/4 support): what this plant's traffic usually looks like.

The brief says normal traffic is repetitive, so anything unusual stands out.
Fixed thresholds catch the gross cases; this small learner catches a command
that is ordinary in itself but unlike anything the same source normally does:
a source that suddenly commands far faster than its own history, or a value
outside the range that action has ever taken.  It is deliberately conservative
(needs MIN_SAMPLES before it says anything) and weighs little.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Deque, Optional

from ..models import Command

MIN_SAMPLES = 20
KEEP = 300


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return ordered[idx]


class Baseline:
    def __init__(self) -> None:
        self.intervals: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=KEEP))
        self.values: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=KEEP))
        self.last_ts: dict[str, int] = {}

    def observe(self, command: Command) -> None:
        """Record a command *after* it has been judged, so it never explains itself."""
        last = self.last_ts.get(command.source)
        if last is not None and command.ts >= last:
            self.intervals[command.source].append((command.ts - last) / 1000.0)
        self.last_ts[command.source] = command.ts
        if command.value is not None:
            try:
                self.values[command.action].append(float(command.value))
            except (TypeError, ValueError):
                pass

    def cadence_deviation(self, command: Command) -> Optional[str]:
        history = self.intervals.get(command.source)
        last = self.last_ts.get(command.source)
        if not history or len(history) < MIN_SAMPLES or last is None:
            return None
        interval = (command.ts - last) / 1000.0
        typical = _percentile(list(history), 0.5)
        floor = _percentile(list(history), 0.05)
        if interval < 2.0 and interval < floor / 4 and typical > 8.0:
            return (f"'{command.source}' usually commands every {typical:.0f} s; this one came {interval:.1f} s "
                    "after the last")
        return None

    def value_deviation(self, command: Command) -> Optional[str]:
        if command.value is None:
            return None
        history = self.values.get(command.action)
        if not history or len(history) < MIN_SAMPLES:
            return None
        try:
            value = float(command.value)
        except (TypeError, ValueError):
            return None
        lo, hi = _percentile(list(history), 0.05), _percentile(list(history), 0.95)
        margin = max(0.05 * abs(hi - lo), 1e-9)
        if value < lo - margin or value > hi + margin:
            return f"{command.action} = {value:g} is outside the learned {lo:g}–{hi:g} range of the last {len(history)} commands"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"intervals": {k: list(v) for k, v in self.intervals.items()},
                "values": {k: list(v) for k, v in self.values.items()},
                "last_ts": dict(self.last_ts)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Baseline":
        b = cls()
        for k, v in (data.get("intervals") or {}).items():
            b.intervals[k].extend(v)
        for k, v in (data.get("values") or {}).items():
            b.values[k].extend(v)
        b.last_ts.update(data.get("last_ts") or {})
        return b
