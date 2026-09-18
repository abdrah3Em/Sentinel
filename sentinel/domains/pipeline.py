"""Process domain: crude oil pipeline pump station (portability proof for the detector core).

Tank farm T-101 feeds mainline pump P-101 (P-102 standby) through ESD-301, the
station inlet emergency-shutdown valve; MOV-201 is the sectionalising valve on
the mainline; a drag-reducing agent (DRA) skid trims friction on the segment.
The hydraulics (surge on a valve slam, cavitation on low suction, ullage limits)
are the liquid-transfer model the detector core was first validated on."""
from __future__ import annotations

from typing import Any

from .. import config, process
from ..attacks.scenarios import SCENARIOS
from ..guard import catalogue as _catalogue, rules as _rules
from ..guard.risk import RULE_NARRATIVE as NARRATIVES
from ..models import Telemetry
from ..plant.simulator import ALL_ACTIONS, Plant, dra_factor

ID = "pipeline"
TITLE = "Crude oil pipeline pump station · T-101 / P-101 / MOV-201 · simulated"
SHORT = "Pipeline simulation"
CONSEQUENTIAL = {"pump_start", "pump_stop", "outlet_open", "outlet_close", "inlet_open", "inlet_close",
                 "setpoint", "dra_rate"}
COMMAND_RULES = _rules.COMMAND_RULES
PROCESS_RULES = _rules.PROCESS_RULES
NARRATIVE_VARS = {"shutoff": config.PUMP_SHUTOFF_HEAD_BAR, "limit": config.PRESSURE_MAX_BAR}
RULES = _catalogue.RULES
PHYSICS_THRESHOLD = config.PHYSICS_RESIDUAL_LPM
PHYSICS_LABEL = "mainline flow"
TREND_KEYS = ["tank_level", "pressure", "flow", "setpoint", "dra_rate"]

# Modbus TCP map: the station RTU obeys any well-formed write
MODBUS = {
    "coils": {0: ("pump_start", "pump_stop"), 1: ("outlet_open", "outlet_close"),
              2: ("inlet_open", "inlet_close"), 3: ("maintenance_on", "maintenance_off")},
    "registers": {0: ("setpoint", 1.0), 1: ("dra_rate", 1.0)},
    "inputs": [("tank_level", 10), ("pressure", 100), ("flow", 1), ("setpoint", 1), ("pump", 1),
               ("outlet_valve", 1), ("inlet_valve", 1), ("maintenance", 1), ("dra_rate", 1)],
}


def thresholds() -> dict:
    return _catalogue.station_thresholds()


def context_label(t: Telemetry | None) -> str:
    if t is None:
        return "UNKNOWN"
    return "MAINTENANCE" if (t.maintenance or t.mode == "MAINTENANCE") else t.mode


def setpoint_of(t: Telemetry) -> float:
    return t.setpoint


def expected_flow(t: Telemetry) -> float:
    """Digital twin: the mainline flow the physics says we should be seeing."""
    if not t.pump or not t.outlet_valve:
        return 0.0
    suction = 1.0 if t.tank_level >= config.PUMP_MIN_SUCTION_LEVEL else max(
        0.0, t.tank_level / config.PUMP_MIN_SUCTION_LEVEL) ** 1.5
    return config.PUMP_RATED_FLOW_LPM * (0.82 + 0.18 * (t.tank_level / 100.0)) * suction * dra_factor(t.dra_rate)


def physics_residual(t: Telemetry) -> tuple[float, float]:
    expected = expected_flow(t)
    return expected, abs(expected - t.flow)


def mode_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"mode": snapshot["mode"], "maintenance": snapshot["maintenance"], "setpoint": snapshot["setpoint"],
            "dra_rate": snapshot.get("dra_rate", 0.0)}


def descriptor() -> dict[str, Any]:
    """What the dashboard needs to draw this process."""
    return {
        "id": ID, "title": TITLE, "short": SHORT, "brand": "Pipeline simulation", "consoles": process.consoles(),
        "process_sub": "T-101 → ESD-301 → P-101 → MOV-201 → mainline",
        "kpis": [
            {"key": "pressure", "label": "Discharge pressure", "unit": "bar", "dp": 2, "min": 0, "max": 7,
             "spark": "orange", "foot": "Limit 5.0 · shut-off 6.4 bar"},
            {"key": "flow", "label": "Mainline flow", "unit": "m³/h", "dp": 0, "min": 0, "max": 110,
             "spark": "green", "foot": "Physics: {expected_flow:0} m³/h · DRA {dra_rate:0} %"},
            {"key": "tank_level", "label": "Tank farm level", "unit": "%", "dp": 1, "min": 0, "max": 100, "spark": "green",
             "foot": "SP {setpoint:0} % · band 20–90 · ullage {ullage:0} %"},
            {"key": "setpoint", "label": "Level setpoint", "unit": "%", "dp": 0, "min": 0, "max": 100,
             "spark": "muted", "foot": "Band 20–90 · trim ±5 %"},
        ],
        "equipment": [
            {"key": "pump", "label": "Mainline pump P-101", "icon": "pump"},
            {"key": "outlet", "label": "MOV-201 sectionalising", "icon": "valve"},
            {"key": "inlet", "label": "ESD-301 station inlet", "icon": "valve"},
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
            ["Discharge", "pressure", {"kind": "num", "dp": 2, "unit": " bar"}],
            ["Mainline flow", "flow", {"kind": "num", "dp": 0, "unit": " m³/h"}],
            ["Tank farm", "tank_level", {"kind": "num", "dp": 1, "unit": " %"}],
            ["P-101", "pump", {"kind": "bool", "on": "Running", "off": "Stopped"}],
            ["MOV-201", "outlet_valve", {"kind": "bool", "on": "Open", "off": "Closed"}],
            ["ESD-301", "inlet_valve", {"kind": "bool", "on": "Open", "off": "Closed"}],
            ["DRA", "dra_rate", {"kind": "num", "dp": 0, "unit": " %"}],
            ["Mode", "mode", {"kind": "text", "title": True}],
        ],
        "console": {
            "buttons": [{"action": a, "label": l} for a, l in (
                ("pump_start", "P-101 start"), ("pump_stop", "P-101 stop"), ("outlet_open", "MOV-201 open"),
                ("outlet_close", "MOV-201 close"), ("inlet_open", "ESD-301 open"), ("inlet_close", "ESD-301 close"),
                ("maintenance_on", "Maintenance on"), ("maintenance_off", "Maintenance off"))],
            "inputs": [{"action": "setpoint", "label": "Level SP", "type": "number", "default": 60,
                        "min": 0, "max": 100, "step": 1},
                       {"action": "dra_rate", "label": "DRA %", "type": "number", "default": 0,
                        "min": 0, "max": 100, "step": 5}],
            "sim": [],
            "sources": [{"value": "operator-hmi", "label": "operator-hmi · trusted"},
                        {"value": "maintenance-hmi", "label": "maintenance-hmi"},
                        {"value": "maintenance-laptop", "label": "maintenance-laptop"},
                        {"value": "unknown-host", "label": "unknown-host"}],
        },
    }
