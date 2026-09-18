"""Scenario building blocks shared by every simulated process."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Step:
    """One scripted action.

    kind: 'command' publishes a control command, 'narrate' only tells the story,
    'sim' pokes a simulator-only hook on plant/sim (fault inject, fault clear),
    'replay' freezes the telemetry path, 'replay_command' re-publishes a
    remembered message verbatim, 'wait' pauses.
    """

    kind: str = "command"
    action: str = ""
    value: Optional[Any] = None
    source: str = "operator-hmi"
    delay: float = 1.0            # seconds to wait *before* this step
    note: str = ""
    seconds: float = 0.0          # for 'replay' / 'wait'
    remember: str = ""            # 'command': keep the published message under this key
    key: str = ""                 # 'replay_command': re-publish the remembered message verbatim
    age_s: float = 0.0            # 'replay_command': how old the replayed capture is


def ordered(scenarios: dict) -> list:
    """Scenarios in the order of the number that leads their title."""
    def key(s):
        head = s.title.split(" ")[0]
        return (0, int(head)) if head.isdigit() else (1, s.title)
    return sorted(scenarios.values(), key=key)


@dataclass
class Scenario:
    id: str
    title: str
    kind: str                     # 'attack' | 'legitimate'
    narrative: str
    expect: str                   # what the guard should conclude
    steps: list[Step] = field(default_factory=list)
    duration_hint: float = 0.0
