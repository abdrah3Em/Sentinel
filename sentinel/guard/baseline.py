"""Learned normal: what this plant's traffic usually looks like (layer 3/4 support, BASE-001).

The brief says normal traffic is repetitive, so anything unusual stands out.
Hand-set thresholds in config.py catch the gross cases and are the cold-start
fallback; this learner adds three things a fixed threshold cannot know:

  * cadence per source   — inter-arrival EWMA and quantiles: a source that
                           suddenly commands far faster than its own history;
  * value ranges         — quantiles per action: a setpoint inside the
                           configured band but outside anything ever asked for;
  * command mix          — which actions each source issues: a first-ever
                           action from a source with a long history.

It is deliberately conservative (MIN_SAMPLES before it says anything) and weighs
little: it explains, it does not decide.  Everything it learned is exported by
summary() so the console can show learned versus configured side by side.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any, Deque, Optional

from .. import config, process
from ..models import Command

MIN_SAMPLES = 20
KEEP = 300
EWMA_ALPHA = 0.1


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return ordered[idx]


class Baseline:
    def __init__(self) -> None:
        self.intervals: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=KEEP))
        self.ewma: dict[str, float] = {}
        self.values: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=KEEP))
        self.mix: dict[str, Counter] = defaultdict(Counter)
        self.last_ts: dict[str, int] = {}

    # ------------------------------------------------------------------ learn
    def observe(self, command: Command) -> None:
        """Record a command *after* it has been judged, so it never explains itself."""
        source = command.source
        last = self.last_ts.get(source)
        if last is not None and command.ts >= last:
            interval = (command.ts - last) / 1000.0
            self.intervals[source].append(interval)
            self.ewma[source] = interval if source not in self.ewma else \
                (1 - EWMA_ALPHA) * self.ewma[source] + EWMA_ALPHA * interval
        self.last_ts[source] = command.ts
        self.mix[source][command.action] += 1
        if command.value is not None:
            try:
                value = float(command.value)
            except (TypeError, ValueError):
                return                                   # names (programs, sections) have no range
            self.values[command.action].append(value)

    # ------------------------------------------------------------------ judge
    def cadence_deviation(self, command: Command) -> Optional[str]:
        history = self.intervals.get(command.source)
        last = self.last_ts.get(command.source)
        if not history or len(history) < MIN_SAMPLES or last is None:
            return None
        interval = (command.ts - last) / 1000.0
        typical = _percentile(list(history), 0.5)
        floor = _percentile(list(history), 0.05)
        ewma = self.ewma.get(command.source, typical)
        if interval < 2.0 and interval < floor / 4 and min(typical, ewma) > 8.0:
            return (f"'{command.source}' usually commands every {typical:.0f} s (EWMA {ewma:.0f} s); "
                    f"this one came {interval:.1f} s after the last")
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

    def mix_deviation(self, command: Command) -> Optional[str]:
        seen = self.mix.get(command.source)
        if not seen or sum(seen.values()) < MIN_SAMPLES:
            return None
        if seen.get(command.action, 0) == 0:
            usual = ", ".join(a for a, _ in seen.most_common(3))
            return (f"first {command.action} ever from '{command.source}' in {sum(seen.values())} commands "
                    f"(usually {usual})")
        return None

    # ------------------------------------------------------------------ export
    def summary(self) -> dict[str, Any]:
        """What was learned, next to what is configured, for the console and RESULTS.md."""
        sources = {}
        for source, history in self.intervals.items():
            values = list(history)
            total = sum(self.mix.get(source, Counter()).values())
            sources[source] = {
                "samples": len(values), "ready": len(values) >= MIN_SAMPLES,
                "median_interval_s": round(_percentile(values, 0.5), 1) if values else None,
                "floor_interval_s": round(_percentile(values, 0.05), 1) if values else None,
                "ewma_interval_s": round(self.ewma.get(source, 0.0), 1) if source in self.ewma else None,
                "mix": {a: round(n / total, 2) for a, n in self.mix.get(source, Counter()).most_common(6)} if total else {},
            }
        actions = {}
        for action, history in self.values.items():
            values = list(history)
            if not values:
                continue
            actions[action] = {"samples": len(values), "ready": len(values) >= MIN_SAMPLES,
                               "p05": round(_percentile(values, 0.05), 2) if values else None,
                               "p95": round(_percentile(values, 0.95), 2) if values else None}
        return {
            "min_samples": MIN_SAMPLES,
            "sources": sources, "actions": actions,
            "configured": {
                "cadence": f"{config.RAPID_COMMAND_COUNT} actuator commands in {config.RAPID_WINDOW_S:.0f} s, "
                           f"{config.BURST_COMMAND_COUNT} in {config.BURST_WINDOW_S:.0f} s",
                "setpoint": f"{config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} %, trim ±{config.SETPOINT_NORMAL_DELTA:.0f} %",
                "avc_target": f"{config.GRID_AVC_TARGET_MIN_KV:.2f}–{config.GRID_AVC_TARGET_MAX_KV:.2f} kV, "
                              f"trim ±{config.GRID_AVC_NORMAL_DELTA_KV:.2f} kV",
                "range": (f"{config.GRID_AVC_TARGET_MIN_KV:.2f}–{config.GRID_AVC_TARGET_MAX_KV:.2f} kV, "
                          f"trim ±{config.GRID_AVC_NORMAL_DELTA_KV:.2f} kV") if process.active_id() == "grid"
                         else f"{config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} %, trim ±{config.SETPOINT_NORMAL_DELTA:.0f} %",
                "mix": "none — every keyed source may issue every action",
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {"intervals": {k: list(v) for k, v in self.intervals.items()},
                "ewma": dict(self.ewma),
                "values": {k: list(v) for k, v in self.values.items()},
                "mix": {k: dict(v) for k, v in self.mix.items()},
                "last_ts": dict(self.last_ts)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Baseline":
        b = cls()
        for k, v in (data.get("intervals") or {}).items():
            b.intervals[k].extend(v)
        b.ewma.update({k: float(v) for k, v in (data.get("ewma") or {}).items()})
        for k, v in (data.get("values") or {}).items():
            b.values[k].extend(v)
        for k, v in (data.get("mix") or {}).items():
            b.mix[k].update(v)
        b.last_ts.update(data.get("last_ts") or {})
        return b
