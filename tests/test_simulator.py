"""The physics has to be right, or the security story means nothing."""
from sentinel import config
from sentinel.plant.simulator import Plant


def run(plant: Plant, seconds: float) -> None:
    for _ in range(int(seconds / 0.1)):
        plant.step(0.1)


def test_normal_operation_is_stable_and_inside_the_envelope():
    plant = Plant(noise=False)
    run(plant, 180)
    assert config.LEVEL_MIN_PCT < plant.level < config.LEVEL_MAX_PCT
    assert 2.0 < plant.pressure < config.PRESSURE_MAX_BAR
    assert 70 < plant.flow < config.FLOW_MAX_LPM


def test_auto_mode_holds_level_near_setpoint():
    plant = Plant(noise=False)
    run(plant, 60)
    plant.apply("setpoint", 45)
    run(plant, 180)
    assert abs(plant.level - 45) < 6


def test_closing_outlet_on_a_running_pump_deadheads_the_pump():
    plant = Plant(noise=False)
    run(plant, 30)
    plant.apply("outlet_close")
    run(plant, 10)
    assert plant.flow < 1.0
    assert plant.pressure > config.PRESSURE_MAX_BAR
    assert plant.deadhead_s > 8


def test_pump_stopped_means_no_flow_and_static_pressure():
    plant = Plant(noise=False)
    run(plant, 20)
    plant.apply("pump_stop")
    run(plant, 10)
    assert plant.flow < 1.0
    assert plant.pressure < 2.0


def test_low_level_causes_cavitation_not_full_flow():
    plant = Plant(noise=False, level=4.0, inlet_valve=False, mode="MANUAL")
    run(plant, 10)
    assert plant.flow < config.PUMP_RATED_FLOW_LPM * 0.6
    assert plant.dry_run_s > 5


def test_physical_events_fire_on_the_edge_only():
    plant = Plant(noise=False)
    run(plant, 20)
    plant.apply("outlet_close")
    seen = []
    for _ in range(200):
        seen += [e.type for e in plant.step(0.1)]
    assert seen.count("PHYSICAL") == 2          # deadhead + overpressure, once each
