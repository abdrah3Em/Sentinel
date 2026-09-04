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
    """One sensor frame published by the plant (PRD section 12)."""

    ts: int = field(default_factory=now_ms)
    seq: int = 0
    tank_level: float = 0.0
    pressure: float = 0.0
    flow: float = 0.0
    pump: bool = False
    inlet_valve: bool = False
    outlet_valve: bool = False
    setpoint: float = 60.0
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
class Command:
    """A control command observed on the wire (PRD section 13)."""

    action: str
    source: str = "operator-hmi"
    value: Optional[float] = None
    id: str = field(default_factory=new_id)
    ts: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
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
