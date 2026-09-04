"""Risk scoring and the engineer-facing advisory (PRD sections 24-26).

Scoring is deterministic and additive on purpose: every point in the score is
traceable to a named finding, so an engineer can argue with the machine.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import config
from ..models import Alert, Command, Finding

# Narrative per dominant rule: what happened, why it matters, what to verify.
RULE_NARRATIVE: dict[str, dict[str, str]] = {
    "SEQ-001": {
        "summary": "Outlet valve close conflicts with running pump",
        "equipment": "Outlet valve V-102 / Pump P-101",
        "why": ("Closing the outlet while the pump is energised removes the only discharge path. "
                "The pump then operates dead-headed: flow collapses to zero, all shaft power turns "
                "into heat and head, and discharge pressure climbs toward pump shut-off "
                "({shutoff:.1f} bar) against a {limit:.1f} bar line limit. Continued operation risks "
                "seal failure, casing overpressure and pump damage."),
        "recommendation": ("Verify operator intent for this valve command. The safe sequence is to stop "
                           "the pump first, confirm zero flow, then isolate the outlet. If no operator "
                           "owns this command, treat the command source as suspect and check who has "
                           "write access to the controller."),
    },
    "STATE-002": {
        "summary": "Pump start requested with the discharge path closed",
        "equipment": "Pump P-101 / Outlet valve V-102",
        "why": ("The outlet valve is closed, so starting the pump immediately dead-heads it. There is "
                "no path for the delivered liquid and pressure rises to shut-off head within seconds."),
        "recommendation": ("Confirm the outlet valve is open and the discharge line is lined up before "
                           "starting the pump. Verify whether this start was requested by a control-room "
                           "operator."),
    },
    "STATE-003": {
        "summary": "Pump start requested with insufficient suction level",
        "equipment": "Tank T-101 / Pump P-101",
        "why": ("Tank level is below the minimum required at the pump suction. Starting the pump draws "
                "vapour rather than liquid, causing cavitation, loss of flow and bearing/seal damage."),
        "recommendation": ("Restore tank level above the minimum before starting the pump, and confirm "
                           "the inlet supply is lined up."),
    },
    "STATE-004": {
        "summary": "Inlet isolation while the pump is drawing the tank down",
        "equipment": "Inlet valve V-101 / Tank T-101",
        "why": ("Closing the make-up supply while the pump keeps delivering flow drains the tank toward "
                "the low limit, ending in loss of suction and an unplanned pump trip."),
        "recommendation": ("Confirm the pump duty is reduced or stopped before isolating the inlet, and "
                           "check the projected time to the low-level limit."),
    },
    "ROC-001": {
        "summary": "Unusually large setpoint change",
        "equipment": "Controller / Tank T-101",
        "why": ("Level setpoints are normally trimmed in small steps. A single large step is either an "
                "operator error or an attempt to drive the process toward a limit faster than the "
                "control loop and the operator can react to."),
        "recommendation": ("Confirm the target level with the shift engineer. If the change is genuinely "
                           "required, ramp it in steps and watch level and pressure between steps."),
    },
    "ROC-002": {
        "summary": "Setpoint is being drifted toward the edge of the safe band",
        "equipment": "Controller / Tank T-101",
        "why": ("Each individual setpoint change is small enough to look like a routine trim, but the "
                "cumulative movement is far larger than an operator would make and is heading for the "
                "operating limit. Moving a setpoint a little at a time is how an attacker walks a process "
                "out of its safe range without any single command looking wrong."),
        "recommendation": ("Compare the current setpoint with the shift log and the value at the start of "
                           "the window. If nobody owns the trajectory, return the setpoint to its logged "
                           "value and identify the source of the trim commands."),
    },
    "CMD-001": {
        "summary": "Replayed or out-of-time command",
        "equipment": "Command path",
        "why": ("The command carries a timestamp or message id that has already been seen. Recorded "
                "traffic replayed to the controller is protocol-valid and will be obeyed, even though "
                "nobody issued it now."),
        "recommendation": ("Confirm with the control room whether this command was issued just now. If "
                           "not, treat the command path as compromised and check what else the same "
                           "source has sent."),
    },
    "ENV-001": {
        "summary": "Requested setpoint is outside the safe operating envelope",
        "equipment": "Tank T-101",
        "why": ("The requested value sits outside the documented operating band for the tank. Operating "
                "there leaves no margin to the high-level or low-level protection."),
        "recommendation": ("Reject or correct the setpoint. Verify who issued it and whether the operating "
                           "band has been changed without authorisation."),
    },
    "ENV-002": {
        "summary": "Command issued while the process is already near an operating limit",
        "equipment": "Pressure transmitter PT-101",
        "why": ("The process is already close to (or beyond) its pressure limit. Any command that adds "
                "load in this state reduces the remaining margin to a protective trip."),
        "recommendation": ("Bring pressure back inside the envelope before making further changes."),
    },
    "SEQ-002": {
        "summary": "Abnormally rapid actuator command sequence",
        "equipment": "Controller / Actuators",
        "why": ("Actuator commands are arriving faster than a control-room operator would issue them. "
                "Rapid sequencing is characteristic of scripted or automated command injection, and it "
                "gives the process no time to settle between changes."),
        "recommendation": ("Identify the source of the command burst and confirm whether an automation "
                           "script or an operator is driving the plant."),
    },
    "SEQ-003": {
        "summary": "Actuator is being cycled repeatedly",
        "equipment": "Actuator",
        "why": ("The same actuator has been driven in both directions inside a few seconds. Cycling "
                "wears the actuator and produces pressure transients in the line."),
        "recommendation": ("Check for a fighting control loop, a stuck HMI, or an external source "
                           "repeating commands."),
    },
    "SEQ-004": {
        "summary": "Known unsafe command pattern observed",
        "equipment": "Process",
        "why": ("The recent command history matches a multi-step pattern that drives the plant into an "
                "unsafe configuration, even though each individual command is valid."),
        "recommendation": ("Review the full command sequence and its source before allowing further "
                           "changes."),
    },
    "TEL-001": {
        "summary": "Telemetry is stale — displayed process state may be wrong",
        "equipment": "Telemetry path",
        "why": ("Decisions are only as good as the state they are based on. The newest telemetry frame is "
                "older than the freshness limit, so the values on the HMI may not reflect the live plant."),
        "recommendation": ("Do not act on the displayed values. Check the telemetry link and the "
                           "controller, and confirm plant state by an independent means before "
                           "commanding anything."),
    },
    "TEL-002": {
        "summary": "Telemetry replay suspected — sequence number is not advancing",
        "equipment": "Telemetry path",
        "why": ("Repeated frames with an identical sequence number indicate recorded telemetry is being "
                "replayed. An attacker can hold the HMI on a comfortable-looking state while the real "
                "plant moves somewhere else."),
        "recommendation": ("Treat the displayed state as untrusted. Verify the plant locally, and "
                           "investigate the telemetry path between the controller and the monitoring "
                           "network."),
    },
    "TEL-003": {
        "summary": "Telemetry sequence went backwards — frames are being injected",
        "equipment": "Telemetry path",
        "why": ("Sequence numbers must increase monotonically. A regression means frames are being "
                "reordered or injected by something other than the controller."),
        "recommendation": ("Isolate the telemetry path and verify the controller is the only publisher."),
    },
    "PHY-001": {
        "summary": "Reported process values do not match the physics",
        "equipment": "Instrumentation",
        "why": ("The equipment states and the measured flow are inconsistent with the process model. "
                "Either an instrument has failed, or the reported values are being manipulated."),
        "recommendation": ("Cross-check the flow transmitter against pump discharge pressure and tank "
                           "level trend before trusting the reading."),
    },
    "SRC-001": {
        "summary": "Command from an unexpected source",
        "equipment": "Command source",
        "why": ("The command did not come from a recognised control-room source for the current operating "
                "mode. Valid credentials on an unexpected host are a common signature of a compromised "
                "engineering workstation."),
        "recommendation": ("Confirm which workstation issued the command and whether that access is "
                           "expected right now."),
    },
    "CTX-001": {
        "summary": "Maintenance activity — expected in context",
        "equipment": "Controller",
        "why": ("The plant is in maintenance mode, where isolation sequences are normal work."),
        "recommendation": ("No action needed beyond confirming the maintenance work order covers this "
                           "equipment."),
    },
}

DEFAULT_NARRATIVE = {
    "summary": "Command is inconsistent with the current process state",
    "equipment": "Process",
    "why": "The command does not match the plant's current physical state and operating context.",
    "recommendation": "Verify operator intent and the current plant line-up before proceeding.",
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


def _fmt(template: str) -> str:
    return template.format(shutoff=config.PUMP_SHUTOFF_HEAD_BAR, limit=config.PRESSURE_MAX_BAR)


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

    narrative = RULE_NARRATIVE.get(rule, DEFAULT_NARRATIVE)
    level = config.severity_for(total)

    why_parts = [_fmt(narrative["why"])]
    if mitigations:
        why_parts.append("Mitigating context: " + "; ".join(m.detail for m in mitigations) + ".")
    if rule == "CTX-001" and raw:
        why_parts.append(f"The same command outside this context would have scored {raw}/100 "
                         f"({config.severity_for(raw)}).")
    recommendation = _fmt(narrative["recommendation"])
    if level in ("LOW",) and mitigations:
        recommendation = ("Context reduces this to an advisory. " + recommendation)

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
