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
                f"Pump is running (runtime {ctx.t.pump_runtime_s:.0f}s)", "Pump P-101"),
        Finding("SEQ-001", "state", W["CLOSING_DISCHARGE"],
                "Command closes the only discharge path while the pump is energised",
                "Outlet valve V-102"),
    ]
    if ctx.flow_active:
        findings.append(Finding("SEQ-001", "state", W["FLOW_ACTIVE"],
                                f"Measured flow is {ctx.t.flow:.0f} L/min through the outlet",
                                "Flow transmitter FT-101"))
    if not ctx.maintenance:
        findings.append(Finding("SEQ-001", "context", W["NOT_MAINTENANCE"],
                                f"Plant is in {ctx.t.mode} mode, not maintenance", "Controller"))
    if ctx.pump_stop_pending():
        findings.append(Finding("SEQ-001", "context", W["PUMP_STOPPING"],
                                "A pump stop was commanded seconds earlier — line is being isolated in order",
                                "Pump P-101"))
    return findings


def rule_deadhead_start(ctx: RuleContext) -> list[Finding]:
    """STATE-002 — start the pump into a closed discharge path."""
    if not ctx.command or ctx.command.action != "pump_start" or not ctx.t:
        return []
    if ctx.t.outlet_valve:
        return []
    findings = [
        Finding("STATE-002", "state", W["DEADHEAD_START"],
                "Pump start requested while the outlet valve is closed (no discharge path)",
                "Pump P-101 / Outlet valve V-102"),
    ]
    if not ctx.maintenance:
        findings.append(Finding("STATE-002", "context", W["NOT_MAINTENANCE"],
                                f"Plant is in {ctx.t.mode} mode, not maintenance", "Controller"))
    return findings


def rule_dry_run(ctx: RuleContext) -> list[Finding]:
    """STATE-003 — start or keep pumping with no liquid at the suction."""
    if not ctx.command or ctx.command.action != "pump_start" or not ctx.t:
        return []
    if ctx.t.tank_level >= config.LEVEL_MIN_PCT:
        return []
    weight = W["DRY_RUN"] if ctx.t.tank_level < config.PUMP_MIN_SUCTION_LEVEL else W["SUCTION_STARVED"]
    return [Finding("STATE-003", "state", weight,
                    f"Tank level is {ctx.t.tank_level:.0f} % — below the {config.LEVEL_MIN_PCT:.0f} % "
                    "minimum suction level for pump start", "Tank T-101 / Pump P-101")]


def rule_suction_isolation(ctx: RuleContext) -> list[Finding]:
    """STATE-004 — cut the make-up supply while the pump is drawing the tank down."""
    if not ctx.command or ctx.command.action != "inlet_close" or not ctx.t:
        return []
    if not ctx.t.pump or not ctx.flow_active:
        return []
    headroom = ctx.t.tank_level - config.LEVEL_MIN_PCT
    if headroom > 25:
        return []
    minutes = max(0.1, (headroom / 100.0 * config.TANK_CAPACITY_L) / max(1.0, ctx.t.flow))
    return [Finding("STATE-004", "state", W["SUCTION_STARVED"],
                    f"Inlet isolation while the pump draws {ctx.t.flow:.0f} L/min — tank reaches the "
                    f"low limit in about {minutes:.1f} min", "Inlet valve V-101 / Tank T-101")]


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
                                f"Setpoint step of {delta:+.0f} % against a normal operator "
                                f"adjustment of ±{config.SETPOINT_NORMAL_DELTA:.0f} %", "Controller"))
    if abs(delta) > config.SETPOINT_LARGE_DELTA:
        findings.append(Finding("ROC-001", "rate", W["SETPOINT_EXTREME"],
                                f"Step is more than {config.SETPOINT_LARGE_DELTA:.0f} % in a single "
                                "command — inconsistent with gradual process control", "Controller"))
    if not (config.SETPOINT_MIN <= requested <= config.SETPOINT_MAX):
        findings.append(Finding("ENV-001", "envelope", W["SETPOINT_OUT_OF_RANGE"],
                                f"Requested setpoint {requested:.0f} % is outside the safe operating "
                                f"band {config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} %",
                                "Tank T-101"))
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
                        f"Setpoint has drifted {net:+.0f} % across {len(steps)} small steps in "
                        f"{elapsed_min:.1f} min ({baseline:.0f} -> {requested:.0f} %) — each step alone "
                        f"is inside the normal ±{config.SETPOINT_NORMAL_DELTA:.0f} % band", "Controller")]
    edge = config.SETPOINT_MAX if net > 0 else config.SETPOINT_MIN
    band = f"{config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} % band"
    if not (config.SETPOINT_MIN <= requested <= config.SETPOINT_MAX):
        findings.append(Finding("ROC-002", "rate", W["DRIFT_TRAJECTORY"],
                                f"The drift has now carried the setpoint outside the {band}", "Tank T-101"))
        return findings
    rate = net / elapsed_min
    remaining = edge - requested
    if rate and remaining / rate >= 0:
        minutes = remaining / rate
        if minutes <= config.DRIFT_PROJECTION_MIN:
            when = "under a minute" if minutes < 1 else f"about {minutes:.0f} min"
            findings.append(Finding("ROC-002", "rate", W["DRIFT_TRAJECTORY"],
                                    f"At this rate the setpoint leaves the {band} in {when}", "Tank T-101"))
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
                        f"Discharge pressure is already {ctx.t.pressure:.1f} bar, above the "
                        f"{config.PRESSURE_MAX_BAR:.1f} bar limit", "Pressure transmitter PT-101")]
    if ctx.t.pressure > config.PRESSURE_WARN_BAR:
        return [Finding("ENV-002", "envelope", W["ENVELOPE_APPROACH"],
                        f"Discharge pressure is {ctx.t.pressure:.1f} bar, approaching the "
                        f"{config.PRESSURE_MAX_BAR:.1f} bar limit", "Pressure transmitter PT-101")]
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
                                f"{len(recent)} actuator commands in {config.RAPID_WINDOW_S:.0f} s — "
                                "faster than normal operator sequencing", "Controller"))
    if len(burst) >= config.BURST_COMMAND_COUNT:
        findings.append(Finding("SEQ-002", "sequence", W["BURST_SEQUENCE"],
                                f"{len(burst)} actuator commands in the last "
                                f"{config.BURST_WINDOW_S:.0f} s", "Controller"))
    # Flapping: the same actuator driven both ways inside the window.
    pairs = [("pump_start", "pump_stop"), ("outlet_open", "outlet_close"), ("inlet_open", "inlet_close")]
    actions = [c.action for c in recent]
    for a, b in pairs:
        if a in actions and b in actions:
            findings.append(Finding("SEQ-003", "sequence", W["FLAPPING"],
                                    f"{a} and {b} both issued within {config.RAPID_WINDOW_S:.0f} s — "
                                    "actuator is being cycled", "Actuator"))
    return findings


UNSAFE_PATTERNS = [
    (("pump_start", "outlet_close"), "Pump started and then the discharge closed"),
    (("outlet_close", "setpoint"), "Discharge closed and then the setpoint driven up"),
    (("maintenance_off", "outlet_close"), "Maintenance cleared immediately before an unsafe valve command"),
    (("inlet_close", "outlet_close"), "Both tank valves isolated in sequence"),
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
                                    f"{description} within {config.BURST_WINDOW_S:.0f} s "
                                    f"({' -> '.join(pattern)})", "Process"))
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
                    "Plant is in MAINTENANCE mode — isolation and restoration commands are expected here",
                    "Controller")]


def rule_source(ctx: RuleContext) -> list[Finding]:
    """SRC-001 — who is issuing the command, and should they be right now?"""
    if not ctx.command:
        return []
    source = ctx.command.source
    if source in config.TRUSTED_SOURCES:
        return []
    if source in config.MAINTENANCE_SOURCES:
        if ctx.maintenance:
            return []
        return [Finding("SRC-001", "context", W["UNTRUSTED_SOURCE"],
                        f"Command originated from '{source}' while the plant is not in maintenance",
                        "Command source")]
    return [Finding("SRC-001", "context", W["UNTRUSTED_SOURCE"],
                    f"Command originated from unrecognised source '{source}'", "Command source")]


def rule_command_replay(ctx: RuleContext) -> list[Finding]:
    """CMD-001 — old traffic replayed: stale timestamps or an id we have already seen."""
    if not ctx.command:
        return []
    findings: list[Finding] = []
    age = (ctx.now_ms - ctx.command.ts) / 1000.0
    if age > config.CMD_STALE_S:
        findings.append(Finding("CMD-001", "integrity", W["COMMAND_STALE"],
                                f"Command timestamp is {age:.0f} s old — replayed or delayed traffic",
                                "Command path"))
    elif age < -config.CMD_STALE_S:
        findings.append(Finding("CMD-001", "integrity", W["COMMAND_STALE"],
                                f"Command timestamp is {-age:.0f} s in the future — clock manipulation "
                                "or forged traffic", "Command path"))
    # The current command is already in the history, so a genuine duplicate shows twice.
    if ctx.state.history.times_seen(ctx.command.id) >= 2:
        findings.append(Finding("CMD-001", "integrity", W["COMMAND_DUPLICATE"],
                                f"Command id {ctx.command.id} has already been processed — the same "
                                "message is being replayed", "Command path"))
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
                        "No telemetry has been received — process state is unknown", "Telemetry path")]
    age = integrity.age_s(ctx.now)
    gap = integrity.gap_s(ctx.now)
    if age > config.TELEMETRY_STALE_S or gap > config.TELEMETRY_STALE_S:
        worst = max(age, gap)
        findings.append(Finding("TEL-001", "telemetry", W["TELEMETRY_STALE"],
                                f"Newest telemetry frame is {worst:.0f} s old — the displayed state may "
                                "no longer represent the live plant", "Telemetry path"))
    if integrity.repeat_count >= config.REPLAY_REPEAT_COUNT:
        findings.append(Finding("TEL-002", "telemetry", W["TELEMETRY_REPLAY"],
                                f"Telemetry sequence has not advanced for {integrity.repeat_count} frames "
                                f"(stuck at seq {integrity.last_seq}) — replay suspected", "Telemetry path"))
    if integrity.regressions:
        findings.append(Finding("TEL-003", "telemetry", W["TELEMETRY_REGRESSION"],
                                f"Telemetry sequence went backwards {integrity.regressions} time(s) — "
                                "frames are being injected or reordered", "Telemetry path"))
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
                    f"Pump {'ON' if t.pump else 'OFF'} with outlet "
                    f"{'OPEN' if t.outlet_valve else 'CLOSED'} should give about {expected:.0f} L/min, "
                    f"but {t.flow:.0f} L/min is reported (residual {residual:.0f} L/min)",
                    "Flow transmitter FT-101")]


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
    rule_source,
    rule_command_replay,
    rule_telemetry_integrity,
    rule_physics_residual,
    rule_maintenance_context,
]

PROCESS_RULES: list[CommandRule] = [
    rule_telemetry_integrity,
    rule_physics_residual,
]


def evaluate_command(state: ProcessState, command: Command, now: float | None = None) -> list[Finding]:
    ctx = RuleContext(state=state, command=command, now=now or time.time())
    findings: list[Finding] = []
    for rule in COMMAND_RULES:
        findings.extend(rule(ctx))
    return findings


def evaluate_process(state: ProcessState, now: float | None = None) -> list[Finding]:
    ctx = RuleContext(state=state, command=None, now=now or time.time())
    findings: list[Finding] = []
    for rule in PROCESS_RULES:
        findings.extend(rule(ctx))
    return findings
