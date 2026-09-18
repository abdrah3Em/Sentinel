"""Wire formats shared by the simulator, the guard and the dashboard."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Telemetry:
    """One sensor frame published by the pump station RTU."""

    ts: int = field(default_factory=now_ms)
    seq: int = 0
    tank_level: float = 0.0
    pressure: float = 0.0
    flow: float = 0.0
    pump: bool = False
    inlet_valve: bool = False
    outlet_valve: bool = False
    setpoint: float = 60.0
    dra_rate: float = 0.0
    mode: str = "AUTO"
    maintenance: bool = False
    pump_runtime_s: float = 0.0
    deadhead_s: float = 0.0
    dry_run_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Telemetry":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class GridTelemetry:
    """One feeder frame published by the distribution simulator (PRD rev 2 section 13)."""

    ts: int = field(default_factory=now_ms)
    seq: int = 0
    frequency_hz: float = 50.0
    v_source_kv: float = 33.0
    tap: int = 0
    avc_target_kv: float = 11.0
    avc_mode: str = "AUTO"
    avc_override_s: float = 0.0
    v_bus_kv: float = 0.0
    v_b1_kv: float = 0.0
    v_b2_kv: float = 0.0
    v_b3_kv: float = 0.0
    v_b4_kv: float = 0.0
    v_b5_kv: float = 0.0
    v_bus2_kv: float = 0.0
    i_feeder_a: float = 0.0
    i_f2_a: float = 0.0
    p_feeder_kw: float = 0.0
    load_b1_kw: float = 0.0
    load_b2_kw: float = 0.0
    load_b3_kw: float = 0.0
    load_b4_kw: float = 0.0
    load_b5_kw: float = 0.0
    pv_kw: float = 0.0
    pv_curtail_pct: float = 0.0
    cb_closed: bool = True
    sw_closed: bool = True
    tie_closed: bool = False
    cb2_closed: bool = True
    protection_tripped: bool = False
    protection2_tripped: bool = False
    fault_present: bool = False
    fault_section: Optional[str] = None
    fault_sections: list[str] = field(default_factory=list)
    fault_indicators: list[bool] = field(default_factory=lambda: [False] * 5)
    supplied: dict[str, bool] = field(default_factory=lambda: {b: True for b in ("b1", "b2", "b3", "b4", "b5")})
    customers_off: int = 0
    cml: float = 0.0
    close_onto_fault_count: int = 0
    switchgear_stress: float = 0.0
    parallel_s: float = 0.0
    circulating_a: float = 0.0
    fault_current_ka: float = 0.0
    trip_age_s: Optional[float] = None
    switching_program: Optional[str] = None
    sp_covers: list[str] = field(default_factory=list)
    permit_to_work: Optional[str] = None
    permits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GridTelemetry":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Command:
    """A control command observed on the wire (PRD section 13)."""

    action: str
    source: str = "operator-hmi"
    value: Optional[float] = None
    id: str = field(default_factory=new_id)
    ts: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        """The wire form: a signed envelope when the source holds a key (sentinel/signing.py)."""
        from . import signing
        return signing.sign(asdict(self))

    def to_unsigned_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Command":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Finding:
    """A single contribution to the risk score, produced by one rule.

    Findings are what make the alert explainable: each one carries its own
    weight and a sentence a control-room engineer can read.
    """

    rule: str
    layer: str
    weight: int
    detail: str
    equipment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Alert:
    """The engineer-facing advisory (PRD section 26)."""

    level: str
    rule: str
    score: int
    summary: str
    equipment: str
    why: str
    recommendation: str
    command_id: Optional[str] = None
    command: Optional[dict[str, Any]] = None
    state: Optional[dict[str, Any]] = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    context: str = "AUTO"
    suppressed_score: int = 0    # what this would have scored without mitigating context
    confidence: str = "HIGH"     # HIGH | REDUCED | LOW — how much the state can be trusted
    uncertainty: str = ""        # plain statement of what could not be verified and what we do about it
    id: str = field(default_factory=new_id)
    ts: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Event:
    """Anything worth putting on the timeline that is not itself an alert."""

    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)
    ts: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
