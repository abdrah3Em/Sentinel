"""Per-layer detection tests (PRD sections 16-25)."""
import pytest

from sentinel import config
from sentinel.guard import risk, rules
from sentinel.guard.engine import CommandGuard
from sentinel.guard.state import ProcessState, TelemetryIntegrity
from sentinel.models import Command, Finding, Telemetry
from tests.helpers import Rig


# --- layer 1: state -------------------------------------------------------
def test_outlet_close_is_harmless_when_the_pump_is_stopped():
    rig = Rig()
    rig.send("pump_stop", settle=6)
    alert = rig.send("outlet_close")
    assert alert is None or alert.level == "LOW"


def test_pump_start_below_minimum_level_flags_dry_run():
    rig = Rig(seconds_of_warmup=5, level=5.0, pump=False, inlet_valve=False, mode="MANUAL")
    alert = rig.send("pump_start")
    assert alert is not None
    assert {f["rule"] for f in alert.findings} & {"STATE-002", "STATE-003"}


def test_inlet_isolation_while_pumping_projects_time_to_low_level():
    rig = Rig()
    rig.send("setpoint", 30, settle=1)          # drive the tank down near the low limit
    rig.advance(90)
    alert = rig.send("inlet_close", source="maintenance-laptop")
    assert alert is not None
    details = " ".join(f["detail"] for f in alert.findings)
    assert "low limit in about" in details


# --- layer 3/2: timing and sequence --------------------------------------
def test_rapid_actuator_burst_is_detected():
    rig = Rig()
    for action in ("pump_start", "outlet_close", "outlet_open", "pump_stop", "pump_start"):
        rig.send(action, source="maintenance-laptop", settle=0.8)
    rules_fired = {f["rule"] for a in rig.guard.alerts for f in a.findings}
    assert "SEQ-002" in rules_fired          # rapid sequence
    assert "SEQ-003" in rules_fired          # actuator flapping
    burst_alerts = [a for a in rig.guard.alerts
                    if any(f["rule"] == "SEQ-002" for f in a.findings)]
    assert max(a.score for a in burst_alerts) >= 30


def test_operator_paced_commands_do_not_look_like_a_burst():
    rig = Rig()
    for action in ("outlet_open", "inlet_open", "outlet_open", "inlet_open"):
        rig.send(action, settle=4.0)
    fired = {f["rule"] for a in rig.guard.alerts for f in a.findings}
    assert "SEQ-002" not in fired


def test_known_unsafe_pattern_is_recognised():
    rig = Rig()
    rig.send("pump_stop", settle=2)
    rig.send("pump_start", settle=2)
    alert = rig.send("outlet_close", source="maintenance-laptop")
    assert "SEQ-004" in {f["rule"] for f in alert.findings}


# --- layer 4/5: rate of change and envelope ------------------------------
@pytest.mark.parametrize("value,expect_rule", [(66, None), (78, "ROC-001"), (95, "ENV-001")])
def test_setpoint_grading(value, expect_rule):
    rig = Rig()
    alert = rig.send("setpoint", value)
    fired = {f["rule"] for f in (alert.findings if alert else [])}
    if expect_rule is None:
        assert not fired & {"ENV-001"}
    else:
        assert expect_rule in fired


def test_command_near_the_pressure_limit_adds_risk():
    rig = Rig()
    rig.send("outlet_close", settle=8)           # pressure climbs past the limit
    alert = rig.send("pump_start", source="maintenance-laptop")
    assert "ENV-002" in {f["rule"] for f in alert.findings}


# --- layer 6: context -----------------------------------------------------
def test_maintenance_context_is_a_negative_contribution():
    rig = Rig()
    rig.send("maintenance_on", source="maintenance-hmi", settle=2)
    alert = rig.send("outlet_close", source="maintenance-hmi")
    weights = {f["rule"]: f["weight"] for f in (alert.findings if alert else [])}
    assert weights.get("CTX-001", 0) < 0


def test_untrusted_source_adds_risk_outside_maintenance():
    rig = Rig()
    alert = rig.send("setpoint", 72, source="unknown-host")
    assert "SRC-001" in {f["rule"] for f in alert.findings}


def test_maintenance_laptop_is_accepted_during_maintenance():
    rig = Rig()
    rig.send("maintenance_on", source="maintenance-hmi", settle=2)
    alert = rig.send("setpoint", 72, source="maintenance-laptop")
    fired = {f["rule"] for f in (alert.findings if alert else [])}
    assert "SRC-001" not in fired


# --- layer 7 / physics ----------------------------------------------------
def test_physics_residual_detects_impossible_telemetry():
    state = ProcessState()
    impossible = Telemetry(seq=1, tank_level=60, pump=True, outlet_valve=True, flow=0.0)
    state.update(impossible, arrival=1000.0)
    # A brief mismatch is instrument lag, not an anomaly.
    assert "PHY-001" not in {f.rule for f in rules.evaluate_process(state, now=1001.0)}
    state.update(impossible, arrival=1006.0)
    assert "PHY-001" in {f.rule for f in rules.evaluate_process(state, now=1006.0)}


def test_consistent_telemetry_produces_no_physics_finding():
    rig = Rig()
    findings = rules.evaluate_process(rig.guard.state, rig.now)
    assert "PHY-001" not in {f.rule for f in findings}


def test_integrity_tracks_repeats_and_regressions():
    integrity = TelemetryIntegrity()
    for seq in (1, 2, 3):
        integrity.observe(Telemetry(seq=seq))
    assert integrity.repeat_count == 0 and integrity.regressions == 0
    for _ in range(4):
        integrity.observe(Telemetry(seq=3))
    assert integrity.repeat_count == 4
    integrity.observe(Telemetry(seq=1))
    assert integrity.regressions == 1


# --- risk model -----------------------------------------------------------
def test_score_is_the_sum_of_findings_clamped_to_100():
    findings = [Finding("A", "state", 40, "a"), Finding("B", "state", 35, "b"),
                Finding("C", "context", -30, "c")]
    assert risk.score(findings) == 45
    assert risk.score([Finding("A", "state", 200, "a")]) == 100
    assert risk.score([Finding("A", "context", -50, "a")]) == 0


def test_severity_bands_match_the_prd():
    assert config.severity_for(0) == "LOW"
    assert config.severity_for(29) == "LOW"
    assert config.severity_for(30) == "MEDIUM"
    assert config.severity_for(59) == "MEDIUM"
    assert config.severity_for(60) == "HIGH"
    assert config.severity_for(79) == "HIGH"
    assert config.severity_for(80) == "CRITICAL"
    assert config.severity_for(100) == "CRITICAL"


def test_dominant_rule_names_the_alert():
    findings = [Finding("SEQ-001", "state", 25, "a"), Finding("SEQ-001", "state", 35, "b"),
                Finding("SRC-001", "context", 15, "c")]
    assert risk.dominant_rule(findings) == "SEQ-001"


def test_alert_carries_the_evidence_and_the_mitigations():
    findings = [Finding("SEQ-001", "state", 60, "unsafe"), Finding("CTX-001", "context", -30, "maintenance")]
    alert = risk.build_alert(findings, {"pump": True}, Command("outlet_close"), "MAINTENANCE")
    assert alert.score == 30 and alert.level == "MEDIUM"
    assert "Mitigating context" in alert.why
    assert alert.command["action"] == "outlet_close"


def test_no_findings_means_no_alert():
    assert risk.build_alert([], {}, Command("pump_start")) is None


# --- engine ---------------------------------------------------------------
def test_every_command_produces_an_assessment_even_when_quiet():
    rig = Rig()
    rig.send("outlet_open")
    assert rig.guard.assessments
    assert rig.guard.assessments[-1]["verdict"] in ("NORMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_process_alerts_are_not_repeated_every_second():
    rig = Rig()
    rig.publish_telemetry()
    rig.advance(12, publish=False)
    first = rig.guard.evaluate_process(now=rig.now)
    rig.now += 1.0
    second = rig.guard.evaluate_process(now=rig.now)
    assert first is not None and second is None


def test_status_reports_the_worst_recent_alert():
    rig = Rig()
    rig.send("outlet_close")
    status = rig.guard.status(now=rig.now)
    assert status["level"] in ("HIGH", "CRITICAL")
    assert status["risk_score"] >= 80
    assert "conflicts with running pump" in status["headline"]


def test_stats_group_by_level_and_rule():
    rig = Rig()
    rig.send("outlet_close")
    stats = rig.guard.stats()
    assert stats["alerts_total"] >= 1
    assert sum(stats["by_level"].values()) == stats["alerts_total"]
    assert "SEQ-001" in stats["by_rule"]


# --- context advisory & telemetry restart --------------------------------
def test_suppressed_command_produces_a_context_advisory_not_a_scare():
    """Fully mitigated risk is reported as context, not as an attack."""
    rig = Rig()
    rig.send("setpoint", 40, settle=1)
    rig.advance(120)                      # draw the tank down toward the low limit
    rig.send("maintenance_on", source="maintenance-hmi", settle=2)
    alert = rig.send("inlet_close", source="maintenance-hmi")
    assert alert is not None
    assert alert.rule == "CTX-001" and alert.level == "LOW"
    assert alert.suppressed_score >= 30
    assert "would have scored" in alert.why


def test_unsafe_command_in_maintenance_is_reduced_but_not_silenced():
    """Closing the discharge on a *running* pump is still unsafe in maintenance."""
    rig = Rig()
    rig.send("maintenance_on", source="maintenance-hmi", settle=2)
    alert = rig.send("outlet_close", source="maintenance-hmi")
    assert alert is not None and alert.rule == "SEQ-001"
    assert alert.level == "MEDIUM"        # 85 raw, -30 for maintenance context
    assert "Mitigating context" in alert.why


def test_controller_restart_is_not_a_replay():
    integrity = TelemetryIntegrity()
    for seq in range(1, 30):
        integrity.observe(Telemetry(seq=seq, ts=1000 + seq * 500))
    integrity.observe(Telemetry(seq=1, ts=1000 + 40 * 500))     # fresh ts, counter restarted
    assert integrity.regressions == 0 and integrity.restarts == 1


def test_a_replay_after_a_restart_is_still_caught():
    integrity = TelemetryIntegrity()
    for seq in range(1, 30):
        integrity.observe(Telemetry(seq=seq, ts=1000 + seq * 500))
    integrity.observe(Telemetry(seq=5, ts=1000 + 5 * 500))      # old ts: genuine replay
    assert integrity.regressions == 1


def test_regression_clears_after_a_clean_run():
    integrity = TelemetryIntegrity()
    integrity.observe(Telemetry(seq=10, ts=5000))
    integrity.observe(Telemetry(seq=4, ts=2000))
    assert integrity.regressions == 1
    for seq in range(11, 40):
        integrity.observe(Telemetry(seq=seq, ts=5000 + seq * 500))
    assert integrity.regressions == 0 and integrity.trusted(now=(5000 + 39 * 500) / 1000)


def test_process_alert_reemits_immediately_when_it_escalates():
    """Replay starts as MEDIUM (sequence stuck) and becomes HIGH once the frame is stale too."""
    rig = Rig()
    frozen = rig.publish_telemetry()
    for _ in range(3):
        rig.now += 0.5
        rig.guard.observe_telemetry(frozen, now=rig.now)
    first = rig.guard.evaluate_process(now=rig.now)
    assert first is not None and first.level == "MEDIUM"
    for _ in range(8):                      # keep replaying: the frame ages past the stale limit
        rig.now += 0.5
        rig.guard.observe_telemetry(frozen, now=rig.now)
        alert = rig.guard.evaluate_process(now=rig.now)
        if alert:
            break
    assert alert is not None and alert.level == "HIGH"
    assert {f["rule"] for f in alert.findings} >= {"TEL-001", "TEL-002"}


def test_reset_forgets_history_and_status():
    rig = Rig()
    rig.send("outlet_close")
    assert rig.guard.status(now=rig.now)["level"] != "NORMAL"
    rig.guard.reset()
    assert rig.guard.status(now=rig.now)["level"] == "NORMAL"
    assert rig.guard.commands_seen == 0 and not rig.guard.alerts
