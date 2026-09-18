"""Process domain: 11 kV distribution feeder F1 (PRD revision 2)."""
from __future__ import annotations

from typing import Any

from .. import config, process
from ..attacks.grid_scenarios import SCENARIOS
from ..guard import grid_catalogue, grid_rules
from ..guard.grid_risk import NARRATIVES
from ..models import GridTelemetry as Telemetry
from ..plant.grid import ALL_ACTIONS, Feeder as Plant, bus_voltage

ID = "grid"
TITLE = "Feeder F1 · 11 kV radial · T1 / CB-101 / SW-102 / TS-201 · simulated"
SHORT = "Grid simulation"
CONSEQUENTIAL = grid_rules.CONSEQUENTIAL
COMMAND_RULES = grid_rules.COMMAND_RULES
PROCESS_RULES = grid_rules.PROCESS_RULES
NARRATIVE_VARS = {"fault_ka": config.GRID_FAULT_CURRENT_KA, "vmin": config.GRID_V_MIN_KV, "vmax": config.GRID_V_MAX_KV}
RULES = grid_catalogue.RULES
PHYSICS_THRESHOLD = config.GRID_PHYSICS_RESIDUAL_KV
PHYSICS_LABEL = "busbar voltage"
TREND_KEYS = ["v_bus_kv", "v_b3_kv", "i_feeder_a", "avc_target_kv", "tap"]

# Modbus TCP map (PRD: the controller obeys any well-formed write)
MODBUS = {
    "coils": {0: ("cb_close", "cb_open"), 1: ("sw_close", "sw_open"), 2: ("tie_close", "tie_open"),
              3: ("protection_reset", None), 4: ("avc_auto", "avc_manual")},
    "registers": {0: ("tap_set", 1.0), 1: ("avc_target", 0.01), 2: ("pv_curtail", 1.0)},
    "inputs": [("v_bus_kv", 100), ("v_b3_kv", 100), ("i_feeder_a", 1), ("tap", 1), ("cb_closed", 1),
               ("sw_closed", 1), ("tie_closed", 1), ("protection_tripped", 1)],
}


def thresholds() -> dict:
    return grid_catalogue.thresholds()


def context_label(t: Telemetry | None) -> str:
    if t is None:
        return "UNKNOWN"
    if t.switching_program:
        return "PROGRAM"
    if t.permit_to_work:
        return "PERMIT"
    return "NORMAL"


def setpoint_of(t: Telemetry) -> float:
    return t.avc_target_kv


def physics_residual(t: Telemetry) -> tuple[float, float]:
    """Digital twin: the busbar voltage tap and source imply, and how far telemetry is from it."""
    expected = bus_voltage(t.tap, t.v_source_kv)
    residual = abs(expected - t.v_bus_kv)
    connected = t.load_b1_kw + t.load_b2_kw + t.load_b3_kw
    if t.cb_closed and t.supplied.get("b1") and connected > 200 and t.i_feeder_a < 5 and not t.fault_current_ka:
        residual = 9.9          # a closed breaker feeding load cannot read zero current
    return expected, residual


def mode_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"avc_mode": snapshot["avc_mode"], "avc_target_kv": snapshot["avc_target_kv"],
            "switching_program": snapshot["switching_program"], "sp_covers": snapshot.get("sp_covers", []),
            "permit_to_work": snapshot["permit_to_work"]}


def descriptor() -> dict[str, Any]:
    vmin, vmax = config.GRID_V_MIN_KV, config.GRID_V_MAX_KV
    return {
        "id": ID, "title": TITLE, "short": SHORT, "brand": "Grid simulation", "consoles": process.consoles(),
        "process_sub": "T1 → CB-101 → S1 → SW-102 → S2 → S3 → TS-201 → F2",
        "kpis": [
            {"key": "v_bus_kv", "label": "Busbar voltage", "unit": "kV", "dp": 2, "min": 10, "max": 12, "spark": "green",
             "foot": f"{{frequency_hz:2}} Hz · band {vmin:.2f}–{vmax:.2f}"},
            {"key": "v_b3_kv", "label": "Hospital bus B3", "unit": "kV", "dp": 2, "min": 10, "max": 12, "spark": "green",
             "foot": "{customers_off} customers off"},
            {"key": "i_feeder_a", "label": "Feeder current", "unit": "A", "dp": 0, "min": 0, "max": 500, "spark": "orange",
             "foot": f"Rating {config.GRID_I_RATING_A:.0f} · warn {config.GRID_I_WARN_A:.0f} A"},
            {"key": "tap", "label": "OLTC tap", "unit": "", "dp": 0, "signed": True, "min": -8, "max": 8, "spark": "muted",
             "foot": "AVC {avc_target_kv:2} kV · {avc_mode}"},
        ],
        "equipment": [
            {"key": "cb", "label": "Breaker CB-101", "icon": "breaker"},
            {"key": "sw", "label": "Sectionaliser SW-102", "icon": "switch"},
            {"key": "tie", "label": "Tie TS-201", "icon": "switch"},
            {"key": "pv", "label": "PV plant PV-1", "icon": "sun"},
            {"key": "sp", "label": "Switching program", "icon": "mode"},
        ],
        "trend": {
            "series": [
                {"key": "v_bus_kv", "label": "Busbar kV", "color": "text", "axis": "left"},
                {"key": "v_b3_kv", "label": "Hospital kV", "color": "green", "axis": "left"},
                {"key": "i_feeder_a", "label": "Feeder A", "color": "orange", "axis": "right"},
                {"key": "avc_target_kv", "label": "AVC target", "color": "muted", "axis": "left", "dash": True},
                {"key": "tap", "label": "Tap", "color": "muted", "axis": "aux", "step": True},
            ],
            "aux": {"min": config.GRID_TAP_MIN, "max": config.GRID_TAP_MAX},
            "left": {"min": 10, "max": 12, "ticks": [10, 10.5, 11, 11.5, 12]},
            "right": {"min": 0, "max": 500, "ticks": [0, 100, 200, 300, 400, 500]},
            "limits": [{"axis": "left", "value": vmax}, {"axis": "left", "value": vmin},
                       {"axis": "right", "value": config.GRID_I_RATING_A, "danger": True}],
        },
        "state_rows": [
            ["Busbar", "v_bus_kv", {"kind": "num", "dp": 2, "unit": " kV"}],
            ["Hospital bus B3", "v_b3_kv", {"kind": "num", "dp": 2, "unit": " kV"}],
            ["Feeder current", "i_feeder_a", {"kind": "num", "dp": 0, "unit": " A"}],
            ["Tap / AVC target", "tap", {"kind": "tap"}],
            ["CB-101", "cb_closed", {"kind": "bool", "on": "Closed", "off": "Open"}],
            ["TS-201", "tie_closed", {"kind": "bool", "on": "Closed", "off": "Open"}],
            ["Protection", "protection_tripped", {"kind": "bool", "on": "Tripped", "off": "Reset"}],
            ["Fault", "fault_section", {"kind": "text", "empty": "none"}],
            ["Program", "switching_program", {"kind": "text", "empty": "none"}],
        ],
        "console": {
            "buttons": [{"action": a, "label": l} for a, l in (
                ("cb_open", "CB-101 open"), ("cb_close", "CB-101 close"), ("protection_reset", "Protection reset"),
                ("sw_open", "SW-102 open"), ("sw_close", "SW-102 close"), ("tie_open", "TS-201 open"),
                ("tie_close", "TS-201 close"), ("tap_raise", "Tap raise"), ("tap_lower", "Tap lower"),
                ("avc_auto", "AVC auto"), ("avc_manual", "AVC manual"),
                ("switching_program_off", "Program off"), ("ptw_cancel", "Permit cancel"))],
            "inputs": [
                {"action": "avc_target", "label": "AVC target kV", "type": "number", "default": 11.0,
                 "min": 9, "max": 13, "step": 0.05},
                {"action": "pv_curtail", "label": "PV curtail %", "type": "number", "default": 0,
                 "min": 0, "max": 100, "step": 5},
                {"action": "switching_program_on", "label": "Program on", "type": "text",
                 "default": "SP-0417:CB-101,SW-102,TS-201,S1"},
                {"action": "ptw_issue", "label": "Permit on", "type": "select", "default": "S1",
                 "options": ["S1", "S2", "S3"]},
            ],
            "sim": [
                {"hook": "fault_inject", "label": "Inject fault", "type": "select", "default": "S2",
                 "options": ["S1", "S2", "S3"]},
                {"hook": "fault_clear", "label": "Clear fault"},
            ],
            "sources": [{"value": "operator-hmi", "label": "operator-hmi · trusted"},
                        {"value": "scada-auto", "label": "scada-auto · trusted"},
                        {"value": "engineering-laptop", "label": "engineering-laptop"},
                        {"value": "field-crew", "label": "field-crew"},
                        {"value": "unknown-host", "label": "unknown-host"}],
        },
    }
