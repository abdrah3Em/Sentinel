"""The detection layers (PRD sections 16-23).

Each rule is a small pure function over a RuleContext that returns Findings.
A Finding is one weighted, human-readable reason.  Nothing here decides the
severity — that is the risk engine's job — and nothing here touches the plant.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .. import config
from ..models import Command, Finding
from .state import ProcessState

W = config.WEIGHTS

DISCHARGE_CLOSE = {"outlet_close"}
ACTUATOR_ACTIONS = {"pump_start", "pump_stop", "outlet_open", "outlet_close",
                    "inlet_open", "inlet_close"}
MAINTENANCE_EXPECTED = {"pump_stop", "outlet_close", "inlet_close", "pump_start", "outlet_open", "inlet_open"}


@dataclass
class RuleContext:
    state: ProcessState
    command: Optional[Command] = None
    now: float = 0.0          # wall clock seconds

    def __post_init__(self) -> None:
        if not self.now:
            self.now = time.time()

    @property
    def now_ms(self) -> int:
        return int(self.now * 1000)

    @property
    def t(self):
        return self.state.telemetry

    @property
    def maintenance(self) -> bool:
        return bool(self.t and (self.t.maintenance or self.t.mode == "MAINTENANCE"))

    @property
    def flow_active(self) -> bool:
        return bool(self.t and self.t.flow > config.FLOW_ACTIVE_LPM)

    @property
    def verification(self) -> dict:
        """The signing verdict the engine recorded for this command (see sentinel/signing.py)."""
        if not self.command:
            return {}
        return self.state.verifications.get(self.command.id, {})

    def pump_stop_pending(self) -> bool:
        """A pump stop commanded moments ago — the correct way to isolate a line."""
        return self.state.history.seconds_since("pump_stop", self.now_ms) < 8.0


CommandRule = Callable[[RuleContext], list[Finding]]


# ---------------------------------------------------------------------------
# Layer 1 — state-based detection
# ---------------------------------------------------------------------------
def rule_discharge_closure(ctx: RuleContext) -> list[Finding]:
    """SEQ-001 — close the discharge path while the pump is delivering flow.

    Rule id kept as SEQ-001 to match the alert example in the PRD.  This is the
    flagship detection: a perfectly valid valve command that dead-heads a
    running pump.
    """
    if not ctx.command or ctx.command.action not in DISCHARGE_CLOSE or not ctx.t:
        return []
    if not ctx.t.pump:
        return []
    findings = [
        Finding("SEQ-001", "state", W["PUMP_RUNNING"],
                f"Pump is running · {ctx.t.pump_runtime_s:.0f} s", "Mainline pump P-101"),
        Finding("SEQ-001", "state", W["CLOSING_DISCHARGE"],
                "Closes the only discharge path with the pump energised",
                "MOV-201"),
    ]
    if ctx.flow_active:
        findings.append(Finding("SEQ-001", "state", W["FLOW_ACTIVE"],
                                f"Flow {ctx.t.flow:.0f} m³/h through the outlet",
                                "Mainline flow FT-201"))
    if not ctx.maintenance:
        findings.append(Finding("SEQ-001", "context", W["NOT_MAINTENANCE"],
                                f"{ctx.t.mode} mode, not maintenance", "Controller"))
    if ctx.pump_stop_pending():
        findings.append(Finding("SEQ-001", "context", W["PUMP_STOPPING"],
                                "Pump stop commanded seconds earlier — isolating in order",
                                "Mainline pump P-101"))
    return findings


def rule_deadhead_start(ctx: RuleContext) -> list[Finding]:
    """STATE-002 — start the pump into a closed discharge path."""
    if not ctx.command or ctx.command.action != "pump_start" or not ctx.t:
        return []
    if ctx.t.outlet_valve:
        return []
    findings = [
        Finding("STATE-002", "state", W["DEADHEAD_START"],
                "MOV-201 closed — no discharge path",
                "Mainline pump P-101 / MOV-201"),
    ]
    if not ctx.maintenance:
        findings.append(Finding("STATE-002", "context", W["NOT_MAINTENANCE"],
                                f"{ctx.t.mode} mode, not maintenance", "Controller"))
    return findings


def rule_dry_run(ctx: RuleContext) -> list[Finding]:
    """STATE-003 — start or keep pumping with no liquid at the suction."""
    if not ctx.command or ctx.command.action != "pump_start" or not ctx.t:
        return []
    if ctx.t.tank_level >= config.LEVEL_MIN_PCT:
        return []
    weight = W["DRY_RUN"] if ctx.t.tank_level < config.PUMP_MIN_SUCTION_LEVEL else W["SUCTION_STARVED"]
    return [Finding("STATE-003", "state", weight,
                    f"Level {ctx.t.tank_level:.0f} %, below the {config.LEVEL_MIN_PCT:.0f} % suction minimum",
                    "Tank farm T-101 / Mainline pump P-101")]


def rule_suction_isolation(ctx: RuleContext) -> list[Finding]:
    """STATE-004 — close ESD-301 while P-101 is drawing the tank farm down."""
    if not ctx.command or ctx.command.action != "inlet_close" or not ctx.t:
        return []
    if not ctx.t.pump or not ctx.flow_active:
        return []
    headroom = ctx.t.tank_level - config.LEVEL_MIN_PCT
    if headroom > 25:
        return []
    minutes = max(0.1, (headroom / 100.0 * config.TANK_CAPACITY_L) / max(1.0, ctx.t.flow))
    return [Finding("STATE-004", "state", W["SUCTION_STARVED"],
                    f"ESD-301 closed at {ctx.t.flow:.0f} m³/h draw — low limit in about {minutes:.1f} min",
                    "ESD-301 / Tank farm T-101")]


# ---------------------------------------------------------------------------
# Layer 4 — rate of change / setpoint
# ---------------------------------------------------------------------------
def rule_setpoint_change(ctx: RuleContext) -> list[Finding]:
    """ROC-001 — setpoint steps far outside normal operator behaviour."""
    if not ctx.command or ctx.command.action != "setpoint" or ctx.command.value is None or not ctx.t:
        return []
    requested = float(ctx.command.value)
    delta = requested - ctx.t.setpoint
    findings: list[Finding] = []
    if abs(delta) > config.SETPOINT_NORMAL_DELTA:
        findings.append(Finding("ROC-001", "rate", W["SETPOINT_LARGE"],
                                f"Step {delta:+.0f} % vs normal ±{config.SETPOINT_NORMAL_DELTA:.0f} % trim", "Controller"))
    if abs(delta) > config.SETPOINT_LARGE_DELTA:
        findings.append(Finding("ROC-001", "rate", W["SETPOINT_EXTREME"],
                                f"More than {config.SETPOINT_LARGE_DELTA:.0f} % in one command", "Controller"))
    if not (config.SETPOINT_MIN <= requested <= config.SETPOINT_MAX):
        findings.append(Finding("ENV-001", "envelope", W["SETPOINT_OUT_OF_RANGE"],
                                f"{requested:.0f} % is outside the {config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} % band",
                                "Tank farm T-101"))
    return findings


def rule_setpoint_drift(ctx: RuleContext) -> list[Finding]:
    """ROC-002 — the setpoint moved a little at a time until it left the safe range.

    Each step passes ROC-001; only the trajectory gives the attack away.
    """
    if not ctx.command or ctx.command.action != "setpoint" or ctx.command.value is None or not ctx.t:
        return []
    steps = ctx.state.history.recent(config.DRIFT_WINDOW_S, ctx.now_ms, {"setpoint"})
    if len(steps) < config.DRIFT_MIN_STEPS:
        return []
    baseline = ctx.state.setpoint_baseline(ctx.now_ms, config.DRIFT_WINDOW_S)
    if baseline is None:
        baseline = ctx.t.setpoint
    requested = float(ctx.command.value)
    net = requested - baseline
    if abs(net) <= config.SETPOINT_LARGE_DELTA:
        return []
    elapsed_min = max(0.05, (ctx.now_ms - steps[0].ts) / 60000.0)
    findings = [Finding("ROC-002", "rate", W["SETPOINT_DRIFT"],
                        f"Drifted {net:+.0f} % over {len(steps)} small steps in {elapsed_min:.1f} min "
                        f"({baseline:.0f} → {requested:.0f} %)", "Controller")]
    edge = config.SETPOINT_MAX if net > 0 else config.SETPOINT_MIN
    band = f"{config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} % band"
    if not (config.SETPOINT_MIN <= requested <= config.SETPOINT_MAX):
        findings.append(Finding("ROC-002", "rate", W["DRIFT_TRAJECTORY"],
                                f"Now outside the {band}", "Tank farm T-101"))
        return findings
    rate = net / elapsed_min
    remaining = edge - requested
    if rate and remaining / rate >= 0:
        minutes = remaining / rate
        if minutes <= config.DRIFT_PROJECTION_MIN:
            when = "under a minute" if minutes < 1 else f"about {minutes:.0f} min"
            findings.append(Finding("ROC-002", "rate", W["DRIFT_TRAJECTORY"],
                                    f"Trajectory leaves the {band} in {when}", "Tank farm T-101"))
    return findings


# ---------------------------------------------------------------------------
# Layer 5 — operating envelope
# ---------------------------------------------------------------------------
def rule_envelope_pressure(ctx: RuleContext) -> list[Finding]:
    """ENV-002 — a command that adds load while the process is already near a limit."""
    if not ctx.command or not ctx.t:
        return []
    if ctx.command.action not in {"pump_start", "outlet_close", "setpoint"}:
        return []
    if ctx.t.pressure > config.PRESSURE_MAX_BAR:
        return [Finding("ENV-002", "envelope", W["ENVELOPE_BREACH"],
                        f"Pressure {ctx.t.pressure:.1f} bar, above the {config.PRESSURE_MAX_BAR:.1f} bar limit",
                        "Discharge pressure PT-201")]
    if ctx.t.pressure > config.PRESSURE_WARN_BAR:
        return [Finding("ENV-002", "envelope", W["ENVELOPE_APPROACH"],
                        f"Pressure {ctx.t.pressure:.1f} bar, near the {config.PRESSURE_MAX_BAR:.1f} bar limit",
                        "Discharge pressure PT-201")]
    return []


# ---------------------------------------------------------------------------
# Layers 2 and 3 — sequence and timing
# ---------------------------------------------------------------------------
def rule_rapid_sequence(ctx: RuleContext) -> list[Finding]:
    """SEQ-002 — actuator commands faster than a human control-room operator."""
    if not ctx.command or ctx.command.action not in ACTUATOR_ACTIONS:
        return []
    recent = ctx.state.history.recent(config.RAPID_WINDOW_S, ctx.now_ms, ACTUATOR_ACTIONS)
    burst = ctx.state.history.recent(config.BURST_WINDOW_S, ctx.now_ms, ACTUATOR_ACTIONS)
    findings: list[Finding] = []
    if len(recent) >= config.RAPID_COMMAND_COUNT:
        findings.append(Finding("SEQ-002", "sequence", W["RAPID_SEQUENCE"],
                                f"{len(recent)} actuator commands in {config.RAPID_WINDOW_S:.0f} s", "Controller"))
    if len(burst) >= config.BURST_COMMAND_COUNT:
        findings.append(Finding("SEQ-002", "sequence", W["BURST_SEQUENCE"],
                                f"{len(burst)} in the last {config.BURST_WINDOW_S:.0f} s", "Controller"))
    # Flapping: the same actuator driven both ways inside the window.
    pairs = [("pump_start", "pump_stop"), ("outlet_open", "outlet_close"), ("inlet_open", "inlet_close")]
    actions = [c.action for c in recent]
    for a, b in pairs:
        if a in actions and b in actions:
            findings.append(Finding("SEQ-003", "sequence", W["FLAPPING"],
                                    f"{a} and {b} within {config.RAPID_WINDOW_S:.0f} s", "Actuator"))
    return findings


UNSAFE_PATTERNS = [
    (("pump_start", "outlet_close"), "P-101 started and then MOV-201 closed"),
    (("outlet_close", "setpoint"), "MOV-201 closed and then the setpoint driven up"),
    (("maintenance_off", "outlet_close"), "Maintenance cleared immediately before an unsafe valve command"),
    (("inlet_close", "outlet_close"), "ESD-301 and MOV-201 both closed in sequence"),
]


def rule_unsafe_pattern(ctx: RuleContext) -> list[Finding]:
    """SEQ-004 — known dangerous multi-step command patterns."""
    if not ctx.command:
        return []
    # The current command is already in the history when the engine calls us.
    window = ctx.state.history.recent(config.BURST_WINDOW_S, ctx.now_ms)
    actions = [c.action for c in window]
    findings: list[Finding] = []
    for pattern, description in UNSAFE_PATTERNS:
        if _contains_subsequence(actions, pattern):
            findings.append(Finding("SEQ-004", "sequence", W["UNSAFE_PATTERN"],
                                    f"{description} ({' → '.join(pattern)})", "Process"))
    return findings


def _contains_subsequence(actions: list[str], pattern: tuple[str, ...]) -> bool:
    it = iter(actions)
    return all(any(a == step for a in it) for step in pattern)


# ---------------------------------------------------------------------------
# Layer 6 — operational context
# ---------------------------------------------------------------------------
def rule_maintenance_context(ctx: RuleContext) -> list[Finding]:
    """CTX-001 — legitimate maintenance work must not read as an attack."""
    if not ctx.command or not ctx.maintenance:
        return []
    if ctx.command.action not in MAINTENANCE_EXPECTED:
        return []
    return [Finding("CTX-001", "context", W["MAINTENANCE_CONTEXT"],
                    "MAINTENANCE mode — isolation steps expected",
                    "Controller")]


def rule_signature(ctx: RuleContext) -> list[Finding]:
    """SRC-001 / CMD-001 — the envelope: a keyed source must sign, and signed traffic must be fresh."""
    v = ctx.verification
    if not v:
        return []
    if v["status"] in ("unsigned", "bad"):
        return [Finding("SRC-001", "context", W["SIGNATURE_INVALID"], v["detail"].capitalize(), "Command source")]
    if v["status"] == "replay":
        return [Finding("CMD-001", "integrity", W["SEQUENCE_REPLAY"],
                        f"Signed envelope replayed: {v['detail']}", "Command path")]
    return []


def rule_source(ctx: RuleContext) -> list[Finding]:
    """SRC-001 — who is issuing the command, and should they be right now?"""
    if not ctx.command:
        return []
    source = ctx.command.source
    if ctx.verification.get("status") in ("unsigned", "bad"):
        return []                      # rule_signature already charged for the forged identity
    if source in config.TRUSTED_SOURCES:
        return []
    if source in config.MAINTENANCE_SOURCES:
        if ctx.maintenance:
            return []
        return [Finding("SRC-001", "context", W["UNTRUSTED_SOURCE"],
                        f"From '{source}' outside maintenance",
                        "Command source")]
    return [Finding("SRC-001", "context", W["UNTRUSTED_SOURCE"],
                    f"Unrecognised source '{source}'", "Command source")]


def rule_command_replay(ctx: RuleContext) -> list[Finding]:
    """CMD-001 — old traffic replayed: stale timestamps or an id we have already seen."""
    if not ctx.command:
        return []
    findings: list[Finding] = []
    age = (ctx.now_ms - ctx.command.ts) / 1000.0
    if age > config.CMD_STALE_S:
        findings.append(Finding("CMD-001", "integrity", W["COMMAND_STALE"],
                                f"Timestamp {age:.0f} s old — replayed or delayed",
                                "Command path"))
    elif age < -config.CMD_STALE_S:
        findings.append(Finding("CMD-001", "integrity", W["COMMAND_STALE"],
                                f"Timestamp {-age:.0f} s in the future", "Command path"))
    # The current command is already in the history, so a genuine duplicate shows twice.
    if ctx.state.history.times_seen(ctx.command.id) >= 2:
        findings.append(Finding("CMD-001", "integrity", W["COMMAND_DUPLICATE"],
                                f"Id {ctx.command.id} has already been processed — replay", "Command path"))
    return findings


# ---------------------------------------------------------------------------
# Layer 7 — telemetry integrity (evaluated on every command *and* periodically)
# ---------------------------------------------------------------------------
def rule_telemetry_integrity(ctx: RuleContext) -> list[Finding]:
    """TEL-001/002/003 — can the displayed process state still be trusted?"""
    integrity = ctx.state.integrity
    findings: list[Finding] = []
    if integrity.last_seq is None:
        return [Finding("TEL-001", "telemetry", W["TELEMETRY_STALE"],
                        "No telemetry received", "Telemetry path")]
    age = integrity.age_s(ctx.now)
    gap = integrity.gap_s(ctx.now)
    if age > config.TELEMETRY_STALE_S or gap > config.TELEMETRY_STALE_S:
        worst = max(age, gap)
        findings.append(Finding("TEL-001", "telemetry", W["TELEMETRY_STALE"],
                                f"Newest frame {worst:.0f} s old", "Telemetry path"))
    if integrity.repeat_count >= config.REPLAY_REPEAT_COUNT:
        findings.append(Finding("TEL-002", "telemetry", W["TELEMETRY_REPLAY"],
                                f"Sequence stuck at {integrity.last_seq} for {integrity.repeat_count} frames — replay suspected",
                                "Telemetry path"))
    if integrity.regressions:
        findings.append(Finding("TEL-003", "telemetry", W["TELEMETRY_REGRESSION"],
                                f"Sequence went backwards {integrity.regressions} time(s) — injected frames",
                                "Telemetry path"))
    return findings


def rule_physics_residual(ctx: RuleContext) -> list[Finding]:
    """PHY-001 — the reported state and the physics do not agree."""
    t = ctx.t
    if t is None:
        return []
    residual = ctx.state.flow_residual()
    if residual <= config.PHYSICS_RESIDUAL_LPM:
        return []
    if ctx.state.residual_duration(ctx.now) < config.PHYSICS_SETTLE_S:
        return []          # valves and flow meters need a moment to follow a command
    expected = ctx.state.expected_flow()
    return [Finding("PHY-001", "physics", W["PHYSICS_MISMATCH"],
                    f"Pump {'ON' if t.pump else 'OFF'}, outlet {'OPEN' if t.outlet_valve else 'CLOSED'} → "
                    f"expect ~{expected:.0f} m³/h, reading {t.flow:.0f}",
                    "Mainline flow FT-201")]


def rule_learned_baseline(ctx: RuleContext) -> list[Finding]:
    """BASE-001 — ordinary in itself, but unlike anything this source or action normally does."""
    if not ctx.command:
        return []
    findings: list[Finding] = []
    cadence = ctx.state.baseline.cadence_deviation(ctx.command)
    if cadence:
        findings.append(Finding("BASE-001", "baseline", W["BASELINE_DEVIATION"], cadence, "Command source"))
    value = ctx.state.baseline.value_deviation(ctx.command)
    if value:
        findings.append(Finding("BASE-001", "baseline", W["BASELINE_DEVIATION"], value, "Controller"))
    mix = ctx.state.baseline.mix_deviation(ctx.command)
    if mix:
        findings.append(Finding("BASE-001", "baseline", W["BASELINE_DEVIATION"], mix.capitalize(), "Command source"))
    return findings


COMMAND_RULES: list[CommandRule] = [
    rule_discharge_closure,
    rule_deadhead_start,
    rule_dry_run,
    rule_suction_isolation,
    rule_setpoint_change,
    rule_setpoint_drift,
    rule_envelope_pressure,
    rule_rapid_sequence,
    rule_unsafe_pattern,
    rule_signature,
    rule_source,
    rule_command_replay,
    rule_telemetry_integrity,
    rule_physics_residual,
    rule_maintenance_context,
    rule_learned_baseline,
]

PROCESS_RULES: list[CommandRule] = [
    rule_telemetry_integrity,
    rule_physics_residual,
]


def evaluate_command(state: ProcessState, command: Command, now: float | None = None) -> list[Finding]:
    from .. import process   # lazy: the domain modules import this file
    ctx = RuleContext(state=state, command=command, now=now or time.time())
    findings: list[Finding] = []
    for rule in process.domain().COMMAND_RULES:
        findings.extend(rule(ctx))
    return findings


def evaluate_process(state: ProcessState, now: float | None = None) -> list[Finding]:
    from .. import process
    ctx = RuleContext(state=state, command=None, now=now or time.time())
    findings: list[Finding] = []
    for rule in process.domain().PROCESS_RULES:
        findings.extend(rule(ctx))
    return findings
