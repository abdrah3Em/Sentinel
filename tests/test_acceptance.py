"""PRD section 52 — acceptance criteria, one test each.

Severity note: the PRD's example calculation labels a score of 95 as "HIGH"
while its own banding table puts 80-100 in CRITICAL. The banding table wins, so
these tests assert "at least HIGH".
"""
import pytest

from sentinel import config
from sentinel.models import Telemetry
from tests.helpers import Rig

HIGH = config.SEVERITY_ORDER["HIGH"]
MEDIUM = config.SEVERITY_ORDER["MEDIUM"]


def rank(level: str) -> int:
    return config.SEVERITY_ORDER[level]


# --- Test 1 ---------------------------------------------------------------
def test_normal_operation_produces_no_high_alert():
    rig = Rig()
    rig.send("setpoint", 62, settle=4)
    rig.send("inlet_open", settle=4)
    rig.send("setpoint", 58, settle=4)
    rig.send("outlet_open", settle=4)
    rig.advance(30)
    rig.guard.evaluate_process(now=rig.now)
    assert all(rank(a.level) < HIGH for a in rig.guard.alerts), \
        [(a.level, a.rule, a.summary) for a in rig.guard.alerts]


# --- Test 2 ---------------------------------------------------------------
def test_outlet_close_with_pump_running_is_high_risk():
    rig = Rig()
    alert = rig.send("outlet_close")
    assert alert is not None
    assert rank(alert.level) >= HIGH
    assert alert.rule == "SEQ-001"
    assert alert.score >= 80
    reasons = " ".join(f["detail"] for f in alert.findings)
    assert "Pump is running" in reasons and "discharge path" in reasons


# --- Test 3 ---------------------------------------------------------------
def test_pump_start_with_closed_outlet_is_high_risk():
    rig = Rig()
    rig.send("pump_stop", settle=3)
    rig.send("outlet_close", settle=3)
    alert = rig.send("pump_start")
    assert alert is not None and alert.rule == "STATE-002"
    assert rank(alert.level) >= HIGH


# --- Test 4 ---------------------------------------------------------------
def test_large_setpoint_jump_is_medium_or_high():
    rig = Rig()
    alert = rig.send("setpoint", 95, source="maintenance-laptop")
    assert alert is not None
    assert rank(alert.level) >= MEDIUM
    rules = {f["rule"] for f in alert.findings}
    assert {"ROC-001", "ENV-001"} <= rules


def test_small_setpoint_trim_stays_quiet():
    rig = Rig()
    alert = rig.send("setpoint", 63)
    assert alert is None or rank(alert.level) < MEDIUM


# --- Test 5 ---------------------------------------------------------------
def test_maintenance_context_suppresses_the_same_sequence():
    rig = Rig()
    rig.send("maintenance_on", source="maintenance-hmi", settle=2)
    rig.send("pump_stop", source="maintenance-hmi", settle=3)
    alert = rig.send("outlet_close", source="maintenance-hmi")
    assert alert is None or rank(alert.level) < HIGH, (alert.level, alert.score, alert.summary)


def test_identical_sequence_in_auto_mode_is_not_suppressed():
    """The mirror image of the test above — context is what changes the verdict."""
    rig = Rig()
    alert = rig.send("outlet_close", source="maintenance-hmi")
    assert alert is not None and rank(alert.level) >= HIGH


# --- Test 6 ---------------------------------------------------------------
def test_replayed_telemetry_is_flagged():
    rig = Rig()
    frozen = rig.publish_telemetry()
    for _ in range(8):                       # same frame over and over
        rig.now += 0.5
        rig.guard.observe_telemetry(frozen, now=rig.now)
    alert = rig.guard.evaluate_process(now=rig.now)
    assert alert is not None
    assert alert.rule in ("TEL-001", "TEL-002")
    assert rank(alert.level) >= config.SEVERITY_ORDER["MEDIUM"]
    assert rig.guard.status(now=rig.now)["telemetry_trusted"] is False


def test_stale_telemetry_is_flagged_even_without_replay():
    rig = Rig()
    rig.publish_telemetry()
    rig.advance(12, publish=False)
    alert = rig.guard.evaluate_process(now=rig.now)
    assert alert is not None and alert.rule == "TEL-001"
    assert rig.guard.status(now=rig.now)["telemetry_fresh"] is False


def test_sequence_regression_is_flagged():
    rig = Rig()
    frame = rig.publish_telemetry()
    old = dict(frame, seq=frame["seq"] - 50, ts=frame["ts"] - 30000)
    rig.now += 0.5
    rig.guard.observe_telemetry(old, now=rig.now)
    findings = {f.rule for f in
                __import__("sentinel.guard.rules", fromlist=["x"]).evaluate_process(rig.guard.state, rig.now)}
    assert "TEL-003" in findings
    # A replayed old frame must not overwrite the newer process picture.
    assert rig.guard.state.telemetry.seq == frame["seq"]


# --- Test 7 ---------------------------------------------------------------
def test_every_high_alert_explains_itself():
    rig = Rig()
    alert = rig.send("outlet_close")
    assert alert.why and len(alert.why) > 80
    assert alert.recommendation and len(alert.recommendation) > 40
    assert alert.equipment
    assert alert.findings and all(f["detail"] for f in alert.findings)
    assert alert.state["pump"] is True          # the state it reasoned about is attached
    assert sum(f["weight"] for f in alert.findings) >= alert.score


# --- Test 8 ---------------------------------------------------------------
def test_guard_never_acts_on_the_plant():
    """FR-011: the guard produces advisories only — it holds no write path."""
    rig = Rig()
    before = rig.plant.snapshot()
    rig.guard.observe_command({"action": "outlet_close", "source": "attacker"}, now=rig.now)
    rig.guard.evaluate_process(now=rig.now)
    after = rig.plant.snapshot()
    for key in ("pump", "outlet_valve", "inlet_valve", "setpoint", "mode", "maintenance"):
        assert before[key] == after[key]
    guard_api = dir(rig.guard)
    assert not any(name.startswith(("send", "command_", "actuate", "trip", "shutdown"))
                   for name in guard_api)


# --- Challenge brief: the attack patterns named in the E1 text ------------
def test_slow_setpoint_drift_is_caught_before_it_leaves_the_band():
    """'A setpoint moved a little at a time until it leaves the safe range.'"""
    rig = Rig()
    verdicts = {}
    for sp in range(64, 96, 4):                      # +4 % every 20 s, each step inside ±5 %
        alert = rig.send("setpoint", sp, source="maintenance-laptop", settle=20)
        verdicts[sp] = alert
    inside_band = [sp for sp in verdicts if sp <= config.SETPOINT_MAX and verdicts[sp]
                   and "ROC-002" in {f["rule"] for f in verdicts[sp].findings}]
    assert inside_band, "drift must be flagged while the setpoint is still inside the safe band"
    first = verdicts[inside_band[0]]
    assert rank(first.level) >= MEDIUM
    assert any("leaves" in f["detail"] for f in first.findings)      # trajectory projected
    assert rank(verdicts[92].level) >= HIGH                         # and HIGH once outside


def test_replayed_command_is_flagged():
    """'Replaying old traffic' — same message id, old timestamp."""
    rig = Rig()
    from sentinel.models import Command
    original = Command("outlet_open"); original.ts = int(rig.now * 1000)
    assert rig.guard.observe_command(original.to_dict(), now=rig.now) is None
    rig.advance(5)
    replayed = dict(original.to_dict(), ts=original.ts - 300_000)
    alert = rig.guard.observe_command(replayed, now=rig.now)
    assert alert is not None and alert.rule == "CMD-001"
    assert rank(alert.level) >= HIGH
    details = " ".join(f["detail"] for f in alert.findings)
    assert "already been processed" in details and "s old" in details


def test_replayed_mode_change_is_not_ignored():
    rig = Rig()
    from sentinel.models import Command
    stale = Command("maintenance_off", source="maintenance-hmi"); stale.ts = int(rig.now * 1000) - 600_000
    alert = rig.guard.observe_command(stale.to_dict(), now=rig.now)
    assert alert is not None and "CMD-001" in {f["rule"] for f in alert.findings}


def test_planned_shutdown_and_startup_are_quiet():
    rig = Rig()
    for action, wait in (("pump_stop", 6), ("outlet_close", 4), ("inlet_close", 4)):
        alert = rig.send(action, settle=wait)
        assert alert is None, (action, alert.level, alert.summary)
    rig.advance(30)                                   # isolated for a while, then restarted
    for action, wait in (("inlet_open", 3), ("outlet_open", 4), ("pump_start", 4)):
        alert = rig.send(action, settle=wait)
        assert alert is None, (action, alert.level, alert.summary)


def test_every_advisory_states_its_confidence_and_what_it_does_when_unsure():
    rig = Rig()
    fresh = rig.send("outlet_close")
    assert fresh.confidence == "HIGH" and "has not acted" in fresh.uncertainty
    rig.send("outlet_open", settle=3); rig.send("pump_stop", settle=3)
    frozen = rig.publish_telemetry()
    for _ in range(8):
        rig.now += 0.5
        rig.guard.observe_telemetry(frozen, now=rig.now)
    unsure = rig.send("setpoint", 95, settle=0)
    assert unsure.confidence == "LOW"
    assert "does not block" in unsure.uncertainty and "confirmed locally" in unsure.uncertainty
    assert unsure.score >= 60                        # uncertainty raises the advisory, never hides it
