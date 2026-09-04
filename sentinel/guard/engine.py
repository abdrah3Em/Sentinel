"""The Command Guard itself: observe, evaluate, explain.

The guard has no write path to the plant.  Its only outputs are advisories for
a human engineer (PRD sections 41 and FR-011).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Deque, Optional

from .. import config
from ..models import Alert, Command, Event, Telemetry, now_ms
from . import risk, rules
from .state import ProcessState

CONSEQUENTIAL = {"pump_start", "pump_stop", "outlet_open", "outlet_close",
                 "inlet_open", "inlet_close", "setpoint"}

PROCESS_ALERT_REPEAT_S = 30.0     # how often a persisting condition re-alerts


class CommandGuard:
    def __init__(self, on_alert: Optional[Callable[[Alert], None]] = None,
                 on_assessment: Optional[Callable[[dict], None]] = None) -> None:
        self.state = ProcessState()
        self.on_alert = on_alert or (lambda alert: None)
        self.on_assessment = on_assessment or (lambda assessment: None)
        self.alerts: Deque[Alert] = deque(maxlen=500)
        self.assessments: Deque[dict] = deque(maxlen=500)
        self._process_alerted: dict[str, tuple[float, str]] = {}   # rule -> (when, level)
        self.commands_seen = 0
        self.started = time.time()

    # ------------------------------------------------------------------ inputs
    def observe_telemetry(self, payload: dict[str, Any], now: float | None = None) -> None:
        self.state.update(Telemetry.from_dict(payload), arrival=now or time.time())

    def observe_command(self, payload: dict[str, Any], now: float | None = None) -> Optional[Alert]:
        now = now or time.time()
        command = Command.from_dict(payload)
        self.commands_seen += 1

        # History first: sequence rules must see the command they are judging.
        self.state.history.add(command)

        # Mode changes are evaluated too — a replayed maintenance_off is still a replay.
        findings = rules.evaluate_command(self.state, command, now)
        total = risk.score(findings)
        context = self._context_label()
        confidence, uncertainty = self.confidence(now)
        alert = risk.build_alert(findings, self.state.to_dict(now), command, context,
                                 confidence, uncertainty)

        verdict = alert.level if alert else "NORMAL"
        quiet = ("Consistent with current process state" if command.action in CONSEQUENTIAL
                 else f"{command.action} is an operational mode change")
        self._record_assessment(command, findings, total, verdict,
                                alert.summary if alert else quiet, now)
        if alert:
            self._emit(alert, now)
        return alert

    def evaluate_process(self, now: float | None = None) -> Optional[Alert]:
        """Periodic check that does not need a command: integrity and physics."""
        now = now or time.time()
        if not self.state.known and self.state.integrity.last_seq is None:
            return None
        findings = rules.evaluate_process(self.state, now)
        active = {f.rule for f in findings}
        for rule_id in list(self._process_alerted):
            if rule_id not in active:
                del self._process_alerted[rule_id]
        if not findings:
            return None

        confidence, uncertainty = self.confidence(now)
        alert = risk.build_alert(findings, self.state.to_dict(now), None, self._context_label(),
                                 confidence, uncertainty)
        if alert is None:
            return None
        last_time, last_level = self._process_alerted.get(alert.rule, (0.0, "LOW"))
        escalated = config.SEVERITY_ORDER[alert.level] > config.SEVERITY_ORDER[last_level]
        # Re-alert on a persisting condition only every so often — unless it has
        # got worse, in which case the engineer hears about it immediately.
        if now - last_time < PROCESS_ALERT_REPEAT_S and not escalated:
            return None
        self._process_alerted[alert.rule] = (now, alert.level)
        self._emit(alert, now)
        return alert

    def confidence(self, now: float | None = None) -> tuple[str, str]:
        """What the system does when it is unsure: say so, never block, ask for verification."""
        now = now or time.time()
        integ = self.state.integrity
        if integ.last_seq is None:
            return "LOW", ("No telemetry has been received, so this could not be checked against the "
                           "live process. Sentinel does not block: the advisory is raised at low "
                           "confidence and the plant state must be confirmed by other means.")
        age = max(integ.age_s(now), integ.gap_s(now))
        if not integ.trusted(now):
            cause = ("replay-suspect" if integ.repeat_count >= config.REPLAY_REPEAT_COUNT
                     or integ.regressions else "stale")
            detail = f"age {age:.0f} s, seq {integ.last_seq}"
            if integ.repeat_count:
                detail += f" repeated ×{integ.repeat_count}"
            return "LOW", (f"Telemetry is {cause} ({detail}), so this assessment is based on a state "
                           "that may no longer be real. Sentinel does not block; it raises the advisory "
                           "anyway, treats the displayed state as unverified, and asks for the plant to "
                           "be confirmed locally before anyone acts.")
        if self.state.residual_duration(now) >= config.PHYSICS_SETTLE_S:
            return "REDUCED", ("Reported flow disagrees with the physics model, so the state used here "
                               "may be wrong. Sentinel does not block — cross-check the instruments "
                               "before acting on this advisory.")
        return "HIGH", (f"Based on fresh, sequence-consistent telemetry (age {age:.1f} s, seq "
                        f"{integ.last_seq}). Sentinel advises only; it has not acted on the plant.")

    def reset(self) -> None:
        """Demo housekeeping: forget history so a fresh run starts clean."""
        self.state = ProcessState()
        self.alerts.clear()
        self.assessments.clear()
        self._process_alerted.clear()
        self.commands_seen = 0

    # ----------------------------------------------------------------- outputs
    def _emit(self, alert: Alert, now: float | None = None) -> None:
        # Stamp with the clock the engine was given, so replay/offline analysis
        # and the live path agree on time.
        if now is not None:
            alert.ts = int(now * 1000)
        self.alerts.append(alert)
        self.on_alert(alert)

    def _record_assessment(self, command: Command, findings: list, total: int,
                           verdict: str, summary: str, now: float) -> None:
        assessment = {
            "ts": int(now * 1000),
            "command": command.to_dict(),
            "score": total,
            "verdict": verdict,
            "summary": summary,
            "findings": [f.to_dict() for f in findings],
            "context": self._context_label(),
            "telemetry_trusted": self.state.integrity.trusted(now),
            "confidence": self.confidence(now)[0],
        }
        self.assessments.append(assessment)
        self.on_assessment(assessment)

    def _context_label(self) -> str:
        t = self.state.telemetry
        if t is None:
            return "UNKNOWN"
        return "MAINTENANCE" if (t.maintenance or t.mode == "MAINTENANCE") else t.mode

    # ------------------------------------------------------------------ status
    def status(self, now: float | None = None, window_s: float = 60.0) -> dict[str, Any]:
        now = now or time.time()
        cutoff_ms = int((now - window_s) * 1000)
        recent = [a for a in self.alerts if a.ts >= cutoff_ms]
        top = max(recent, key=lambda a: a.score, default=None)
        level = top.level if top else "NORMAL"
        return {
            "status": "NORMAL" if top is None else f"{top.level}_RISK",
            "level": level,
            "risk_score": top.score if top else 0,
            "headline": top.summary if top else "No unsafe command detected",
            "alert_id": top.id if top else None,
            "telemetry_fresh": self.state.integrity.age_s(now) < config.TELEMETRY_STALE_S,
            "telemetry_trusted": self.state.integrity.trusted(now),
            "telemetry_age_s": (round(min(999.0, max(self.state.integrity.age_s(now),
                                                     self.state.integrity.gap_s(now))), 1)
                                if self.state.integrity.last_seq is not None else None),
            "telemetry_seq": self.state.integrity.last_seq,
            "telemetry_repeats": self.state.integrity.repeat_count,
            "context": self._context_label(),
            "commands_seen": self.commands_seen,
            "alerts_total": len(self.alerts),
            "uptime_s": round(now - self.started, 1),
            "recent_alerts": len(recent),
        }

    def stats(self) -> dict[str, Any]:
        by_level: dict[str, int] = {}
        by_rule: dict[str, int] = {}
        for alert in self.alerts:
            by_level[alert.level] = by_level.get(alert.level, 0) + 1
            by_rule[alert.rule] = by_rule.get(alert.rule, 0) + 1
        return {
            "commands_seen": self.commands_seen,
            "alerts_total": len(self.alerts),
            "by_level": by_level,
            "by_rule": by_rule,
            "telemetry_frames": self.state.integrity.frames,
            "replay_frames": self.state.integrity.replay_frames,
        }
