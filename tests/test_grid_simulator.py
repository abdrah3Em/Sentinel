"""Feeder physics: supply paths, voltage drop, protection, close-onto-fault, parallel, AVC."""
from sentinel import config
from sentinel.plant.grid import Feeder, topology


def run(feeder: Feeder, seconds: float) -> list:
    events = []
    for _ in range(int(seconds / 0.1)):
        events += feeder.step(0.1)
    return events


def test_healthy_feeder_is_inside_statutory_limits():
    f = Feeder(noise=False)
    run(f, 60)
    t = f.telemetry()
    assert all(t.supplied.values())
    assert config.GRID_V_MIN_KV < t.v_b3_kv < t.v_b2_kv < t.v_b1_kv <= t.v_bus_kv < config.GRID_V_MAX_KV
    assert 100 < t.i_feeder_a < config.GRID_I_WARN_A
    assert t.customers_off == 0 and t.cml == 0


def test_supply_paths_follow_the_switchgear():
    assert topology(True, True, False)["buses"] == {"b1": True, "b2": True, "b3": True}
    assert topology(False, True, False)["buses"] == {"b1": False, "b2": False, "b3": False}
    assert topology(True, False, False)["buses"] == {"b1": True, "b2": False, "b3": False}
    assert topology(False, False, True)["buses"] == {"b1": False, "b2": True, "b3": True}
    assert topology(True, True, True)["parallel"] is True


def test_fault_trips_the_breaker_and_drops_the_hospital():
    f = Feeder(noise=False)
    run(f, 10)
    f.sim_hook({"fault_inject": "S2"})
    events = run(f, 1)
    t = f.telemetry()
    assert not t.cb_closed and t.protection_tripped
    assert t.fault_present and t.fault_indicators == [False, True, False]
    assert not t.supplied["b3"] and t.v_b3_kv == 0
    assert any("tripped CB-101" in e.detail for e in events)
    run(f, 30)
    assert f.customers_off == sum(config.GRID_CUSTOMERS.values()) and f.cml > 0


def test_close_onto_fault_flows_fault_current_and_retrips():
    f = Feeder(noise=False)
    run(f, 10)
    f.sim_hook({"fault_inject": "S2"})
    run(f, 1)
    events = f.apply("cb_close")
    assert any("6.5 kA" in e.detail for e in events)
    assert f.cb_closed
    f.step(0.1)
    assert f.fault_current_ka == config.GRID_FAULT_CURRENT_KA
    run(f, 1)
    t = f.telemetry()
    assert not t.cb_closed and t.close_onto_fault_count == 1 and t.switchgear_stress > 0


def test_cleared_fault_and_reset_allow_a_clean_restoration():
    f = Feeder(noise=False)
    run(f, 10)
    f.sim_hook({"fault_inject": "S3"})
    run(f, 1)
    f.sim_hook({"fault_clear": True})
    f.apply("protection_reset")
    f.apply("cb_close")
    run(f, 2)
    t = f.telemetry()
    assert t.cb_closed and all(t.supplied.values()) and t.close_onto_fault_count == 0
    assert t.trip_age_s is not None


def test_avc_walks_the_tap_to_the_target_and_out_of_limits():
    f = Feeder(noise=False)
    run(f, 10)
    f.apply("avc_target", 11.8)
    run(f, 40)
    assert f.tap >= 5
    assert f.v_bus_kv > config.GRID_V_MAX_KV
    f.apply("avc_target", 11.0)
    run(f, 40)
    assert abs(f.v_bus_kv - 11.0) < 0.1


def test_manual_tap_overrides_the_avc_for_a_while():
    f = Feeder(noise=False)
    run(f, 10)
    f.apply("tap_raise")
    assert f.tap == 1
    run(f, 10)
    assert f.tap == 1                      # AVC held off by the manual override
    run(f, config.GRID_MANUAL_OVERRIDE_S)
    assert f.tap == 0                      # ...then brings the busbar back to target


def test_tie_close_with_everything_closed_is_a_parallel():
    f = Feeder(noise=False)
    run(f, 5)
    f.apply("tap_raise")
    f.apply("tie_close")
    run(f, 5)
    t = f.telemetry()
    assert t.parallel_s > 4 and t.circulating_a > 0
    events = run(f, config.GRID_PARALLEL_STANDING_S)
    assert any("Standing parallel" in e.detail for e in events)


def test_load_transfer_through_the_tie_keeps_the_hospital_supplied():
    f = Feeder(noise=False)
    run(f, 5)
    f.apply("tie_close")
    f.apply("sw_open")
    f.apply("cb_open")
    run(f, 2)
    t = f.telemetry()
    assert not t.supplied["b1"] and t.supplied["b2"] and t.supplied["b3"]
    assert t.i_feeder_a == 0 and t.v_b3_kv > config.GRID_V_MIN_KV
