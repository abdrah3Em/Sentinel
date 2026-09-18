"""PRD revision 2, section 52 — acceptance criteria for the distribution feeder, one test each."""
from sentinel import config
from sentinel.models import Command
from tests.helpers import Rig

HIGH = config.SEVERITY_ORDER["HIGH"]
MEDIUM = config.SEVERITY_ORDER["MEDIUM"]
LOW = config.SEVERITY_ORDER["LOW"]
ATTACKER = "engineering-laptop"


def rank(level: str) -> int:
    return config.SEVERITY_ORDER[level]


def worst_rank(rig: Rig) -> int:
    return max((rank(a.level) for a in rig.guard.alerts), default=-1)


# --- T1: normal operation, planned switching, fault-and-restore are quiet ----
def test_normal_operations_produce_no_high_advisory():
    rig = Rig()
    rig.send("avc_target", 11.05, settle=3)
    rig.send("pv_curtail", 20, settle=3)
    rig.send("avc_target", 11.0, settle=3)
    rig.send("pv_curtail", 0, settle=3)
    rig.advance(20)
    rig.guard.evaluate_process(now=rig.now)
    assert worst_rank(rig) < MEDIUM, [(a.level, a.rule) for a in rig.guard.alerts]


def test_planned_switching_program_stays_at_low_or_below():
    rig = Rig()
    rig.send("switching_program_on", "SP-0417", settle=2)
    for action in ("tie_close", "sw_open", "cb_open"):
        rig.send(action, settle=4)
    rig.send("ptw_issue", "S1", settle=2)
    rig.send("ptw_cancel", settle=2)
    for action in ("cb_close", "sw_close", "tie_open", "switching_program_off"):
        rig.send(action, settle=4)
    assert worst_rank(rig) <= LOW, [(a.level, a.rule, a.score) for a in rig.guard.alerts]
    assert rig.plant.supplied == {"b1": True, "b2": True, "b3": True}


def test_fault_locate_clear_restore_is_quiet():
    rig = Rig()
    rig.sim("fault_inject", "S3", settle=3)
    rig.sim("fault_clear", settle=2)
    assert rig.send("protection_reset", settle=2) is None
    assert rig.send("cb_close", settle=2) is None
    assert rig.plant.supplied["b3"]


# --- T2: close onto a standing fault ----------------------------------------
def test_close_onto_fault_is_critical_and_names_the_fault():
    rig = Rig()
    rig.sim("fault_inject", "S2", settle=3)
    alert = rig.send("cb_close", source=ATTACKER)
    assert alert is not None and alert.rule == "STATE-001"
    assert rank(alert.level) >= HIGH and alert.score >= 80
    details = " ".join(f["detail"] for f in alert.findings)
    assert "FI-2" in details and "S2" in details and "Hospital" in details
    assert "engineering-laptop" in details


# --- T3: open a healthy feeder with no alternate path -----------------------
def test_open_healthy_feeder_names_the_hospital():
    rig = Rig()
    alert = rig.send("cb_open", source=ATTACKER)
    assert alert is not None and alert.rule == "STATE-002" and rank(alert.level) >= HIGH
    assert "Hospital bus B3" in " ".join(f["detail"] for f in alert.findings)
    rig.advance(5)
    assert rig.plant.customers_off > 0


# --- T4: tie close creating a parallel ---------------------------------------
def test_uncontrolled_parallel_is_at_least_medium():
    rig = Rig()
    alert = rig.send("tie_close")
    assert alert is not None and alert.rule == "STATE-003" and rank(alert.level) >= MEDIUM


# --- T5: setpoint step and slow drift ----------------------------------------
def test_large_avc_target_step_is_at_least_medium():
    rig = Rig()
    alert = rig.send("avc_target", 11.9, source=ATTACKER)
    assert alert is not None and rank(alert.level) >= MEDIUM
    assert {f["rule"] for f in alert.findings} >= {"ROC-001", "ENV-001"}


def test_slow_drift_is_flagged_before_the_limit_with_time_to_limit():
    rig = Rig()
    flagged_inside_band, crossed = None, None
    for value in (11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8):
        alert = rig.send("avc_target", value, source=ATTACKER, settle=4)
        if alert and alert.rule == "ROC-002":
            if value <= config.GRID_V_MAX_KV and flagged_inside_band is None:
                flagged_inside_band = alert
            if value > config.GRID_V_MAX_KV:
                crossed = alert
    assert flagged_inside_band is not None and rank(flagged_inside_band.level) >= MEDIUM
    assert "crosses the" in " ".join(f["detail"] for f in flagged_inside_band.findings)
    assert crossed is not None and rank(crossed.level) >= HIGH


# --- T6: identical sequence with and without a program -----------------------
def test_same_sequence_outside_a_program_is_high():
    rig = Rig()
    alerts = [rig.send(a, settle=4) for a in ("tie_close", "sw_open", "cb_open")]
    assert max(rank(a.level) for a in alerts if a) >= HIGH


# --- T7: energising under permit is HIGH even inside a program ---------------
def test_energising_a_permitted_section_is_high_inside_a_program():
    rig = Rig()
    rig.send("switching_program_on", "SP-0417", settle=2)
    rig.send("tie_close", settle=4)
    rig.send("sw_open", settle=4)
    rig.send("cb_open", settle=6)
    rig.send("ptw_issue", "S1", settle=6)
    alert = rig.send("cb_close")
    assert alert is not None and alert.rule == "STATE-004" and rank(alert.level) >= HIGH


# --- T8: replayed telemetry and commands, confidence LOW ---------------------
def test_replay_is_flagged_and_confidence_is_low():
    rig = Rig()
    frame = rig.publish_telemetry()
    for _ in range(6):
        rig.now += 0.5
        rig.guard.observe_telemetry(frame, now=rig.now)
    process_alert = rig.guard.evaluate_process(now=rig.now)
    assert process_alert is not None and process_alert.rule == "TEL-002"
    assert process_alert.confidence == "LOW" and "replay" in process_alert.uncertainty.lower()
    alert = rig.send("sw_open", source=ATTACKER, settle=0)
    assert alert is not None and alert.confidence == "LOW"


def test_replayed_command_is_flagged():
    rig = Rig()
    original = Command(action="pv_curtail", source="operator-hmi", value=0)
    original.ts = int(rig.now * 1000)
    rig.guard.observe_command(original.to_dict(), now=rig.now)
    rig.advance(5)
    replayed = dict(original.to_dict(), ts=original.ts - 300_000)
    alert = rig.guard.observe_command(replayed, now=rig.now)
    assert alert is not None and alert.rule == "CMD-001" and rank(alert.level) >= HIGH


# --- T9 / T10: explainable, and never acts -----------------------------------
def test_every_advisory_explains_what_where_why_and_what_to_verify():
    rig = Rig()
    rig.sim("fault_inject", "S2", settle=3)
    rig.send("cb_close", source=ATTACKER)
    rig.send("tie_close", settle=2)
    assert rig.guard.alerts
    for alert in rig.guard.alerts:
        assert alert.summary and alert.equipment and alert.why and alert.recommendation
        assert alert.findings and alert.confidence in ("HIGH", "REDUCED", "LOW") and alert.uncertainty


def test_guard_never_operates_switchgear():
    rig = Rig()
    before = (rig.plant.cb_closed, rig.plant.sw_closed, rig.plant.tie_closed, rig.plant.tap)
    rig.sim("fault_inject", "S2", settle=3)
    tripped = (rig.plant.cb_closed, rig.plant.sw_closed, rig.plant.tie_closed, rig.plant.tap)
    # The guard sees a CRITICAL command; only the plant's own apply() changes anything.
    command = {"action": "cb_close", "source": ATTACKER, "id": "x1", "ts": int(rig.now * 1000)}
    rig.guard.observe_command(command, now=rig.now)
    assert (rig.plant.cb_closed, rig.plant.sw_closed, rig.plant.tie_closed, rig.plant.tap) == tripped
    assert before != tripped   # protection, not the guard, opened the breaker
