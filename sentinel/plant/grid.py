"""Physical model of distribution feeders F1 and F2 (PRD revision 2, sections 11-12).

    T1 ─ BB-101 ─[CB-101]─S1─ B1 ─[SW-102]─S2─ B2(PV-1) ─S3─ B3(hospital) ─[TS-201]─ B4 ─S4─[CB-201]─ BB-201 ─ T2
                                                                                        └─S5─ B5

F1 is an 11 kV radial feeder off a 33/11 kV transformer with an on-load tap
changer under automatic voltage control; F2 is a second feeder off a fixed-tap
transformer.  The normally-open tie TS-201 joins them at B3/B4.  Voltages and
currents come from a forward-backward sweep power flow over every energised
island each step: loads aggregated up the tree, voltage drops pushed down it,
repeated until it settles.  Fault current is the source contribution through
the path impedance to the faulted section.  Faults can stand on several
sections at once; permits-to-work are per section.

Like the pump-station model it is a plain, deterministic, dt-driven object with
no I/O.  It models the consequences that make an otherwise-valid switching
command dangerous:

    close CB-101 onto a standing fault   -> kA for 150 ms, re-trip, stress
    open CB-101 with no alternate path   -> three buses go dead, CML climbs
    close the tie with everything closed -> F1 and F2 paralleled, circulating current
    walk the AVC target upward           -> the tap changer obediently drives the
                                            busbar outside statutory limits

The controller accepts any correctly formed command.  That is the point.
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import config
from ..models import GridTelemetry, now_ms
from .simulator import PlantEvent

SWITCHING_ACTIONS = {"cb_open", "cb_close", "sw_open", "sw_close", "tie_open", "tie_close", "cb2_open", "cb2_close"}
TAP_ACTIONS = {"tap_raise", "tap_lower", "tap_set"}
ALL_ACTIONS = SWITCHING_ACTIONS | TAP_ACTIONS | {
    "protection_reset", "avc_target", "avc_auto", "avc_manual", "pv_curtail",
    "switching_program_on", "switching_program_off", "ptw_issue", "ptw_cancel",
}
SECTIONS = ("S1", "S2", "S3", "S4", "S5")
F1_SECTIONS = ("S1", "S2", "S3")
BUSES = ("b1", "b2", "b3", "b4", "b5")
F1_BUSES = ("b1", "b2", "b3")
SECTION_BUS = {"S1": "b1", "S2": "b2", "S3": "b3", "S4": "b4", "S5": "b5"}
BUS_NAME = {"b1": "Residential bus B1", "b2": "Industrial bus B2", "b3": "Hospital bus B3",
            "b4": "Commercial bus B4", "b5": "Residential bus B5"}
DEVICE = {"cb": "CB-101", "sw": "SW-102", "tie": "TS-201", "cb2": "CB-201"}
SP_COVERS = ["CB-101", "SW-102", "TS-201", "CB-201", "S1", "S2", "S3", "S4", "S5"]

# Network graph: branch -> (from node, to node).  Nodes: bb1 (BB-101), bb2 (BB-201), b1..b5.
BRANCHES = {"S1": ("bb1", "b1"), "S2": ("b1", "b2"), "S3": ("b2", "b3"),
            "TIE": ("b3", "b4"), "S4": ("bb2", "b4"), "S5": ("b4", "b5")}
BRANCH_SWITCH = {"S1": "cb", "S2": "sw", "TIE": "tie", "S4": "cb2"}       # branches that carry a switch
BRANCH_SECTION = {"S1": "S1", "S2": "S2", "S3": "S3", "S4": "S4", "S5": "S5"}

TAN_PHI = math.tan(math.acos(config.GRID_LOAD_PF))
SQRT3 = math.sqrt(3.0)


def _closed(name: str, sw: dict[str, bool]) -> bool:
    return sw.get(BRANCH_SWITCH.get(name, ""), True)


def islands(sw: dict[str, bool]) -> dict[str, Any]:
    """Which nodes each source reaches through closed branches.

    Returns per-branch feeding source ('t1', 't2', 'both' for the loop) and a
    'parallel' flag when both sources see the same node."""
    reach: dict[str, set[str]] = {}
    for root in ("bb1", "bb2"):
        seen = {root}
        queue = deque([root])
        while queue:
            node = queue.popleft()
            for name, (a, b) in BRANCHES.items():
                if not _closed(name, sw):
                    continue
                other = b if a == node else a if b == node else None
                if other and other not in seen:
                    seen.add(other)
                    queue.append(other)
        reach[root] = seen
    parallel = bool((reach["bb1"] & reach["bb2"]) - {"bb1", "bb2"}) and sw.get("cb", True) and sw.get("cb2", True)
    return {"t1": reach["bb1"], "t2": reach["bb2"], "parallel": parallel}


def topology(cb: bool, sw: bool, tie: bool, cb2: bool = True) -> dict[str, Any]:
    """Which sections are energised, and from which source, for a switch configuration."""
    switches = {"cb": cb, "sw": sw, "tie": tie, "cb2": cb2}
    isl = islands(switches)
    from_t1, from_f2, sections = {}, {}, {}
    for name, (a, b) in BRANCHES.items():
        if name == "TIE":
            continue
        live = _closed(name, switches)
        # a section is energised when its far end is reachable from a source over closed branches
        from_t1[name] = live and (b in isl["t1"] or a in isl["t1"]) and (a in isl["t1"] and b in isl["t1"])
        from_f2[name] = live and (a in isl["t2"] and b in isl["t2"])
        sections[name] = from_t1[name] or from_f2[name]
    # A bus is live when a source reaches it over closed branches — including backwards
    # through the tie, where the section "beyond" the bus is the one carrying the supply.
    buses = {b: (b in isl["t1"] and cb) or (b in isl["t2"] and cb2) for b in BUSES}
    return {"t1": from_t1, "f2": from_f2, "sections": sections, "buses": buses, "parallel": isl["parallel"]}


def switches_after(cb: bool, sw: bool, tie: bool, action: str, cb2: bool = True) -> tuple[bool, bool, bool, bool]:
    """The switch configuration a command would produce."""
    return ({"cb_open": False, "cb_close": True}.get(action, cb),
            {"sw_open": False, "sw_close": True}.get(action, sw),
            {"tie_open": False, "tie_close": True}.get(action, tie),
            {"cb2_open": False, "cb2_close": True}.get(action, cb2))


def bus_voltage(tap: int, v_source_kv: float = config.GRID_SOURCE_KV) -> float:
    """Busbar voltage from tap position and source voltage — the digital-twin expectation."""
    return (config.GRID_NOMINAL_KV * (1.0 + tap * config.GRID_TAP_STEP_PCT / 100.0)
            * (v_source_kv / config.GRID_SOURCE_KV))


def drop_kv(branch: str, p_kw: float, q_kvar: float, v_kv: float) -> float:
    """Voltage drop across a branch carrying P, Q at sending-end voltage V (kV)."""
    r, x = config.GRID_SECTIONS[branch]
    return (p_kw * r + q_kvar * x) / max(1.0, v_kv) / 1000.0


def sweep(root: str, v_root_kv: float, switches: dict[str, bool], injections: dict[str, tuple[float, float]],
          stop_at: set[str] = frozenset(), sweeps: int = config.GRID_POWER_FLOW_SWEEPS) -> dict[str, Any]:
    """Forward-backward sweep over the radial tree reachable from `root`.

    injections: node -> (P kW, Q kvar) net load (positive = consumption).
    Returns node voltages and per-branch (P, Q, I) flows."""
    parent: dict[str, tuple[str, str]] = {}          # node -> (parent node, branch)
    order = [root]
    queue = deque([root])
    seen = {root} | set(stop_at)
    while queue:
        node = queue.popleft()
        for name, (a, b) in BRANCHES.items():
            if not _closed(name, switches):
                continue
            other = b if a == node else a if b == node else None
            if other and other not in seen:
                seen.add(other)
                parent[other] = (node, name)
                order.append(other)
                queue.append(other)
    v = {n: v_root_kv for n in order}
    flows: dict[str, tuple[float, float, float]] = {}
    for _ in range(sweeps):
        # backward: aggregate P, Q from the leaves toward the root
        agg = {n: list(injections.get(n, (0.0, 0.0))) for n in order}
        for n in reversed(order[1:]):
            p, branch = parent[n]
            agg[p][0] += agg[n][0]
            agg[p][1] += agg[n][1]
            flows[branch] = (agg[n][0], agg[n][1], math.hypot(agg[n][0], agg[n][1]) / (SQRT3 * max(1.0, v[p])))
        # forward: push voltages down from the root
        for n in order[1:]:
            p, branch = parent[n]
            v[n] = max(0.0, v[p] - drop_kv(branch, flows[branch][0], flows[branch][1], v[p]))
    return {"nodes": order, "v": v, "flows": flows, "parent": parent}


def fault_current_ka(source_kv: float, path_branches: list[str]) -> float:
    """Source contribution into a fault: E/√3 over source impedance plus half the faulted section."""
    r, x = config.GRID_SOURCE_Z_OHM, 0.0
    for i, branch in enumerate(path_branches):
        br, bx = config.GRID_SECTIONS[branch]
        scale = 0.5 if i == len(path_branches) - 1 else 1.0
        r, x = r + br * scale, x + bx * scale
    return round(source_kv * 1000.0 / SQRT3 / math.hypot(r, x) / 1000.0, 2)


@dataclass
class Feeder:
    # switchgear and control
    tap: int = 0
    avc_target_kv: float = config.GRID_NOMINAL_KV
    avc_mode: str = "AUTO"
    cb_closed: bool = True
    sw_closed: bool = True
    tie_closed: bool = False
    cb2_closed: bool = True
    protection_tripped: bool = False
    protection2_tripped: bool = False
    fault_sections: list[str] = field(default_factory=list)
    pv_curtail_pct: float = 0.0
    switching_program: Optional[str] = None
    sp_covers: list[str] = field(default_factory=list)
    permits: list[str] = field(default_factory=list)
    noise: bool = True

    # internals
    seq: int = 0
    clock: float = 0.0
    v_source_kv: float = config.GRID_SOURCE_KV
    irradiance: float = 0.6
    avc_override_until: float = 0.0
    tap_ready_at: float = 0.0
    trip_at: Optional[float] = None
    trip2_at: Optional[float] = None
    flash_until: float = 0.0
    flash_ka: float = 0.0
    last_trip_at: Optional[float] = None
    customers_off: int = 0
    cml: float = 0.0
    close_onto_fault_count: int = 0
    switchgear_stress: float = 0.0
    parallel_s: float = 0.0

    # measured
    frequency_hz: float = 50.0
    v_bus_kv: float = config.GRID_NOMINAL_KV
    v_bus2_kv: float = config.GRID_F2_KV
    v_bus: dict[str, float] = field(default_factory=lambda: {b: 0.0 for b in BUSES})
    loads_kw: dict[str, float] = field(default_factory=lambda: dict(config.GRID_LOADS_KW))
    pv_kw: float = 0.0
    i_feeder_a: float = 0.0
    i_f2_a: float = 0.0
    p_feeder_kw: float = 0.0
    circulating_a: float = 0.0
    fault_current_ka: float = 0.0
    supplied: dict[str, bool] = field(default_factory=lambda: {b: True for b in BUSES})

    _flags: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._solve()

    # ------------------------------------------------------------------ helpers
    @property
    def fault_section(self) -> Optional[str]:
        return self.fault_sections[0] if self.fault_sections else None

    @fault_section.setter
    def fault_section(self, value: Optional[str]) -> None:
        self.fault_sections = [value] if value else []

    @property
    def permit_to_work(self) -> Optional[str]:
        return self.permits[0] if self.permits else None

    @permit_to_work.setter
    def permit_to_work(self, value: Optional[str]) -> None:
        self.permits = [value] if value else []

    @property
    def fault_present(self) -> bool:
        return bool(self.fault_sections)

    @property
    def fault_indicators(self) -> list[bool]:
        return [s in self.fault_sections for s in SECTIONS]

    @property
    def switches(self) -> dict[str, bool]:
        return {"cb": self.cb_closed, "sw": self.sw_closed, "tie": self.tie_closed, "cb2": self.cb2_closed}

    def topology(self) -> dict[str, Any]:
        return topology(self.cb_closed, self.sw_closed, self.tie_closed, self.cb2_closed)

    def _path_from_source(self, section: str, source: str) -> list[str]:
        """Branches from a source busbar to a section, over closed branches (empty if unreachable)."""
        root = "bb1" if source == "t1" else "bb2"
        result = sweep(root, 11.0, self.switches, {}, sweeps=0)
        far = BRANCHES[section][1] if source == "t1" or section != "S4" else BRANCHES[section][0]
        target = BRANCHES[section][0] if far not in result["parent"] else far
        path: list[str] = []
        node = far if far in result["parent"] else target
        while node in result["parent"]:
            parent, branch = result["parent"][node]
            path.append(branch)
            node = parent
        path.reverse()
        return path if section in path else []

    # ------------------------------------------------------------------ command
    def apply(self, action: str, value: Optional[Any] = None) -> list[PlantEvent]:
        """Accept a protocol-valid command. The controller does not second-guess it."""
        events: list[PlantEvent] = []
        if action in ("cb_close", "cb2_close"):
            attr, device, source = ("cb_closed", "CB-101", "t1") if action == "cb_close" else ("cb2_closed", "CB-201", "t2")
            if not getattr(self, attr):
                setattr(self, attr, True)
                fed = [s for s in self.fault_sections if self.topology()[source].get(s)]
                if fed:
                    # The physical consequence of the flagship attack: fault current flows
                    # until protection clears it again.
                    worst = max(fault_current_ka(self.v_bus_kv if source == "t1" else self.v_bus2_kv,
                                                 self._path_from_source(s, source) or [s]) for s in fed)
                    self.close_onto_fault_count += 1
                    self.switchgear_stress = min(100.0, self.switchgear_stress + 25.0)
                    self.flash_until = self.clock + config.GRID_FAULT_FLASH_S
                    self.flash_ka = worst
                    if source == "t1":
                        self.trip_at = self.clock + config.GRID_FAULT_FLASH_S
                    else:
                        self.trip2_at = self.clock + config.GRID_FAULT_FLASH_S
                    events.append(PlantEvent("PHYSICAL", f"{device} closed onto the standing fault on "
                                             f"{', '.join(fed)} — {worst:.1f} kA fault current", "HIGH"))
                else:
                    events.append(PlantEvent("SWITCHGEAR", f"{device} closed"))
                    if self.topology()["parallel"]:
                        events.append(PlantEvent("PROCESS", "F1 and F2 paralleled through TS-201", "MEDIUM"))
        elif action in ("cb_open", "cb2_open"):
            attr, device = ("cb_closed", "CB-101") if action == "cb_open" else ("cb2_closed", "CB-201")
            if getattr(self, attr):
                events.append(PlantEvent("SWITCHGEAR", f"{device} opened"))
            setattr(self, attr, False)
        elif action == "protection_reset":
            if self.protection_tripped or self.protection2_tripped:
                events.append(PlantEvent("SWITCHGEAR", "Protection latch reset"))
            self.protection_tripped = self.protection2_tripped = False
        elif action in ("sw_open", "sw_close"):
            closing = action == "sw_close"
            if closing != self.sw_closed:
                events.append(PlantEvent("SWITCHGEAR", f"SW-102 {'closed' if closing else 'opened'}"))
            self.sw_closed = closing
            if closing and self.topology()["parallel"]:
                events.append(PlantEvent("PROCESS", "F1 and F2 paralleled through TS-201", "MEDIUM"))
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
            # One permit per section; several sections may be under permit at once.
            sections = [s.strip().upper() for s in str(value or "S1").split(",")]
            for section in sections:
                if section in SECTIONS and section not in self.permits:
                    self.permits.append(section)
                    events.append(PlantEvent("MODE", f"Permit-to-work issued on section {section}"))
        elif action == "ptw_cancel":
            targets = ([s.strip().upper() for s in str(value).split(",")] if value not in (None, "", True)
                       else list(self.permits))
            for section in targets:
                if section in self.permits:
                    self.permits.remove(section)
                    events.append(PlantEvent("MODE", f"Permit-to-work on {section} cancelled"))
        return events

    def sim_hook(self, payload: dict[str, Any]) -> list[PlantEvent]:
        """Simulator-only hooks used by scenarios (never by the guard)."""
        events: list[PlantEvent] = []
        if "fault_inject" in payload:
            for section in str(payload["fault_inject"]).upper().split(","):
                section = section.strip()
                if section in SECTIONS and section not in self.fault_sections:
                    self.fault_sections.append(section)
                    idx = SECTIONS.index(section) + 1
                    events.append(PlantEvent("PHYSICAL", f"Fault on section {section} — fault indicator "
                                             f"FI-{idx} set", "HIGH"))
            self._arm_protection()
        if payload.get("fault_clear"):
            value = payload["fault_clear"]
            targets = ([s.strip().upper() for s in str(value).split(",")] if value is not True
                       else list(self.fault_sections))
            for section in targets:
                if section in self.fault_sections:
                    self.fault_sections.remove(section)
                    events.append(PlantEvent("PHYSICAL", f"Fault on {section} located and cleared by the field crew"))
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

    def _arm_protection(self) -> None:
        topo = self.topology()
        if any(topo["t1"].get(s) for s in self.fault_sections) and self.trip_at is None:
            self.trip_at = self.clock + config.GRID_TRIP_DELAY_S
        if any(topo["f2"].get(s) for s in self.fault_sections) and self.trip2_at is None:
            self.trip2_at = self.clock + config.GRID_TRIP_DELAY_S

    def _protection(self) -> list[PlantEvent]:
        events: list[PlantEvent] = []
        self._arm_protection()
        topo = self.topology()
        if self.trip_at is not None and self.clock >= self.trip_at:
            self.trip_at = None
            fed = [s for s in self.fault_sections if topo["t1"].get(s)]
            if fed and self.cb_closed:
                self.cb_closed = False
                self.protection_tripped = True
                self.last_trip_at = self.clock
                events.append(PlantEvent("PROTECTION", f"Protection tripped CB-101 for the fault on {', '.join(fed)}", "HIGH"))
        if self.trip2_at is not None and self.clock >= self.trip2_at:
            self.trip2_at = None
            fed = [s for s in self.fault_sections if topo["f2"].get(s)]
            if fed:
                if self.tie_closed and any(s in F1_SECTIONS for s in fed):
                    self.tie_closed = False
                    events.append(PlantEvent("PROTECTION", "Feeder F2 protection opened TS-201", "HIGH"))
                elif self.cb2_closed:
                    self.cb2_closed = False
                    self.protection2_tripped = True
                    events.append(PlantEvent("PROTECTION", f"Protection tripped CB-201 for the fault on {', '.join(fed)}", "HIGH"))
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
        """Power flow over every energised island: forward-backward sweep from each live source."""
        topo = self.topology()
        self.supplied = dict(topo["buses"])
        diurnal = 0.85 + 0.15 * math.sin(2 * math.pi * self.clock / config.GRID_DIURNAL_PERIOD_S)
        loads: dict[str, float] = {}
        for b in BUSES:
            base = config.GRID_LOADS_KW[b] * diurnal
            if self.noise:
                base *= 1.0 + random.uniform(-0.01, 0.01)
            loads[b] = base if self.supplied[b] else 0.0
        self.loads_kw = loads
        self.pv_kw = (config.GRID_PV_RATED_KW * self.irradiance * (1.0 - self.pv_curtail_pct / 100.0)
                      if self.supplied["b2"] else 0.0)
        injections = {b: (loads[b] - (self.pv_kw if b == "b2" else 0.0), loads[b] * TAN_PHI) for b in BUSES}

        self.v_bus_kv = bus_voltage(self.tap, self.v_source_kv)
        self.v_bus2_kv = config.GRID_F2_KV
        v = {b: 0.0 for b in BUSES}
        self.i_feeder_a = self.i_f2_a = self.p_feeder_kw = 0.0
        switches = self.switches
        # In a parallel, each source solves its own side up to the tie; the loop current is
        # estimated from the voltage difference across the tie (below).
        stop_t1 = {"b4"} if topo["parallel"] else set()
        stop_t2 = {"b3"} if topo["parallel"] else set()
        if self.cb_closed:
            res = sweep("bb1", self.v_bus_kv, switches, injections, stop_at=stop_t1)
            for n in res["nodes"]:
                if n in v:
                    v[n] = res["v"][n]
            if "S1" in res["flows"]:
                p, q, i = res["flows"]["S1"]
                self.p_feeder_kw, self.i_feeder_a = p, i
        if self.cb2_closed:
            res = sweep("bb2", self.v_bus2_kv, switches, injections, stop_at=stop_t2)
            for n in res["nodes"]:
                if n in v and (v[n] == 0.0 or n in ("b4", "b5")):
                    v[n] = res["v"][n]
            if "S4" in res["flows"]:
                self.i_f2_a = res["flows"]["S4"][2]
        self.v_bus = v
        if topo["parallel"]:
            r, x = config.GRID_SECTIONS["TIE"]
            loop_r = r + sum(config.GRID_SECTIONS[s][0] for s in ("S1", "S2", "S3", "S4"))
            loop_x = x + sum(config.GRID_SECTIONS[s][1] for s in ("S1", "S2", "S3", "S4"))
            self.circulating_a = abs(v["b3"] - v["b4"]) * 1000.0 / SQRT3 / math.hypot(loop_r, loop_x) + \
                abs(self.v_bus_kv - self.v_bus2_kv) * 1000.0 / SQRT3 / math.hypot(loop_r, loop_x)
        else:
            self.circulating_a = 0.0
        self.fault_current_ka = self.flash_ka if self.clock < self.flash_until else 0.0
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
            v_b4_kv=round(self.v_bus["b4"], 3), v_b5_kv=round(self.v_bus["b5"], 3),
            v_bus2_kv=round(self.v_bus2_kv, 3),
            i_feeder_a=round(self.i_feeder_a, 1), i_f2_a=round(self.i_f2_a, 1), p_feeder_kw=round(self.p_feeder_kw, 0),
            load_b1_kw=round(self.loads_kw["b1"], 0), load_b2_kw=round(self.loads_kw["b2"], 0),
            load_b3_kw=round(self.loads_kw["b3"], 0), load_b4_kw=round(self.loads_kw["b4"], 0),
            load_b5_kw=round(self.loads_kw["b5"], 0), pv_kw=round(self.pv_kw, 0),
            pv_curtail_pct=self.pv_curtail_pct,
            cb_closed=self.cb_closed, sw_closed=self.sw_closed, tie_closed=self.tie_closed, cb2_closed=self.cb2_closed,
            protection_tripped=self.protection_tripped, protection2_tripped=self.protection2_tripped,
            fault_present=self.fault_present, fault_section=self.fault_section,
            fault_sections=list(self.fault_sections), fault_indicators=self.fault_indicators,
            supplied=dict(self.supplied), customers_off=self.customers_off, cml=round(self.cml, 1),
            close_onto_fault_count=self.close_onto_fault_count,
            switchgear_stress=round(self.switchgear_stress, 1), parallel_s=round(self.parallel_s, 1),
            circulating_a=round(self.circulating_a, 0), fault_current_ka=self.fault_current_ka,
            trip_age_s=(round(self.clock - self.last_trip_at, 1) if self.last_trip_at is not None else None),
            switching_program=self.switching_program, sp_covers=list(self.sp_covers),
            permit_to_work=self.permit_to_work, permits=list(self.permits),
        )

    def snapshot(self) -> dict[str, Any]:
        return self.telemetry().to_dict()
