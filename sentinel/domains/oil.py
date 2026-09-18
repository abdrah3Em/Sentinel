"""Process domain: crude oil pumping station — storage tank, transfer pump, pipeline valves.

The hydraulics (dead-heading, cavitation, level limits) are the liquid-transfer
model from PRD revision 1, relabelled for an energy asset."""
from __future__ import annotations

from typing import Any

from .. import config, process
from ..attacks.scenarios import SCENARIOS as TANK_SCENARIOS
from ..guard import catalogue as _catalogue, rules as _rules
from ..guard.risk import RULE_NARRATIVE as NARRATIVES
from ..models import Telemetry
from ..plant.simulator import ALL_ACTIONS, Plant

ID = "oil"
TITLE = "Crude oil pumping station · T-101 / P-101 · simulated"
SHORT = "Oil pipeline simulation"
CONSEQUENTIAL = {"pump_start", "pump_stop", "outlet_open", "outlet_close", "inlet_open", "inlet_close", "setpoint"}
COMMAND_RULES = _rules.COMMAND_RULES
PROCESS_RULES = _rules.PROCESS_RULES
NARRATIVE_VARS = {"shutoff": config.PUMP_SHUTOFF_HEAD_BAR, "limit": config.PRESSURE_MAX_BAR}
RULES = _catalogue.RULES
SCENARIOS = TANK_SCENARIOS
PHYSICS_THRESHOLD = config.PHYSICS_RESIDUAL_LPM
PHYSICS_LABEL = "flow"
TREND_KEYS = ["tank_level", "pressure", "flow", "setpoint"]

# Modbus TCP map (PRD: the controller obeys any well-formed write)
MODBUS = {
    "coils": {0: ("pump_start", "pump_stop"), 1: ("outlet_open", "outlet_close"),
              2: ("inlet_open", "inlet_close"), 3: ("maintenance_on", "maintenance_off")},
    "registers": {0: ("setpoint", 1.0)},
    "inputs": [("tank_level", 10), ("pressure", 100), ("flow", 1), ("setpoint", 1), ("pump", 1),
               ("outlet_valve", 1), ("inlet_valve", 1), ("maintenance", 1)],
}


def thresholds() -> dict:
    return _catalogue.tank_thresholds()


def context_label(t: Telemetry | None) -> str:
    if t is None:
        return "UNKNOWN"
    return "MAINTENANCE" if (t.maintenance or t.mode == "MAINTENANCE") else t.mode


def setpoint_of(t: Telemetry) -> float:
    return t.setpoint


def expected_flow(t: Telemetry) -> float:
    """Digital twin: the flow the physics says we should be seeing."""
    if not t.pump or not t.outlet_valve:
        return 0.0
    suction = 1.0 if t.tank_level >= config.PUMP_MIN_SUCTION_LEVEL else max(
        0.0, t.tank_level / config.PUMP_MIN_SUCTION_LEVEL) ** 1.5
    return config.PUMP_RATED_FLOW_LPM * (0.82 + 0.18 * (t.tank_level / 100.0)) * suction


def physics_residual(t: Telemetry) -> tuple[float, float]:
    expected = expected_flow(t)
    return expected, abs(expected - t.flow)


def mode_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"mode": snapshot["mode"], "maintenance": snapshot["maintenance"], "setpoint": snapshot["setpoint"]}


def descriptor() -> dict[str, Any]:
    """What the dashboard needs to draw this process."""
    return {
        "id": ID, "title": TITLE, "short": SHORT, "brand": "Oil pipeline simulation", "consoles": process.consoles(),
        "process_sub": "Storage tank → transfer pump → pipeline",
        "kpis": [
            {"key": "tank_level", "label": "Crude tank level", "unit": "%", "dp": 1, "min": 0, "max": 100, "spark": "green",
             "foot": "SP {setpoint:0} % · band 20–90"},
            {"key": "pressure", "label": "Pipeline pressure", "unit": "bar", "dp": 2, "min": 0, "max": 7,
             "spark": "orange", "foot": "Limit 5.0 · shut-off 6.4 bar"},
            {"key": "flow", "label": "Pipeline flow", "unit": "m³/h", "dp": 0, "min": 0, "max": 100,
             "spark": "green", "foot": "Physics: {expected_flow:0} m³/h"},
            {"key": "setpoint", "label": "Level setpoint", "unit": "%", "dp": 0, "min": 0, "max": 100,
             "spark": "muted", "foot": "Band 20–90 · trim ±5 %"},
        ],
        "equipment": [
            {"key": "pump", "label": "Transfer pump P-101", "icon": "pump"},
            {"key": "outlet", "label": "Pipeline valve V-102", "icon": "valve"},
            {"key": "inlet", "label": "Gathering inlet V-101", "icon": "valve"},
            {"key": "mode", "label": "Operating mode", "icon": "mode"},
        ],
        "trend": {
            "series": [
                {"key": "tank_level", "label": "Level %", "color": "text", "axis": "left"},
                {"key": "flow", "label": "Flow m³/h", "color": "green", "axis": "left"},
                {"key": "pressure", "label": "Pressure bar", "color": "orange", "axis": "right"},
                {"key": "setpoint", "label": "Setpoint", "color": "muted", "axis": "left", "dash": True},
            ],
            "left": {"min": 0, "max": 100, "ticks": [0, 25, 50, 75, 100]},
            "right": {"min": 0, "max": 7, "ticks": [0, 2, 4, 6]},
            "limits": [{"axis": "left", "value": config.LEVEL_MAX_PCT}, {"axis": "left", "value": config.LEVEL_MIN_PCT},
                       {"axis": "right", "value": config.PRESSURE_MAX_BAR, "danger": True}],
        },
        "state_rows": [
            ["Tank level", "tank_level", {"kind": "num", "dp": 1, "unit": " %"}],
            ["Pressure", "pressure", {"kind": "num", "dp": 2, "unit": " bar"}],
            ["Flow", "flow", {"kind": "num", "dp": 0, "unit": " m³/h"}],
            ["Pump", "pump", {"kind": "bool", "on": "Running", "off": "Stopped"}],
            ["Pipeline V-102", "outlet_valve", {"kind": "bool", "on": "Open", "off": "Closed"}],
            ["Mode", "mode", {"kind": "text", "title": True}],
        ],
        "console": {
            "buttons": [{"action": a, "label": l} for a, l in (
                ("pump_start", "Pump start"), ("pump_stop", "Pump stop"), ("outlet_open", "Pipeline valve open"),
                ("outlet_close", "Pipeline valve close"), ("inlet_open", "Inlet open"), ("inlet_close", "Inlet close"),
                ("maintenance_on", "Maintenance on"), ("maintenance_off", "Maintenance off"))],
            "inputs": [{"action": "setpoint", "label": "Setpoint", "type": "number", "default": 60,
                        "min": 0, "max": 100, "step": 1}],
            "sim": [],
            "sources": [{"value": "operator-hmi", "label": "operator-hmi · trusted"},
                        {"value": "maintenance-hmi", "label": "maintenance-hmi"},
                        {"value": "maintenance-laptop", "label": "maintenance-laptop"},
                        {"value": "unknown-host", "label": "unknown-host"}],
        },
    }
