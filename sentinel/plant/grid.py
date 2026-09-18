"""Physical model of distribution feeder F1 (PRD revision 2, sections 11-12).

One 11 kV radial feeder: a 33/11 kV transformer with an on-load tap changer
under automatic voltage control, a feeder breaker, a sectionaliser, a normally
open tie to a second feeder, a PV plant and three load groups (one a hospital).

Like the pumping-station model it is a plain, deterministic, dt-driven object with
no I/O.  It models the consequences that make an otherwise-valid switching
command dangerous:

    close CB-101 onto a standing fault   -> 6.5 kA for 150 ms, re-trip, stress
    open CB-101 with no alternate path   -> three buses go dead, CML climbs
    close the tie with everything closed -> F1 and F2 paralleled, circulating current
    walk the AVC target upward           -> the tap changer obediently drives the
                                            busbar outside statutory limits

The controller accepts any correctly formed command.  That is the point.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import config
from ..models import GridTelemetry, now_ms
from .simulator import PlantEvent

SWITCHING_ACTIONS = {"cb_open", "cb_close", "sw_open", "sw_close", "tie_open", "tie_close"}
TAP_ACTIONS = {"tap_raise", "tap_lower", "tap_set"}
ALL_ACTIONS = SWITCHING_ACTIONS | TAP_ACTIONS | {
    "protection_reset", "avc_target", "avc_auto", "avc_manual", "pv_curtail",
    "switching_program_on", "switching_program_off", "ptw_issue", "ptw_cancel",
}
SECTIONS = ("S1", "S2", "S3")
BUSES = ("b1", "b2", "b3")
SECTION_BUS = {"S1": "b1", "S2": "b2", "S3": "b3"}
BUS_NAME = {"b1": "Residential bus B1", "b2": "Industrial bus B2", "b3": "Hospital bus B3"}
DEVICE = {"cb": "CB-101", "sw": "SW-102", "tie": "TS-201"}
SP_COVERS = ["CB-101", "SW-102", "TS-201", "S1", "S2", "S3"]

TAN_PHI = math.tan(math.acos(config.GRID_LOAD_PF))
LOOP_Z = math.hypot(sum(r for r, _ in config.GRID_SECTIONS.values()),
                    sum(x for _, x in config.GRID_SECTIONS.values()))


def topology(cb: bool, sw: bool, tie: bool) -> dict[str, Any]:
    """Which sections are energised, and from which source, for a switch configuration.

        BB ──S1── B1 ─[SW-102]─S2── B2 ──S3── B3 ─[TS-201]─ F2
    """
    from_t1 = {"S1": cb, "S2": cb and sw, "S3": cb and sw}
    from_f2 = {"S1": tie and sw, "S2": tie, "S3": tie}
    sections = {s: from_t1[s] or from_f2[s] for s in SECTIONS}
    return {"t1": from_t1, "f2": from_f2, "sections": sections,
            "buses": {SECTION_BUS[s]: sections[s] for s in SECTIONS},
            "parallel": cb and sw and tie}


def switches_after(cb: bool, sw: bool, tie: bool, action: str) -> tuple[bool, bool, bool]:
    """The switch configuration a command would produce."""
    return ({"cb_open": False, "cb_close": True}.get(action, cb),
            {"sw_open": False, "sw_close": True}.get(action, sw),
            {"tie_open": False, "tie_close": True}.get(action, tie))


def bus_voltage(tap: int, v_source_kv: float = config.GRID_SOURCE_KV) -> float:
    """Busbar voltage from tap position and source voltage — the digital-twin expectation."""
    return (config.GRID_NOMINAL_KV * (1.0 + tap * config.GRID_TAP_STEP_PCT / 100.0)
            * (v_source_kv / config.GRID_SOURCE_KV))


def _drop_kv(section: str, p_kw: float, q_kvar: float, v_kv: float) -> float:
    r, x = config.GRID_SECTIONS[section]
    return (p_kw * r + q_kvar * x) / max(1.0, v_kv) / 1000.0


@dataclass
class Feeder:
    # switchgear and control
    tap: int = 0
    avc_target_kv: float = config.GRID_NOMINAL_KV
    avc_mode: str = "AUTO"
    cb_closed: bool = True
    sw_closed: bool = True
    tie_closed: bool = False
    protection_tripped: bool = False
    fault_section: Optional[str] = None
    pv_curtail_pct: float = 0.0
    switching_program: Optional[str] = None
    sp_covers: list[str] = field(default_factory=list)
    permit_to_work: Optional[str] = None
    noise: bool = True

    # internals
    seq: int = 0
    clock: float = 0.0
    v_source_kv: float = config.GRID_SOURCE_KV
    irradiance: float = 0.6
    avc_override_until: float = 0.0
    tap_ready_at: float = 0.0
    trip_at: Optional[float] = None
    flash_until: float = 0.0
    last_trip_at: Optional[float] = None
    customers_off: int = 0
    cml: float = 0.0
    close_onto_fault_count: int = 0
    switchgear_stress: float = 0.0
    parallel_s: float = 0.0

    # measured
    frequency_hz: float = 50.0
    v_bus_kv: float = config.GRID_NOMINAL_KV
    v_bus: dict[str, float] = field(default_factory=lambda: {b: 0.0 for b in BUSES})
    loads_kw: dict[str, float] = field(default_factory=lambda: dict(config.GRID_LOADS_KW))
    pv_kw: float = 0.0
    i_feeder_a: float = 0.0
    p_feeder_kw: float = 0.0
    circulating_a: float = 0.0
    fault_current_ka: float = 0.0
    supplied: dict[str, bool] = field(default_factory=lambda: {b: True for b in BUSES})

    _flags: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._solve()

    # ------------------------------------------------------------------ helpers
    @property
    def fault_present(self) -> bool:
        return self.fault_section is not None

    @property
    def fault_indicators(self) -> list[bool]:
        return [s == self.fault_section for s in SECTIONS]

    def topology(self) -> dict[str, Any]:
        return topology(self.cb_closed, self.sw_closed, self.tie_closed)

    # ------------------------------------------------------------------ command
    def apply(self, action: str, value: Optional[Any] = None) -> list[PlantEvent]:
        """Accept a protocol-valid command. The controller does not second-guess it."""
        events: list[PlantEvent] = []
        if action == "cb_close":
            if not self.cb_closed:
                after = topology(True, self.sw_closed, self.tie_closed)
                if self.fault_section and after["t1"][self.fault_section]:
                    # The physical consequence of the flagship attack: fault current flows
                    # until protection clears it again.
                    self.close_onto_fault_count += 1
                    self.switchgear_stress = min(100.0, self.switchgear_stress + 25.0)
                    self.flash_until = self.clock + config.GRID_FAULT_FLASH_S
                    self.trip_at = self.clock + config.GRID_FAULT_FLASH_S
                    events.append(PlantEvent("PHYSICAL", f"CB-101 closed onto the standing fault on "
                                             f"{self.fault_section} — {config.GRID_FAULT_CURRENT_KA:.1f} kA "
                                             f"fault current", "HIGH"))
                else:
                    events.append(PlantEvent("SWITCHGEAR", "CB-101 closed"))
            self.cb_closed = True
        elif action == "cb_open":
            if self.cb_closed:
                events.append(PlantEvent("SWITCHGEAR", "CB-101 opened"))
            self.cb_closed = False
        elif action == "protection_reset":
            if self.protection_tripped:
                events.append(PlantEvent("SWITCHGEAR", "Protection latch reset on CB-101"))
            self.protection_tripped = False
        elif action in ("sw_open", "sw_close"):
            closing = action == "sw_close"
            if closing != self.sw_closed:
                events.append(PlantEvent("SWITCHGEAR", f"SW-102 {'closed' if closing else 'opened'}"))
            self.sw_closed = closing
        elif action in ("tie_open", "tie_close"):
            closing = action == "tie_close"
            if closing != self.tie_closed:
                events.append(PlantEvent("SWITCHGEAR", f"TS-201 {'closed' if closing else 'opened'}"))
            self.tie_closed = closing
            if closing and self.topology()["parallel"]:
                events.append(PlantEvent("PROCESS", "F1 and F2 paralleled through TS-201", "MEDIUM"))
        elif action in ("tap_raise", "tap_lower", "tap_set"):
            old = self.tap
            if action == "tap_set" and value is not None:
                self.tap = int(round(float(value)))
            else:
                self.tap += 1 if action == "tap_raise" else -1
            self.tap = max(config.GRID_TAP_MIN, min(config.GRID_TAP_MAX, self.tap))
            self.avc_override_until = self.clock + config.GRID_MANUAL_OVERRIDE_S
            self.tap_ready_at = self.clock + config.GRID_TAP_STEP_TIME_S
            if self.tap != old:
                events.append(PlantEvent("PROCESS", f"OLTC tapped {old:+d} -> {self.tap:+d} (manual)"))
        elif action == "avc_target" and value is not None:
            old = self.avc_target_kv
            self.avc_target_kv = max(9.0, min(13.0, float(value)))
            events.append(PlantEvent("SETPOINT", f"AVC target {old:.2f} -> {self.avc_target_kv:.2f} kV"))
        elif action in ("avc_auto", "avc_manual"):
            self.avc_mode = "AUTO" if action == "avc_auto" else "MANUAL"
            events.append(PlantEvent("MODE", f"AVC switched to {self.avc_mode}"))
        elif action == "pv_curtail" and value is not None:
            self.pv_curtail_pct = max(0.0, min(100.0, float(value)))
            events.append(PlantEvent("SETPOINT", f"PV-1 curtailed to {self.pv_curtail_pct:.0f} %"))
        elif action == "switching_program_on":
            # "SP-0417" covers the whole feeder; "SP-0417:CB-101,TS-201" names the equipment it covers.
            text = str(value) if value not in (None, "") else "SP-0001"
            program, _, covers = text.partition(":")
            self.switching_program = program.strip() or "SP-0001"
            named = [c.strip().upper() for c in covers.split(",") if c.strip()]
            self.sp_covers = [c for c in named if c in SP_COVERS] or list(SP_COVERS)
            events.append(PlantEvent("MODE", f"Switching program {self.switching_program} declared — "
                                     f"covers {', '.join(self.sp_covers)}"))
        elif action == "switching_program_off":
            if self.switching_program:
                events.append(PlantEvent("MODE", f"Switching program {self.switching_program} closed"))
            self.switching_program = None
            self.sp_covers = []
        elif action == "ptw_issue":
            section = str(value or "S1").upper()
            self.permit_to_work = section if section in SECTIONS else "S1"
            events.append(PlantEvent("MODE", f"Permit-to-work issued on section {self.permit_to_work}"))
        elif action == "ptw_cancel":
            if self.permit_to_work:
                events.append(PlantEvent("MODE", f"Permit-to-work on {self.permit_to_work} cancelled"))
            self.permit_to_work = None
        return events

    def sim_hook(self, payload: dict[str, Any]) -> list[PlantEvent]:
        """Simulator-only hooks used by scenarios (never by the guard)."""
        events: list[PlantEvent] = []
        if "fault_inject" in payload:
            section = str(payload["fault_inject"]).upper()
            if section in SECTIONS:
                self.fault_section = section
                idx = SECTIONS.index(section) + 1
                events.append(PlantEvent("PHYSICAL", f"Fault on section {section} — fault indicator "
                                         f"FI-{idx} set", "HIGH"))
                if self.topology()["sections"][section] and self.trip_at is None:
                    self.trip_at = self.clock + config.GRID_TRIP_DELAY_S
        if payload.get("fault_clear"):
            if self.fault_section:
                events.append(PlantEvent("PHYSICAL", f"Fault on {self.fault_section} located and "
                                         "cleared by the field crew"))
            self.fault_section = None
        return events

    # ------------------------------------------------------------------ physics
    def step(self, dt: float) -> list[PlantEvent]:
        self.clock += dt
        events: list[PlantEvent] = []
        if self.noise:
            self.irradiance = max(0.3, min(0.9, self.irradiance + random.uniform(-0.01, 0.01)))
            self.v_source_kv = config.GRID_SOURCE_KV + random.uniform(-0.05, 0.05)

        events += self._protection()
        events += self._avc()
        self._solve()

        self.customers_off = sum(config.GRID_CUSTOMERS[b] for b in BUSES if not self.supplied[b])
        self.cml += self.customers_off * dt / 60.0
        self.parallel_s = self.parallel_s + dt if self.topology()["parallel"] else 0.0
        return events + self._conditions()

    def _protection(self) -> list[PlantEvent]:
        events: list[PlantEvent] = []
        topo = self.topology()
        if self.fault_section and topo["sections"][self.fault_section] and self.trip_at is None:
            self.trip_at = self.clock + config.GRID_TRIP_DELAY_S
        if self.trip_at is not None and self.clock >= self.trip_at:
            self.trip_at = None
            if self.fault_section and topo["sections"][self.fault_section]:
                if topo["t1"][self.fault_section] and self.cb_closed:
                    self.cb_closed = False
                    self.protection_tripped = True
                    self.last_trip_at = self.clock
                    events.append(PlantEvent("PROTECTION", f"Protection tripped CB-101 for the fault on "
                                             f"{self.fault_section}", "HIGH"))
                if topo["f2"][self.fault_section] and self.tie_closed:
                    self.tie_closed = False
                    events.append(PlantEvent("PROTECTION", "Feeder F2 protection opened TS-201", "HIGH"))
        return events

    def _avc(self) -> list[PlantEvent]:
        """AUTO: hold the busbar at the target, one tap every step time, unless overridden."""
        if self.avc_mode != "AUTO" or self.clock < self.avc_override_until or self.clock < self.tap_ready_at:
            return []
        band = self.avc_target_kv * config.GRID_AVC_DEADBAND_PCT / 100.0
        v = bus_voltage(self.tap, self.v_source_kv)
        old = self.tap
        if v < self.avc_target_kv - band and self.tap < config.GRID_TAP_MAX:
            self.tap += 1
        elif v > self.avc_target_kv + band and self.tap > config.GRID_TAP_MIN:
            self.tap -= 1
        if self.tap == old:
            return []
        self.tap_ready_at = self.clock + config.GRID_TAP_STEP_TIME_S
        return [PlantEvent("PROCESS", f"AVC tapped OLTC {old:+d} -> {self.tap:+d} "
                           f"(target {self.avc_target_kv:.2f} kV)")]

    def _solve(self) -> None:
        topo = self.topology()
        self.supplied = dict(topo["buses"])
        diurnal = 0.85 + 0.15 * math.sin(2 * math.pi * self.clock / config.GRID_DIURNAL_PERIOD_S)
        loads: dict[str, float] = {}
        for b in BUSES:
            base = config.GRID_LOADS_KW[b] * diurnal
            if self.noise:
                base *= 1.0 + random.uniform(-0.01, 0.01)
            loads[b] = base if self.supplied[b] else 0.0
        q = {b: loads[b] * TAN_PHI for b in BUSES}
        self.loads_kw = loads
        self.pv_kw = (config.GRID_PV_RATED_KW * self.irradiance * (1.0 - self.pv_curtail_pct / 100.0)
                      if self.supplied["b2"] else 0.0)

        self.v_bus_kv = bus_voltage(self.tap, self.v_source_kv)
        v = {b: 0.0 for b in BUSES}
        self.i_feeder_a = self.p_feeder_kw = 0.0
        if self.cb_closed:
            p1 = loads["b1"] + ((loads["b2"] + loads["b3"] - self.pv_kw) if self.sw_closed else 0.0)
            q1 = q["b1"] + ((q["b2"] + q["b3"]) if self.sw_closed else 0.0)
            v["b1"] = self.v_bus_kv - _drop_kv("S1", p1, q1, self.v_bus_kv)
            if self.sw_closed:
                p2, q2 = loads["b2"] + loads["b3"] - self.pv_kw, q["b2"] + q["b3"]
                v["b2"] = v["b1"] - _drop_kv("S2", p2, q2, v["b1"])
                v["b3"] = v["b2"] - _drop_kv("S3", loads["b3"], q["b3"], v["b2"])
            self.p_feeder_kw = p1
            self.i_feeder_a = math.hypot(p1, q1) / (math.sqrt(3) * self.v_bus_kv)
        if self.tie_closed and not topo["t1"]["S3"]:
            f2 = config.GRID_F2_KV
            v["b3"] = f2
            p3 = loads["b2"] - self.pv_kw + (loads["b1"] if self.sw_closed else 0.0)
            q3 = q["b2"] + (q["b1"] if self.sw_closed else 0.0)
            v["b2"] = v["b3"] - _drop_kv("S3", p3, q3, v["b3"])
            if self.sw_closed:
                v["b1"] = v["b2"] - _drop_kv("S2", loads["b1"], q["b1"], v["b2"])
        self.v_bus = v
        self.circulating_a = (abs(self.v_bus_kv - config.GRID_F2_KV) * 1000.0 / LOOP_Z
                              if topo["parallel"] else 0.0)
        self.fault_current_ka = config.GRID_FAULT_CURRENT_KA if self.clock < self.flash_until else 0.0
        if self.fault_current_ka:
            self.i_feeder_a = self.fault_current_ka * 1000.0
        self.frequency_hz = 50.0 + (random.uniform(-0.02, 0.02) if self.noise else 0.0)

    def _conditions(self) -> list[PlantEvent]:
        events: list[PlantEvent] = []
        for b in BUSES:
            severity = "HIGH" if b == config.GRID_CRITICAL_BUS else "MEDIUM"
            events += self._edge(f"DEAD_{b}", not self.supplied[b], "PHYSICAL",
                                 f"{BUS_NAME[b]} lost supply", f"{BUS_NAME[b]} supply restored", severity)
        events += self._edge("OVERVOLTAGE", self.v_bus_kv > config.GRID_V_MAX_KV, "PROCESS",
                             f"Busbar voltage above the {config.GRID_V_MAX_KV:.2f} kV statutory limit",
                             "Busbar voltage back inside statutory limits", "MEDIUM")
        events += self._edge("UNDERVOLTAGE", self.supplied["b3"] and 0 < self.v_bus["b3"] < config.GRID_V_MIN_KV,
                             "PROCESS", f"Hospital bus voltage below the {config.GRID_V_MIN_KV:.2f} kV "
                             "statutory limit", "Hospital bus voltage back inside limits", "MEDIUM")
        events += self._edge("OVERLOAD", self.i_feeder_a > config.GRID_I_RATING_A and not self.fault_current_ka,
                             "PHYSICAL", f"Feeder current above the {config.GRID_I_RATING_A:.0f} A rating",
                             "Feeder current back below rating", "HIGH")
        events += self._edge("PARALLEL", self.parallel_s > config.GRID_PARALLEL_STANDING_S, "PHYSICAL",
                             f"Standing parallel between F1 and F2 for over "
                             f"{config.GRID_PARALLEL_STANDING_S:.0f} s ({self.circulating_a:.0f} A circulating)",
                             "Parallel between F1 and F2 broken", "HIGH")
        return events

    def _edge(self, flag: str, active: bool, kind: str, on_msg: str, off_msg: str,
              severity: str) -> list[PlantEvent]:
        if active and flag not in self._flags:
            self._flags.add(flag)
            return [PlantEvent(kind, on_msg, severity)]
        if not active and flag in self._flags:
            self._flags.discard(flag)
            return [PlantEvent(kind, off_msg, "INFO")]
        return []

    # ------------------------------------------------------------------ output
    def telemetry(self) -> GridTelemetry:
        self.seq += 1
        return GridTelemetry(
            ts=now_ms(), seq=self.seq,
            frequency_hz=round(self.frequency_hz, 2), v_source_kv=round(self.v_source_kv, 2),
            tap=self.tap, avc_target_kv=round(self.avc_target_kv, 2), avc_mode=self.avc_mode,
            avc_override_s=round(max(0.0, self.avc_override_until - self.clock), 1),
            v_bus_kv=round(self.v_bus_kv, 3), v_b1_kv=round(self.v_bus["b1"], 3),
            v_b2_kv=round(self.v_bus["b2"], 3), v_b3_kv=round(self.v_bus["b3"], 3),
            i_feeder_a=round(self.i_feeder_a, 1), p_feeder_kw=round(self.p_feeder_kw, 0),
            load_b1_kw=round(self.loads_kw["b1"], 0), load_b2_kw=round(self.loads_kw["b2"], 0),
            load_b3_kw=round(self.loads_kw["b3"], 0), pv_kw=round(self.pv_kw, 0),
            pv_curtail_pct=self.pv_curtail_pct,
            cb_closed=self.cb_closed, sw_closed=self.sw_closed, tie_closed=self.tie_closed,
            protection_tripped=self.protection_tripped, fault_present=self.fault_present,
            fault_section=self.fault_section, fault_indicators=self.fault_indicators,
            supplied=dict(self.supplied), customers_off=self.customers_off, cml=round(self.cml, 1),
            close_onto_fault_count=self.close_onto_fault_count,
            switchgear_stress=round(self.switchgear_stress, 1), parallel_s=round(self.parallel_s, 1),
            circulating_a=round(self.circulating_a, 0), fault_current_ka=self.fault_current_ka,
            trip_age_s=(round(self.clock - self.last_trip_at, 1) if self.last_trip_at is not None else None),
            switching_program=self.switching_program, sp_covers=list(self.sp_covers),
            permit_to_work=self.permit_to_work,
        )

    def snapshot(self) -> dict[str, Any]:
        return self.telemetry().to_dict()
