"""Physical model of the water treatment tank/pump skid.

The simulator is deliberately a plain, deterministic, dt-driven object with no
I/O so it can be unit tested and driven at any speed.  It models the physical
consequences that make an otherwise-valid command dangerous:

    pump running into a closed outlet  -> flow collapses, pressure climbs
                                          toward pump shut-off head
    pump running on an empty tank      -> cavitation, flow decays
    inlet closed while drawing down    -> tank runs toward the low limit

Note that the plant has *no* cyber protection of its own.  It accepts any
correctly formed command, exactly like the controllers this project is about.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import config
from ..models import Telemetry, now_ms

ACTUATOR_ACTIONS = {
    "pump_start", "pump_stop",
    "outlet_open", "outlet_close",
    "inlet_open", "inlet_close",
}
ALL_ACTIONS = ACTUATOR_ACTIONS | {"setpoint", "maintenance_on", "maintenance_off", "mode_auto", "mode_manual"}


@dataclass
class PlantEvent:
    """A physical happening inside the plant (not a security alert)."""

    type: str
    detail: str
    severity: str = "INFO"


@dataclass
class Plant:
    level: float = 62.0
    pressure: float = 0.4
    flow: float = 0.0
    pump: bool = True
    inlet_valve: bool = True
    outlet_valve: bool = True
    setpoint: float = config.DEFAULT_SETPOINT
    mode: str = "AUTO"
    maintenance: bool = False

    seq: int = 0
    pump_runtime_s: float = 0.0
    deadhead_s: float = 0.0
    dry_run_s: float = 0.0
    inlet_override_until: float = 0.0
    clock: float = 0.0
    noise: bool = True

    _flags: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ command
    def apply(self, action: str, value: Optional[float] = None) -> list[PlantEvent]:
        """Accept a protocol-valid command. The plant does not second-guess it."""
        events: list[PlantEvent] = []
        if action == "pump_start":
            if not self.pump:
                events.append(PlantEvent("PUMP_STATE", "Pump started"))
            self.pump = True
        elif action == "pump_stop":
            if self.pump:
                events.append(PlantEvent("PUMP_STATE", "Pump stopped"))
            self.pump = False
            self.pump_runtime_s = 0.0
        elif action == "outlet_open":
            self.outlet_valve = True
            events.append(PlantEvent("VALVE_STATE", "Outlet valve opened"))
        elif action == "outlet_close":
            self.outlet_valve = False
            events.append(PlantEvent("VALVE_STATE", "Outlet valve closed"))
        elif action == "inlet_open":
            self.inlet_valve = True
            self.inlet_override_until = self.clock + 30.0
            events.append(PlantEvent("VALVE_STATE", "Inlet valve opened (manual override)"))
        elif action == "inlet_close":
            self.inlet_valve = False
            self.inlet_override_until = self.clock + 30.0
            events.append(PlantEvent("VALVE_STATE", "Inlet valve closed (manual override)"))
        elif action == "setpoint" and value is not None:
            old = self.setpoint
            self.setpoint = max(0.0, min(100.0, float(value)))
            events.append(PlantEvent("SETPOINT", f"Level setpoint {old:.0f} -> {self.setpoint:.0f} %"))
        elif action == "maintenance_on":
            self.maintenance = True
            self.mode = "MAINTENANCE"
            events.append(PlantEvent("MODE", "Plant entered MAINTENANCE mode"))
        elif action == "maintenance_off":
            self.maintenance = False
            self.mode = "AUTO"
            events.append(PlantEvent("MODE", "Plant returned to AUTO mode"))
        elif action == "mode_manual":
            self.mode = "MANUAL"
            events.append(PlantEvent("MODE", "Plant switched to MANUAL mode"))
        elif action == "mode_auto":
            self.mode = "MAINTENANCE" if self.maintenance else "AUTO"
            events.append(PlantEvent("MODE", "Plant switched to AUTO mode"))
        return events

    # ------------------------------------------------------------------ physics
    def step(self, dt: float) -> list[PlantEvent]:
        self.clock += dt
        self._level_control()

        inflow = self._inflow()
        outflow = self._outflow()
        self.flow += (outflow - self.flow) * min(1.0, dt / 0.6)   # flow meter lag

        net_lpm = inflow - outflow
        self.level += (net_lpm * dt / 60.0) / config.TANK_CAPACITY_L * 100.0
        self.level = max(0.0, min(100.0, self.level))

        target_p = self._target_pressure()
        tau = 1.6 if self._deadheading() else 0.9
        self.pressure += (target_p - self.pressure) * min(1.0, dt / tau)

        if self.noise:
            self.pressure += random.uniform(-0.012, 0.012)
            self.flow = max(0.0, self.flow + random.uniform(-0.5, 0.5))
            self.level = max(0.0, min(100.0, self.level + random.uniform(-0.02, 0.02)))

        return self._accumulate_conditions(dt)

    def _level_control(self) -> None:
        """AUTO mode: the PLC holds level at setpoint with the inlet valve."""
        if self.mode not in ("AUTO", "MAINTENANCE"):
            return
        if self.clock < self.inlet_override_until:
            return
        if self.level < self.setpoint - config.CONTROL_DEADBAND_PCT:
            self.inlet_valve = True
        elif self.level > self.setpoint + config.CONTROL_DEADBAND_PCT:
            self.inlet_valve = False

    def _inflow(self) -> float:
        if not self.inlet_valve or self.level >= 99.5:
            return 0.0
        return config.INLET_FLOW_LPM * (1.0 - 0.15 * self.level / 100.0)

    def _suction_factor(self) -> float:
        """Pumps need liquid at the suction. Below the minimum level they cavitate."""
        if self.level >= config.PUMP_MIN_SUCTION_LEVEL:
            return 1.0
        return max(0.0, self.level / config.PUMP_MIN_SUCTION_LEVEL) ** 1.5

    def _outflow(self) -> float:
        if not self.pump or not self.outlet_valve:
            return 0.0                                    # check valve, no gravity bypass
        head_factor = 0.82 + 0.18 * (self.level / 100.0)  # more head, slightly more flow
        return config.PUMP_RATED_FLOW_LPM * head_factor * self._suction_factor()

    def _deadheading(self) -> bool:
        return self.pump and not self.outlet_valve

    def _target_pressure(self) -> float:
        static = config.LINE_PRESSURE_BASE_BAR + config.STATIC_HEAD_BAR_AT_FULL * (self.level / 100.0)
        if not self.pump:
            return static
        suction = self._suction_factor()
        if self._deadheading():
            # No discharge path: the pump converts all its energy into head.
            return static + (config.PUMP_SHUTOFF_HEAD_BAR - static) * suction
        return static + 1.9 * (self.flow / config.PUMP_RATED_FLOW_LPM) * suction

    def _accumulate_conditions(self, dt: float) -> list[PlantEvent]:
        events: list[PlantEvent] = []
        if self.pump:
            self.pump_runtime_s += dt
        if self._deadheading():
            self.deadhead_s += dt
        else:
            self.deadhead_s = 0.0
        if self.pump and self.level < config.PUMP_MIN_SUCTION_LEVEL:
            self.dry_run_s += dt
        else:
            self.dry_run_s = 0.0

        events += self._edge("DEADHEAD", self.deadhead_s > 2.0, "PHYSICAL",
                             "Pump is running against a closed discharge path",
                             "Discharge path restored", "HIGH")
        events += self._edge("OVERPRESSURE", self.pressure > config.PRESSURE_MAX_BAR, "PHYSICAL",
                             f"Discharge pressure above the {config.PRESSURE_MAX_BAR:.1f} bar operating limit",
                             "Discharge pressure back inside the operating envelope", "HIGH")
        events += self._edge("DRY_RUN", self.dry_run_s > 2.0, "PHYSICAL",
                             "Pump is running with insufficient suction level (cavitation risk)",
                             "Suction level recovered", "HIGH")
        events += self._edge("LOW_LEVEL", self.level < config.LEVEL_MIN_PCT, "PROCESS",
                             f"Tank level below the {config.LEVEL_MIN_PCT:.0f} % low limit",
                             "Tank level recovered", "MEDIUM")
        events += self._edge("HIGH_LEVEL", self.level > config.LEVEL_MAX_PCT, "PROCESS",
                             f"Tank level above the {config.LEVEL_MAX_PCT:.0f} % high limit",
                             "Tank level back inside limits", "MEDIUM")
        return events

    def _edge(self, flag: str, active: bool, kind: str, on_msg: str, off_msg: str,
              severity: str) -> list[PlantEvent]:
        """Emit an event only on the rising/falling edge of a condition."""
        if active and flag not in self._flags:
            self._flags.add(flag)
            return [PlantEvent(kind, on_msg, severity)]
        if not active and flag in self._flags:
            self._flags.discard(flag)
            return [PlantEvent(kind, off_msg, "INFO")]
        return []

    # ------------------------------------------------------------------ output
    def telemetry(self) -> Telemetry:
        self.seq += 1
        return Telemetry(
            ts=now_ms(),
            seq=self.seq,
            tank_level=round(self.level, 1),
            pressure=round(self.pressure, 2),
            flow=round(self.flow, 1),
            pump=self.pump,
            inlet_valve=self.inlet_valve,
            outlet_valve=self.outlet_valve,
            setpoint=round(self.setpoint, 1),
            mode=self.mode,
            maintenance=self.maintenance,
            pump_runtime_s=round(self.pump_runtime_s, 1),
            deadhead_s=round(self.deadhead_s, 1),
            dry_run_s=round(self.dry_run_s, 1),
        )

    def snapshot(self) -> dict[str, Any]:
        return self.telemetry().to_dict()
