"""Risk scoring and the engineer-facing advisory (PRD sections 24-26).

Scoring is deterministic and additive on purpose: every point in the score is
traceable to a named finding, so an engineer can argue with the machine.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import config, process
from ..models import Alert, Command, Finding

# Narrative per dominant rule: what happened, why it matters, what to verify.
RULE_NARRATIVE: dict[str, dict[str, str]] = {
    "SEQ-001": {
        "summary": "MOV-201 close conflicts with running mainline pump",
        "equipment": "MOV-201 / Mainline pump P-101",
        "why": "Slamming MOV-201 shut dead-heads P-101: flow stops and a surge drives pressure to "
               "{shutoff:.1f} bar shut-off against the {limit:.1f} bar segment MAOP — seal failure and a hydrocarbon release.",
        "recommendation": "Stop P-101 and confirm zero flow before closing MOV-201; if nobody owns "
                          "this command, treat the source as compromised.",
    },
    "STATE-002": {
        "summary": "P-101 start requested with MOV-201 closed",
        "equipment": "Mainline pump P-101 / MOV-201",
        "why": "MOV-201 is closed, so P-101 starts dead-headed and surges to shut-off head within seconds.",
        "recommendation": "Open MOV-201 and line up the segment before starting; confirm who requested the start.",
    },
    "STATE-003": {
        "summary": "P-101 start requested with insufficient suction",
        "equipment": "Tank farm T-101 / Mainline pump P-101",
        "why": "Tank farm level is below P-101's minimum suction: the pump cavitates and loses flow.",
        "recommendation": "Restore tank farm level above the minimum and confirm ESD-301 is open before starting.",
    },
    "STATE-004": {
        "summary": "ESD-301 closed while P-101 is drawing the tank farm down",
        "equipment": "ESD-301 / Tank farm T-101",
        "why": "With ESD-301 closed P-101 drains the tank farm to the low limit and loses suction.",
        "recommendation": "Reduce or stop P-101 before closing ESD-301.",
    },
    "ROC-001": {
        "summary": "Unusually large setpoint change",
        "equipment": "Station RTU / Tank farm T-101",
        "why": "Setpoints are trimmed in small steps; one large step drives the process toward a limit "
               "faster than the loop or the operator can react.",
        "recommendation": "Confirm the target with the shift engineer; ramp it in steps if it is genuine.",
    },
    "ROC-002": {
        "summary": "Setpoint is being drifted toward the edge of the safe band",
        "equipment": "Station RTU / Tank farm T-101",
        "why": "Each trim looks routine, but the cumulative movement is heading for the operating limit — "
               "how an attacker walks a process out of range without one command looking wrong.",
        "recommendation": "Compare with the shift log; if nobody owns the trajectory, restore the logged "
                          "setpoint and trace the source.",
    },
    "CMD-001": {
        "summary": "Replayed or out-of-time command",
        "equipment": "Command path",
        "why": "The timestamp or message id has been seen before: recorded traffic is being replayed and "
               "the controller will obey it.",
        "recommendation": "Confirm with the control room that this was issued now; if not, treat the "
                          "command path as compromised.",
    },
    "ENV-001": {
        "summary": "Requested setpoint is outside the safe operating envelope",
        "equipment": "Tank farm T-101",
        "why": "The value sits outside the documented band, leaving no margin to the level protection.",
        "recommendation": "Reject or correct the setpoint and verify who issued it.",
    },
    "ENV-002": {
        "summary": "Command issued while the process is already near an operating limit",
        "equipment": "Discharge pressure PT-201",
        "why": "Pressure is already near its limit; adding load removes the remaining margin to a trip.",
        "recommendation": "Bring pressure back inside the envelope before making further changes.",
    },
    "SEQ-002": {
        "summary": "Abnormally rapid actuator command sequence",
        "equipment": "Controller / Actuators",
        "why": "Commands are arriving faster than an operator issues them — the signature of a script.",
        "recommendation": "Identify the source of the burst before allowing further commands.",
    },
    "SEQ-003": {
        "summary": "Actuator is being cycled repeatedly",
        "equipment": "Actuator",
        "why": "The same actuator was driven both ways within seconds: wear and pressure transients.",
        "recommendation": "Check for a fighting loop, a stuck HMI or an external source repeating commands.",
    },
    "SEQ-004": {
        "summary": "Known unsafe command pattern observed",
        "equipment": "Process",
        "why": "The recent command sequence matches a known multi-step path into an unsafe configuration.",
        "recommendation": "Review the full sequence and its source before allowing further changes.",
    },
    "TEL-001": {
        "summary": "Telemetry is stale — displayed process state may be wrong",
        "equipment": "Telemetry path",
        "why": "The newest frame is older than the freshness limit; the HMI may not show the live plant.",
        "recommendation": "Check the telemetry link and confirm plant state independently before commanding.",
    },
    "TEL-002": {
        "summary": "Telemetry replay suspected — sequence number is not advancing",
        "equipment": "Telemetry path",
        "why": "Repeated identical frames mean recorded telemetry is being replayed while the real plant moves.",
        "recommendation": "Treat the display as untrusted; verify the plant locally and inspect the telemetry path.",
    },
    "TEL-003": {
        "summary": "Telemetry sequence went backwards — frames are being injected",
        "equipment": "Telemetry path",
        "why": "Sequence numbers must only increase; a regression means frames from another publisher.",
        "recommendation": "Isolate the telemetry path and verify the controller is the only publisher.",
    },
    "PHY-001": {
        "summary": "Reported process values do not match the physics",
        "equipment": "Instrumentation",
        "why": "Equipment states and measured flow disagree with the model: a failed instrument or manipulated values.",
        "recommendation": "Cross-check the flow transmitter against pressure and level trend before trusting it.",
    },
    "SRC-001": {
        "summary": "Command from an unexpected source",
        "equipment": "Command source",
        "why": "Not a recognised control-room source for this mode — a common sign of a compromised workstation.",
        "recommendation": "Confirm which workstation issued the command and whether that access is expected.",
    },
    "BASE-001": {
        "summary": "Command departs from this plant's learned normal",
        "equipment": "Command source",
        "why": "The command is valid, but its timing or value is unlike anything this source or action "
               "has done before on this plant.",
        "recommendation": "Confirm the change with whoever operates from this source.",
    },
    "CTX-001": {
        "summary": "Maintenance activity — expected in context",
        "equipment": "Controller",
        "why": "The plant is in maintenance mode, where isolation sequences are normal work.",
        "recommendation": "Confirm the work order covers this equipment.",
    },
}

DEFAULT_NARRATIVE = {
    "summary": "Command is inconsistent with the current process state",
    "equipment": "Process",
    "why": "The command does not match the plant's current state and context.",
    "recommendation": "Verify operator intent and the current line-up before proceeding.",
}


def score(findings: list[Finding]) -> int:
    """Additive score, clamped to 0-100 (PRD section 24)."""
    total = sum(f.weight for f in findings)
    return max(0, min(100, total))


def dominant_rule(findings: list[Finding]) -> Optional[str]:
    """The rule contributing the most risk — it names the alert."""
    positive = [f for f in findings if f.weight > 0]
    if not positive:
        return None
    totals: dict[str, int] = {}
    for finding in positive:
        totals[finding.rule] = totals.get(finding.rule, 0) + finding.weight
    return max(totals.items(), key=lambda kv: kv[1])[0]


class _Vars(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def narratives() -> dict[str, dict[str, str]]:
    """Shared integrity narratives, overlaid with the active process's own."""
    return {**RULE_NARRATIVE, **process.domain().NARRATIVES}


def _fmt(template: str) -> str:
    return template.format_map(_Vars(process.domain().NARRATIVE_VARS))


def build_alert(findings: list[Finding], state_dict: dict[str, Any],
                command: Optional[Command] = None,
                context: str = "AUTO",
                confidence: str = "HIGH", uncertainty: str = "") -> Optional[Alert]:
    """Turn findings into the explainable advisory, or None if nothing to say."""
    if not findings:
        return None
    total = score(findings)
    rule = dominant_rule(findings)
    if rule is None:
        return None

    mitigations = [f for f in findings if f.weight < 0]
    raw = sum(f.weight for f in findings if f.weight > 0)

    if total < config.ALERT_MIN_SCORE:
        # Context cancelled the risk. Say so explicitly when it would otherwise have
        # been a real alert — that is the point of PRD section 36 — and stay silent
        # when there was nothing to suppress in the first place.
        if not (mitigations and raw >= config.SEVERITY_BANDS[2][0]):
            return None
        rule = "CTX-001"

    narrative = narratives().get(rule, DEFAULT_NARRATIVE)
    level = config.severity_for(total)

    why_parts = [_fmt(narrative["why"])]
    if mitigations:
        why_parts.append("Mitigating context: " + "; ".join(m.detail for m in mitigations) + ".")
    if rule == "CTX-001" and raw:
        why_parts.append(f"Without this context it would have scored {raw}/100 ({config.severity_for(raw)}).")
    recommendation = _fmt(narrative["recommendation"])
    if level in ("LOW",) and mitigations:
        recommendation = "Advisory only in this context. " + recommendation

    return Alert(
        level=level,
        rule=rule,
        score=total,
        summary=narrative["summary"],
        equipment=narrative["equipment"],
        why=" ".join(why_parts),
        recommendation=recommendation,
        command_id=command.id if command else None,
        command=command.to_dict() if command else None,
        state=state_dict,
        findings=[f.to_dict() for f in findings],
        context=context,
        suppressed_score=raw if rule == "CTX-001" and raw > total else 0,
        confidence=confidence,
        uncertainty=uncertainty,
    )
