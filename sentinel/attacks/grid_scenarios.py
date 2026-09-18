"""Attack and operations scenarios for the distribution feeder (PRD rev 2, sections 30-36).

Nothing here touches real infrastructure: each scenario is a deterministic
script of protocol-valid MQTT commands (plus simulator-only fault hooks) aimed
at the local feeder model, so a judge can watch the same story replay
identically every time.
"""
from __future__ import annotations

from .base import Scenario, Step

OPERATOR = "operator-hmi"
ATTACKER = "engineering-laptop"
CREW = "field-crew"

SCENARIOS: dict[str, Scenario] = {}


def register(scenario: Scenario) -> Scenario:
    SCENARIOS[scenario.id] = scenario
    return scenario


def _n(note: str, delay: float = 1.0) -> Step:
    return Step("narrate", note=note, delay=delay)


def _c(action: str, value=None, source: str = OPERATOR, note: str = "", delay: float = 2.0, **kw) -> Step:
    return Step("command", action, value, source, delay, note or action, **kw)


def _sim(hook: str, value=None, note: str = "", delay: float = 1.0) -> Step:
    return Step("sim", hook, value, ATTACKER, delay, note)


register(Scenario(
    id="close_onto_fault",
    title="1 — Close onto a standing fault (flagship)",
    kind="attack",
    narrative="Protection trips CB-101 for a fault on S2; an ordinary cb_close arrives before it is cleared.",
    expect="CRITICAL · STATE-001",
    duration_hint=34,
    steps=[
        _n("Feeder healthy, tie open, AVC AUTO.", 0.5),
        _sim("fault_inject", "S2", "Fault on S2", 2.0),
        _n("CB-101 tripped, FI-2 set, hospital dark — protection doing its job.", 3.0),
        _n("A valid cb_close arrives from an engineering laptop. Fault not cleared.", 4.0),
        _c("cb_close", source=ATTACKER, note="Attack: cb_close onto the fault", delay=1.0),
        _n("6.5 kA for 150 ms, re-trip, stress counter up.", 5.0),
        _sim("fault_clear", True, "Crew clears the fault on S2", 6.0),
        _c("protection_reset", note="Operator resets protection", delay=3.0),
        _c("cb_close", note="Operator restores supply", delay=3.0),
        _n("Same command, cleared fault: quiet.", 3.0),
    ],
))

register(Scenario(
    id="inject_open",
    title="2 — Command injection: open a healthy feeder breaker",
    kind="attack",
    narrative="A valid cb_open on a healthy, loaded feeder — three buses and the hospital go dark.",
    expect="CRITICAL · STATE-002",
    duration_hint=16,
    steps=[
        _n("Feeder at duty, tie open, no switching program.", 0.5),
        _n("A valid cb_open arrives from an engineering laptop.", 3.0),
        _c("cb_open", source=ATTACKER, note="Attack: cb_open with the feeder loaded", delay=1.0),
        _n("B1, B2, B3 dead. CML climbing.", 5.0),
        _c("cb_close", note="Operator restores supply", delay=5.0),
    ],
))

register(Scenario(
    id="avc_drift",
    title="3 — Slow drift of the voltage target",
    kind="attack",
    narrative="AVC target raised 0.1 kV at a time; the tap changer walks the busbar out of limits.",
    expect="MEDIUM → HIGH · ROC-002",
    duration_hint=48,
    steps=[
        _n("AVC target 11.00 kV, busbar inside the band.", 0.5),
        _c("avc_target", 11.1, ATTACKER, "Drift step 1: 11.00 -> 11.10 kV", 1.5),
        _c("avc_target", 11.2, ATTACKER, "Drift step 2: 11.20 kV", 4.0),
        _c("avc_target", 11.3, ATTACKER, "Drift step 3: 11.30 kV", 4.0),
        _c("avc_target", 11.4, ATTACKER, "Drift step 4: 11.40 kV", 4.0),
        _n("Net movement now beyond any one-shift trim.", 1.0),
        _c("avc_target", 11.5, ATTACKER, "Drift step 5: 11.50 kV", 3.0),
        _c("avc_target", 11.6, ATTACKER, "Drift step 6: 11.60 kV", 4.0),
        _c("avc_target", 11.7, ATTACKER, "Drift step 7: 11.70 kV — outside the statutory band", 4.0),
        _c("avc_target", 11.8, ATTACKER, "Drift step 8: 11.80 kV", 4.0),
        _n("Every customer now above statutory voltage.", 4.0),
        _c("avc_target", 11.0, note="Operator returns the target to 11.00 kV", delay=6.0),
    ],
))

register(Scenario(
    id="breaker_pumping",
    title="4 — Rapid switching sequence / breaker pumping",
    kind="attack",
    narrative="Open, close, open, close, tie close — inside four seconds.",
    expect="MEDIUM · SEQ-002 + SEQ-003 · STATE-003",
    duration_hint=14,
    steps=[
        _n("A scripted burst of switching commands begins.", 0.5),
        _c("cb_open", source=ATTACKER, note="Burst 1/5", delay=0.8),
        _c("cb_close", source=ATTACKER, note="Burst 2/5", delay=0.8),
        _c("cb_open", source=ATTACKER, note="Burst 3/5", delay=0.8),
        _c("cb_close", source=ATTACKER, note="Burst 4/5", delay=0.8),
        _c("tie_close", source=ATTACKER, note="Burst 5/5 — parallels F1 and F2", delay=0.8),
        _n("Five switching commands in four seconds, no program.", 3.0),
        _c("tie_open", note="Operator breaks the parallel", delay=4.0),
    ],
))

register(Scenario(
    id="telemetry_replay",
    title="5 — Telemetry & command replay",
    kind="attack",
    narrative="A frozen healthy frame hides a dark hospital bus; a captured command is replayed verbatim.",
    expect="HIGH · TEL-002 + CMD-001 · confidence LOW",
    duration_hint=44,
    steps=[
        _c("pv_curtail", 0, note="Routine operator command — recorded by the attacker", delay=0.5, remember="captured"),
        _n("Telemetry frame captured for replay.", 2.0),
        Step("replay", seconds=30.0, delay=1.0, source=ATTACKER, note="Attack: healthy frame replayed at 2 Hz, controller silenced"),
        _c("sw_open", source=ATTACKER, note="Attack: sw_open behind the frozen picture", delay=3.0),
        _n("B2 and the hospital are dead; the display has not moved.", 5.0),
        Step("replay_command", key="captured", age_s=300.0, source=ATTACKER, delay=4.0,
             note="Attack: recorded command replayed verbatim — same id, 5 min old"),
        _n("Every decision now rests on a state that is not real.", 6.0),
        _n("Telemetry returns — mimic snaps to the dead buses.", 10.0),
        _c("sw_close", note="Operator restores SW-102", delay=4.0),
    ],
))

register(Scenario(
    id="planned_switching",
    title="6 — Planned switching program (must stay quiet)",
    kind="legitimate",
    narrative="Transfer, isolate S1 under permit, restore — under a declared switching program.",
    expect="LOW at most · CTX-001",
    duration_hint=40,
    steps=[
        _c("switching_program_on", "SP-0417:CB-101,SW-102,TS-201,S1", note="Program SP-0417 declared: CB-101, SW-102, TS-201, S1", delay=0.5),
        _c("tie_close", note="Transfer: tie_close (short parallel)", delay=3.0),
        _c("sw_open", note="Transfer: sw_open — B2, B3 from F2", delay=3.0),
        _c("cb_open", note="Isolate: cb_open — S1 dead for work", delay=3.0),
        _c("ptw_issue", "S1", note="Permit-to-work on S1", delay=3.0),
        _n("Crew on S1. Same commands as an attack; nothing above LOW.", 3.0),
        _c("ptw_cancel", note="Permit cancelled", delay=5.0),
        _c("cb_close", note="Restore: cb_close", delay=3.0),
        _c("sw_close", note="Restore: sw_close (short parallel)", delay=3.0),
        _c("tie_open", note="Restore: tie_open", delay=3.0),
        _c("switching_program_off", note="Program SP-0417 closed", delay=3.0),
    ],
))

register(Scenario(
    id="fault_restore",
    title="7 — Fault, locate, clear, restore",
    kind="legitimate",
    narrative="Fault on S3, crew clears it, protection reset, breaker closed — the same cb_close, now expected.",
    expect="Quiet · CTX-002",
    duration_hint=24,
    steps=[
        _sim("fault_inject", "S3", "Fault on S3 — protection trips CB-101", 0.5),
        _n("Crew dispatched.", 3.0),
        _sim("fault_clear", True, "Crew clears the fault on S3", 8.0),
        _c("protection_reset", note="Protection reset", delay=3.0),
        _c("cb_close", note="Restoration: cb_close", delay=3.0),
        _n("All buses supplied. Quiet.", 3.0),
    ],
))

register(Scenario(
    id="planned_outage",
    title="8 — Planned outage and restoration",
    kind="legitimate",
    narrative="Feeder taken off and brought back in the correct order under a program: transfer, open, restore.",
    expect="LOW at most · CTX-001",
    duration_hint=30,
    steps=[
        _c("switching_program_on", "SP-0420:CB-101,SW-102,TS-201", note="Program SP-0420 declared", delay=0.5),
        _c("tie_close", note="Transfer: tie_close", delay=3.0),
        _c("sw_open", note="Transfer: sw_open — B2, B3 from F2", delay=3.0),
        _c("cb_open", note="Outage: cb_open — S1 off", delay=3.0),
        _n("Outage in progress. Hospital still supplied from F2.", 4.0),
        _c("cb_close", note="Restore: cb_close", delay=4.0),
        _c("sw_close", note="Restore: sw_close (short parallel)", delay=3.0),
        _c("tie_open", note="Restore: tie_open", delay=3.0),
        _c("switching_program_off", note="Program SP-0420 closed", delay=3.0),
    ],
))

register(Scenario(
    id="normal_ops",
    title="9 — Normal operations (false-positive check)",
    kind="legitimate",
    narrative="Small AVC trims, a routine tap change and a PV curtailment for a constraint.",
    expect="Quiet",
    duration_hint=24,
    steps=[
        _n("Routine shift activity.", 0.5),
        _c("avc_target", 11.05, note="Trim AVC +0.05 kV", delay=2.0),
        _c("tap_raise", note="Routine manual tap +1", delay=3.0),
        _c("pv_curtail", 20, note="Curtail PV-1 to 20 % for a constraint", delay=3.0),
        _c("tap_lower", note="Tap back −1", delay=3.0),
        _c("avc_target", 11.0, note="Trim AVC back to 11.00 kV", delay=3.0),
        _c("pv_curtail", 0, note="Constraint lifted", delay=3.0),
        _n("Nothing unsafe. Quiet.", 2.0),
    ],
))
