"""Per-rule checks for the feeder detection layers."""
from sentinel import config
from tests.helpers import Rig

ATTACKER = "engineering-laptop"


def rules_of(alert):
    return {f["rule"] for f in alert.findings} if alert else set()


def test_open_with_the_tie_closed_transfers_load_and_stays_quiet():
    rig = Rig()
    rig.send("tie_close", settle=3)
    alert = rig.send("cb_open")
    assert alert is None or alert.level == "LOW"
    assert rig.plant.supplied["b3"]


def test_sectionaliser_open_isolating_the_hospital():
    rig = Rig()
    alert = rig.send("sw_open", source=ATTACKER)
    assert alert is not None and alert.rule == "STATE-005"
    assert "Hospital" in " ".join(f["detail"] for f in alert.findings)


def test_tap_raise_with_busbar_over_the_limit():
    rig = Rig()
    rig.send("avc_manual", settle=1)
    rig.send("tap_set", 7, settle=8)
    assert rig.plant.v_bus_kv > config.GRID_V_MAX_KV
    alert = rig.send("tap_raise")
    assert "STATE-006" in rules_of(alert)


def test_curtailing_pv_on_a_heavily_loaded_feeder():
    saved = dict(config.GRID_LOADS_KW)
    try:
        for b in config.GRID_LOADS_KW:            # load the feeder up to near its rating
            config.GRID_LOADS_KW[b] = saved[b] * 1.7
        rig = Rig(seconds_of_warmup=5)
        rig.plant.clock = config.GRID_DIURNAL_PERIOD_S / 4       # diurnal peak
        rig.advance(3)
        assert rig.plant.i_feeder_a > config.GRID_I_WARN_A
        alert = rig.send("pv_curtail", 100, source=ATTACKER)
    finally:
        config.GRID_LOADS_KW.update(saved)
    assert "STATE-007" in rules_of(alert)


def test_breaker_pumping_and_rapid_sequence():
    rig = Rig()
    alerts = [rig.send(a, source=ATTACKER, settle=0.8)
              for a in ("cb_open", "cb_close", "cb_open", "cb_close", "tie_close")]
    found = set().union(*(rules_of(a) for a in alerts))
    assert {"SEQ-002", "SEQ-003", "STATE-003"} <= found


def test_reset_then_close_with_fault_present_is_a_known_pattern():
    rig = Rig()
    rig.sim("fault_inject", "S2", settle=3)
    rig.send("protection_reset", source=ATTACKER, settle=1)
    alert = rig.send("cb_close", source=ATTACKER)
    assert {"STATE-001", "SEQ-004"} <= rules_of(alert)


def test_program_context_is_a_negative_contribution():
    rig = Rig()
    rig.send("switching_program_on", "SP-1", settle=2)
    alert = rig.send("tie_close")
    assert alert is None or any(f["weight"] < 0 and f["rule"] == "CTX-001" for f in alert.findings)


def test_engineering_laptop_is_accepted_inside_a_program():
    rig = Rig()
    rig.send("switching_program_on", "SP-1", settle=2)
    assert rig.send("pv_curtail", 10, source=ATTACKER) is None


def test_unknown_source_adds_risk():
    rig = Rig()
    alert = rig.send("pv_curtail", 10, source="unknown-host")
    assert alert is None or "SRC-001" in rules_of(alert)


def test_physics_residual_catches_an_impossible_busbar_reading():
    rig = Rig()
    frame = rig.publish_telemetry()
    for _ in range(12):
        rig.now += 0.5
        frame = dict(frame, seq=frame["seq"] + 1, ts=int(rig.now * 1000), v_bus_kv=frame["v_bus_kv"] + 1.0)
        rig.guard.observe_telemetry(frame, now=rig.now)
    alert = rig.guard.evaluate_process(now=rig.now)
    assert alert is not None and alert.rule == "PHY-001"
    assert alert.confidence == "REDUCED"


def test_rule_catalogue_and_scenarios_are_exposed_for_the_grid():
    from sentinel.attacks.scenarios import catalogue as scenarios
    from sentinel.guard.catalogue import catalogue, thresholds
    ids = {r["id"] for r in catalogue()}
    assert {"STATE-001", "STATE-004", "ROC-002", "CTX-001", "TEL-002"} <= ids
    assert all(r["title"] for r in catalogue())
    assert thresholds()["envelope"]["voltage_kv"] == [config.GRID_V_MIN_KV, config.GRID_V_MAX_KV]
    assert {s["id"] for s in scenarios()} >= {"close_onto_fault", "planned_switching", "telemetry_replay"}


def test_program_coverage_only_excuses_named_equipment():
    rig = Rig()
    rig.send("switching_program_on", "SP-9:TS-201", settle=2)
    covered = rig.send("tie_close", settle=3)
    assert covered is None or covered.level == "LOW"
    rig.send("tie_open", settle=3)
    uncovered = rig.send("cb_open")
    assert uncovered is not None and uncovered.rule == "STATE-002"
    assert config.SEVERITY_ORDER[uncovered.level] >= config.SEVERITY_ORDER["HIGH"]


def test_energising_any_of_several_permitted_sections_is_high():
    rig = Rig()
    rig.send("switching_program_on", "SP-7:CB-101,SW-102,S2,S3", settle=2)
    rig.send("sw_open", settle=3)
    rig.send("ptw_issue", "S2,S3", settle=2)
    alert = rig.send("sw_close")
    assert alert is not None and alert.rule == "STATE-004"
    assert "S2, S3" in " ".join(f["detail"] for f in alert.findings)


def test_opening_the_second_feeder_breaker_under_load_is_flagged():
    rig = Rig()
    alert = rig.send("cb2_open", source="engineering-laptop")
    assert alert is not None and alert.rule == "STATE-002"
    assert "Commercial bus B4" in " ".join(f["detail"] for f in alert.findings)
