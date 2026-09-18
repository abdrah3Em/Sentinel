"""Detection layers for the distribution feeder (PRD revision 2, sections 16-23).

Same shape as the pumping-station rules: pure functions over a RuleContext that
return weighted, human-readable findings.  The integrity rules (CMD-001,
TEL-00x) are shared with the station unchanged.
"""
from __future__ import annotations

from .. import config
from ..models import Finding
from ..plant.grid import (BUSES, BUS_NAME, DEVICE, SECTIONS, SWITCHING_ACTIONS, TAP_ACTIONS,
                          bus_voltage, switches_after, topology)
from .rules import (RuleContext, W, _contains_subsequence, rule_command_replay, rule_learned_baseline,
                    rule_signature, rule_telemetry_integrity)

ACTUATOR_ACTIONS = SWITCHING_ACTIONS | TAP_ACTIONS | {"protection_reset"}
CONSEQUENTIAL = SWITCHING_ACTIONS | TAP_ACTIONS | {"avc_target", "pv_curtail"}
PROGRAM_EXPECTED = SWITCHING_ACTIONS | {"protection_reset", "ptw_issue", "ptw_cancel", "pv_curtail"}
CLOSES = {"cb_close": "cb", "sw_close": "sw", "tie_close": "tie", "cb2_close": "cb2"}
VMIN, VMAX = config.GRID_V_MIN_KV, config.GRID_V_MAX_KV


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _switches(ctx: RuleContext) -> tuple[bool, bool, bool, bool]:
    t = ctx.t
    return bool(t.cb_closed), bool(t.sw_closed), bool(t.tie_closed), bool(getattr(t, "cb2_closed", True))


def _before_after(ctx: RuleContext) -> tuple[dict, dict]:
    cb, sw, tie, cb2 = _switches(ctx)
    before = topology(cb, sw, tie, cb2)
    after = topology(*switches_after(cb, sw, tie, ctx.command.action, cb2))
    return before, after


def _device(action: str) -> str:
    return DEVICE.get(action.split("_")[0], action)


def _lost_buses(before: dict, after: dict) -> list[str]:
    return [b for b in BUSES if before["buses"][b] and not after["buses"][b]]


def _names(buses: list[str]) -> str:
    return ", ".join(BUS_NAME[b] for b in buses)


def _not_in_program(rule: str, ctx: RuleContext) -> list[Finding]:
    if ctx.t.switching_program:
        return []
    return [Finding(rule, "context", W["NOT_IN_PROGRAM"],
                    "No switching program declared", "Switching program")]


def _permits(t) -> list[str]:
    return list(getattr(t, "permits", None) or ([t.permit_to_work] if t.permit_to_work else []))


def _energised_permitted_sections(ctx: RuleContext) -> list[str]:
    """Sections under permit that this close would energise."""
    t = ctx.t
    if not t or ctx.command is None or ctx.command.action not in CLOSES:
        return []
    before, after = _before_after(ctx)
    return [s for s in _permits(t) if after["sections"].get(s) and not before["sections"].get(s)]


def _energises_permitted_section(ctx: RuleContext) -> bool:
    return bool(_energised_permitted_sections(ctx))


# ---------------------------------------------------------------------------
# Layer 1 — state-based detection
# ---------------------------------------------------------------------------
def rule_close_onto_fault(ctx: RuleContext) -> list[Finding]:
    """STATE-001 — the flagship: a perfectly ordinary close, onto a fault that has not been cleared."""
    if not ctx.command or ctx.command.action not in ("cb_close", "cb2_close") or not ctx.t:
        return []
    t = ctx.t
    device = _device(ctx.command.action)
    source = "t1" if ctx.command.action == "cb_close" else "f2"
    _, after = _before_after(ctx)
    findings: list[Finding] = []
    faults = list(getattr(t, "fault_sections", None) or ([t.fault_section] if t.fault_section else []))
    for section in faults:
        if after[source].get(section):
            idx = SECTIONS.index(section) + 1
            findings.append(Finding("STATE-001", "state", W["FAULT_PRESENT"],
                                    f"FI-{idx} set on {section} — fault not cleared", f"{device} / Section {section}"))
    tripped = t.protection_tripped if source == "t1" else getattr(t, "protection2_tripped", False)
    if tripped:
        age = f"{t.trip_age_s:.0f} s ago" if t.trip_age_s is not None and source == "t1" else "earlier"
        findings.append(Finding("STATE-001", "state", W["PROTECTION_TRIPPED"],
                                f"Protection tripped {age}, not reset", f"{device} relay"))
    if not findings:
        return []
    if after["sections"]["S3"] and not t.supplied.get("b3", False):
        findings.append(Finding("STATE-001", "state", W["CRITICAL_LOAD"],
                                "Hospital bus B3 downstream", BUS_NAME["b3"]))
    return findings + _not_in_program("STATE-001", ctx)


def rule_open_under_load(ctx: RuleContext) -> list[Finding]:
    """STATE-002 — open the feeder breaker with load on it and nowhere else for it to go."""
    if not ctx.command or ctx.command.action not in ("cb_open", "cb2_open") or not ctx.t:
        return []
    t = ctx.t
    device = _device(ctx.command.action)
    if not (t.cb_closed if device == "CB-101" else getattr(t, "cb2_closed", True)):
        return []
    before, after = _before_after(ctx)
    lost = _lost_buses(before, after)
    loaded = (t.i_feeder_a if device == "CB-101" else getattr(t, "i_f2_a", 0.0)) > config.GRID_LOADED_A
    if not lost and not loaded:
        return []
    findings: list[Finding] = []
    if lost:
        findings.append(Finding("STATE-002", "state", W["LOSS_OF_SUPPLY"],
                                f"Drops {_names(lost)} — no alternate path", device))
        if "b3" in lost:
            findings.append(Finding("STATE-002", "state", W["CRITICAL_LOAD"],
                                    "Hospital bus B3 loses supply", BUS_NAME["b3"]))
    if loaded:
        amps = t.i_feeder_a if device == "CB-101" else getattr(t, "i_f2_a", 0.0)
        findings.append(Finding("STATE-002", "state", W["BREAKER_LOADED"], f"Carrying {amps:.0f} A", device))
    if not lost:
        findings.append(Finding("CTX-003", "context", W["ALTERNATE_PATH"],
                                "TS-201 closed — load transfers to F2", "TS-201"))
    return findings + _not_in_program("STATE-002", ctx)


def rule_parallel(ctx: RuleContext) -> list[Finding]:
    """STATE-003 — a close that loops F1 and F2 together."""
    if not ctx.command or ctx.command.action not in CLOSES or not ctx.t:
        return []
    before, after = _before_after(ctx)
    if before["parallel"] or not after["parallel"]:
        return []
    return [Finding("STATE-003", "state", W["UNCONTROLLED_PARALLEL"],
                    "Parallels F1 and F2 through TS-201", "TS-201 / CB-101")] \
        + _not_in_program("STATE-003", ctx)


def rule_energise_under_permit(ctx: RuleContext) -> list[Finding]:
    """STATE-004 — energising a section a crew is working on.  A program never excuses this."""
    sections = _energised_permitted_sections(ctx)
    if not sections:
        return []
    section = ", ".join(sections)
    return [Finding("STATE-004", "state", W["PTW_ENERGISE"],
                    f"Energises {section} under permit-to-work", f"Section {section}"),
            Finding("STATE-004", "state", W["PTW_CREW"],
                    f"Crew may be on {section} conductors", f"Section {section}")]


def rule_isolate_critical(ctx: RuleContext) -> list[Finding]:
    """STATE-005 — open the sectionaliser when the hospital is fed only through it."""
    if not ctx.command or ctx.command.action != "sw_open" or not ctx.t or not ctx.t.sw_closed:
        return []
    before, after = _before_after(ctx)
    lost = _lost_buses(before, after)
    if not lost:
        return []
    findings = [Finding("STATE-005", "state", W["LOSS_OF_SUPPLY"],
                        f"Drops {_names(lost)} — TS-201 open", "SW-102")]
    if "b3" in lost:
        findings.append(Finding("STATE-005", "state", W["CRITICAL_LOAD"],
                                "Hospital bus B3 fed only through SW-102", BUS_NAME["b3"]))
    return findings + _not_in_program("STATE-005", ctx)


def rule_tap_at_limit(ctx: RuleContext) -> list[Finding]:
    """STATE-006 — a manual tap step that pushes an already-out-of-limits busbar further out."""
    if not ctx.command or ctx.command.action not in TAP_ACTIONS or not ctx.t:
        return []
    t, action = ctx.t, ctx.command.action
    raising = action == "tap_raise" or (action == "tap_set" and ctx.command.value is not None
                                        and float(ctx.command.value) > t.tap)
    lowering = action == "tap_lower" or (action == "tap_set" and ctx.command.value is not None
                                         and float(ctx.command.value) < t.tap)
    if raising and t.v_bus_kv > VMAX:
        return [Finding("STATE-006", "state", W["TAP_AT_LIMIT"],
                        f"Busbar {t.v_bus_kv:.2f} kV already above the {VMAX:.2f} kV limit", "T1 OLTC")]
    if lowering and t.v_bus_kv < VMIN:
        return [Finding("STATE-006", "state", W["TAP_AT_LIMIT"],
                        f"Busbar {t.v_bus_kv:.2f} kV already below the {VMIN:.2f} kV limit", "T1 OLTC")]
    return []


def rule_curtail_under_stress(ctx: RuleContext) -> list[Finding]:
    """STATE-007 — remove the PV support while the feeder is near its rating."""
    if not ctx.command or ctx.command.action != "pv_curtail" or ctx.command.value is None or not ctx.t:
        return []
    if float(ctx.command.value) < 90 or ctx.t.i_feeder_a < config.GRID_I_WARN_A:
        return []
    return [Finding("STATE-007", "state", W["CURTAIL_UNDER_STRESS"],
                    f"Curtail to {float(ctx.command.value):.0f} % at {ctx.t.i_feeder_a:.0f} A of "
                    f"{config.GRID_I_RATING_A:.0f} A — removes {ctx.t.pv_kw:.0f} kW support", "PV-1")]


# ---------------------------------------------------------------------------
# Layer 4 — rate of change on the voltage target
# ---------------------------------------------------------------------------
def rule_target_change(ctx: RuleContext) -> list[Finding]:
    """ROC-001 / ENV-001 — a single AVC-target or tap step larger than an operator would make."""
    if not ctx.command or ctx.command.action not in {"avc_target", "tap_set"} or ctx.command.value is None \
            or not ctx.t:
        return []
    t = ctx.t
    findings: list[Finding] = []
    if ctx.command.action == "avc_target":
        requested = float(ctx.command.value)
        delta = requested - t.avc_target_kv
        if abs(delta) > config.GRID_AVC_NORMAL_DELTA_KV:
            findings.append(Finding("ROC-001", "rate", W["SETPOINT_LARGE"],
                                    f"Step {delta:+.2f} kV vs normal ±{config.GRID_AVC_NORMAL_DELTA_KV:.2f} kV trim", "AVC"))
        if abs(delta) > config.GRID_AVC_LARGE_DELTA_KV:
            findings.append(Finding("ROC-001", "rate", W["SETPOINT_EXTREME"],
                                    f"More than {config.GRID_AVC_LARGE_DELTA_KV:.1f} kV in one command", "AVC"))
        resulting = requested
    else:
        tap = int(round(float(ctx.command.value)))
        delta_taps = tap - t.tap
        if abs(delta_taps) > config.GRID_TAP_NORMAL_DELTA:
            findings.append(Finding("ROC-001", "rate", W["SETPOINT_LARGE"],
                                    f"Jumps {delta_taps:+d} taps in one command", "T1 OLTC"))
        if abs(delta_taps) > config.GRID_TAP_LARGE_DELTA:
            findings.append(Finding("ROC-001", "rate", W["SETPOINT_EXTREME"],
                                    f"More than {config.GRID_TAP_LARGE_DELTA} taps in one command", "T1 OLTC"))
        resulting = bus_voltage(tap, t.v_source_kv)
    if not (VMIN <= resulting <= VMAX):
        findings.append(Finding("ENV-001", "envelope", W["SETPOINT_OUT_OF_RANGE"],
                                f"Puts the busbar at {resulting:.2f} kV, outside {VMIN:.2f}–{VMAX:.2f} kV",
                                "11 kV busbar BB-101"))
    return findings


def rule_target_drift(ctx: RuleContext) -> list[Finding]:
    """ROC-002 — the target walked a little at a time.  Each step passes ROC-001; the trajectory does not."""
    if not ctx.command or ctx.command.action != "avc_target" or ctx.command.value is None or not ctx.t:
        return []
    steps = ctx.state.history.recent(config.DRIFT_WINDOW_S, ctx.now_ms, {"avc_target"})
    if len(steps) < config.DRIFT_MIN_STEPS:
        return []
    baseline = ctx.state.setpoint_baseline(ctx.now_ms, config.DRIFT_WINDOW_S)
    if baseline is None:
        baseline = ctx.t.avc_target_kv
    requested = float(ctx.command.value)
    net = requested - baseline
    if abs(net) <= config.GRID_DRIFT_NET_KV:
        return []
    elapsed_min = max(0.05, (ctx.now_ms - steps[0].ts) / 60000.0)
    findings = [Finding("ROC-002", "rate", W["SETPOINT_DRIFT"],
                        f"Drifted {net:+.2f} kV over {len(steps)} small steps in {elapsed_min:.1f} min "
                        f"({baseline:.2f} → {requested:.2f} kV)", "AVC")]
    edge = VMAX if net > 0 else VMIN
    if not (VMIN <= requested <= VMAX):
        findings.append(Finding("ROC-002", "rate", W["DRIFT_TRAJECTORY"],
                                f"Now outside the {VMIN:.2f}–{VMAX:.2f} kV band", "11 kV busbar BB-101"))
        return findings
    rate = net / elapsed_min
    remaining = edge - requested
    if rate and remaining / rate >= 0:
        minutes = remaining / rate
        if minutes <= config.DRIFT_PROJECTION_MIN:
            when = "under a minute" if minutes < 1 else f"about {minutes:.0f} min"
            findings.append(Finding("ROC-002", "rate", W["DRIFT_TRAJECTORY"],
                                    f"Busbar crosses the {edge:.2f} kV limit in {when} at this rate",
                                    "11 kV busbar BB-101"))
    return findings


# ---------------------------------------------------------------------------
# Layer 5 — operating envelope
# ---------------------------------------------------------------------------
def rule_envelope(ctx: RuleContext) -> list[Finding]:
    """ENV-002 — a command that adds load or voltage while the feeder is already near a limit."""
    if not ctx.command or not ctx.t:
        return []
    action, t = ctx.command.action, ctx.t
    raising_target = action == "avc_target" and ctx.command.value is not None \
        and float(ctx.command.value) > t.avc_target_kv
    if action not in {"cb_close", "tie_close", "sw_close", "tap_raise"} and not raising_target:
        return []
    findings: list[Finding] = []
    if t.v_bus_kv > VMAX:
        findings.append(Finding("ENV-002", "envelope", W["ENVELOPE_BREACH"],
                                f"Busbar {t.v_bus_kv:.2f} kV, above the {VMAX:.2f} kV limit",
                                "11 kV busbar BB-101"))
    elif t.v_bus_kv > config.GRID_V_WARN_HIGH_KV and (raising_target or action == "tap_raise"):
        findings.append(Finding("ENV-002", "envelope", W["ENVELOPE_APPROACH"],
                                f"Busbar {t.v_bus_kv:.2f} kV, near the {VMAX:.2f} kV limit",
                                "11 kV busbar BB-101"))
    if action in {"cb_close", "tie_close", "sw_close"}:
        if t.i_feeder_a > config.GRID_I_RATING_A and not t.fault_current_ka:
            findings.append(Finding("ENV-002", "envelope", W["ENVELOPE_BREACH"],
                                    f"Feeder {t.i_feeder_a:.0f} A, above its {config.GRID_I_RATING_A:.0f} A rating", "CB-101"))
        elif t.i_feeder_a > config.GRID_I_WARN_A:
            findings.append(Finding("ENV-002", "envelope", W["ENVELOPE_APPROACH"],
                                    f"Feeder {t.i_feeder_a:.0f} A, near its {config.GRID_I_RATING_A:.0f} A rating", "CB-101"))
    return findings


# ---------------------------------------------------------------------------
# Layers 2 and 3 — sequence and timing
# ---------------------------------------------------------------------------
def rule_rapid_switching(ctx: RuleContext) -> list[Finding]:
    """SEQ-002 / SEQ-003 — switching faster than a control room ever does, and breaker pumping."""
    if not ctx.command or ctx.command.action not in ACTUATOR_ACTIONS:
        return []
    recent = ctx.state.history.recent(config.RAPID_WINDOW_S, ctx.now_ms, ACTUATOR_ACTIONS)
    burst = ctx.state.history.recent(config.BURST_WINDOW_S, ctx.now_ms, ACTUATOR_ACTIONS)
    findings: list[Finding] = []
    if len(recent) >= config.RAPID_COMMAND_COUNT:
        findings.append(Finding("SEQ-002", "sequence", W["RAPID_SEQUENCE"],
                                f"{len(recent)} switching commands in {config.RAPID_WINDOW_S:.0f} s", "Control room"))
    if len(burst) >= config.BURST_COMMAND_COUNT:
        findings.append(Finding("SEQ-002", "sequence", W["BURST_SEQUENCE"],
                                f"{len(burst)} in the last {config.BURST_WINDOW_S:.0f} s", "Control room"))
    actions = [c.action for c in recent]
    for a, b, device in (("cb_open", "cb_close", "CB-101"), ("sw_open", "sw_close", "SW-102"),
                         ("tie_open", "tie_close", "TS-201"), ("cb2_open", "cb2_close", "CB-201")):
        if a in actions and b in actions:
            findings.append(Finding("SEQ-003", "sequence", W["BREAKER_PUMPING"],
                                    f"{a} and {b} within {config.RAPID_WINDOW_S:.0f} s — {device} pumped", device))
    return findings


UNSAFE_PATTERNS = [
    (("protection_reset", "cb_close"), "Protection reset and an immediate close with the fault still present",
     lambda t: t.fault_present),
    (("tie_open", "sw_open", "cb_open"), "Feeder isolated piece by piece outside a switching program",
     lambda t: not t.switching_program),
    (("avc_manual", "tap_raise", "tap_raise"), "Voltage control disabled and the tap driven by hand",
     lambda t: True),
    (("switching_program_off", "cb_open"), "Switching program cleared immediately before a breaker open",
     lambda t: True),
]


def rule_unsafe_pattern(ctx: RuleContext) -> list[Finding]:
    """SEQ-004 — known dangerous multi-step switching patterns."""
    if not ctx.command or not ctx.t:
        return []
    window = ctx.state.history.recent(config.BURST_WINDOW_S, ctx.now_ms)
    actions = [c.action for c in window]
    findings: list[Finding] = []
    for pattern, description, applies in UNSAFE_PATTERNS:
        if ctx.command.action == pattern[-1] and applies(ctx.t) and _contains_subsequence(actions, pattern):
            findings.append(Finding("SEQ-004", "sequence", W["UNSAFE_PATTERN"],
                                    f"{description} ({' → '.join(pattern)})", "Feeder F1"))
    return findings


# ---------------------------------------------------------------------------
# Layer 6 — operational context
# ---------------------------------------------------------------------------
def rule_program_context(ctx: RuleContext) -> list[Finding]:
    """CTX-001 — a declared switching program makes isolation, transfer and restoration expected,
    but only for the equipment it names."""
    if not ctx.command or not ctx.t or not ctx.t.switching_program:
        return []
    if ctx.command.action not in PROGRAM_EXPECTED or _energises_permitted_section(ctx):
        return []
    device = _device(ctx.command.action)
    covers = list(getattr(ctx.t, "sp_covers", None) or [])
    if ctx.command.action in SWITCHING_ACTIONS and covers and device not in covers:
        return [Finding("CTX-001", "context", 0,
                        f"Program {ctx.t.switching_program} does not cover {device}", "Switching program")]
    return [Finding("CTX-001", "context", W["PROGRAM_CONTEXT"],
                    f"Program {ctx.t.switching_program} covers {device} — step expected", "Switching program")]


def rule_restoration_context(ctx: RuleContext) -> list[Finding]:
    """CTX-002 — after a cleared fault and a protection reset, the close is the expected restoration."""
    if not ctx.command or ctx.command.action != "cb_close" or not ctx.t:
        return []
    t = ctx.t
    if t.fault_present or t.protection_tripped or t.cb_closed or t.trip_age_s is None or t.trip_age_s > 900:
        return []
    return [Finding("CTX-002", "context", W["FAULT_CLEARED"],
                    "Fault cleared and protection reset — restoration step", "CB-101")]


def rule_source(ctx: RuleContext) -> list[Finding]:
    """SRC-001 — who is switching, and should they be right now?"""
    if not ctx.command:
        return []
    source = ctx.command.source
    if ctx.verification.get("status") in ("unsigned", "bad"):
        return []                      # rule_signature already charged for the forged identity
    if source in config.GRID_TRUSTED_SOURCES:
        return []
    if source in config.GRID_PROGRAM_SOURCES:
        if ctx.t and ctx.t.switching_program:
            return []
        return [Finding("SRC-001", "context", W["UNTRUSTED_SOURCE"],
                        f"From '{source}' with no switching program", "Command source")]
    return [Finding("SRC-001", "context", W["UNTRUSTED_SOURCE"],
                    f"Unrecognised source '{source}'", "Command source")]


# ---------------------------------------------------------------------------
# Layer 7 — physics
# ---------------------------------------------------------------------------
def rule_physics(ctx: RuleContext) -> list[Finding]:
    """PHY-001 — telemetry that disagrees with what tap, source and loads say it should be."""
    t = ctx.t
    if t is None or ctx.state.residual() <= config.GRID_PHYSICS_RESIDUAL_KV:
        return []
    if ctx.state.residual_duration(ctx.now) < config.PHYSICS_SETTLE_S:
        return []
    expected = ctx.state.expected()
    if t.cb_closed and t.i_feeder_a < 5 and (t.load_b1_kw + t.load_b2_kw + t.load_b3_kw) > 200:
        detail = f"CB-101 closed with {t.load_b1_kw + t.load_b2_kw + t.load_b3_kw:.0f} kW supplied, reads {t.i_feeder_a:.0f} A"
    else:
        detail = f"Busbar {t.v_bus_kv:.2f} kV; tap {t.tap:+d} at {t.v_source_kv:.1f} kV source implies {expected:.2f} kV"
    return [Finding("PHY-001", "physics", W["PHYSICS_MISMATCH"], detail, "Feeder instrumentation")]


COMMAND_RULES = [
    rule_close_onto_fault,
    rule_open_under_load,
    rule_parallel,
    rule_energise_under_permit,
    rule_isolate_critical,
    rule_tap_at_limit,
    rule_curtail_under_stress,
    rule_target_change,
    rule_target_drift,
    rule_envelope,
    rule_rapid_switching,
    rule_unsafe_pattern,
    rule_signature,
    rule_source,
    rule_command_replay,
    rule_telemetry_integrity,
    rule_physics,
    rule_program_context,
    rule_restoration_context,
    rule_learned_baseline,
]

PROCESS_RULES = [
    rule_telemetry_integrity,
    rule_physics,
]
