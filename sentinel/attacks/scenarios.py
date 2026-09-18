"""Controlled attack and operations scenarios (PRD sections 30-36).

Nothing here touches real infrastructure.  Each scenario is a deterministic
script of protocol-valid MQTT commands aimed at the local simulator, so a judge
can watch the same story replay identically every time.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from .. import config, process
from ..bus import Bus
from ..models import Command, Event
from .base import Scenario, Step, ordered

log = logging.getLogger("sentinel.attacks")

ATTACKER = "maintenance-laptop"
OPERATOR = "operator-hmi"
MAINTAINER = "maintenance-hmi"


def _prep(action: str, value: Optional[float] = None, note: str = "", delay: float = 2.0) -> Step:
    """Operator line-up commands used to reach the scenario's starting state."""
    return Step("command", action, value, OPERATOR, delay, note or f"Line-up: {action}")


SCENARIOS: dict[str, Scenario] = {}


def register(scenario: Scenario) -> Scenario:
    SCENARIOS[scenario.id] = scenario
    return scenario


register(Scenario(
    id="unsafe_valve",
    title="1 — Valve slam on a running mainline pump (flagship)",
    kind="attack",
    narrative="A valid MOV-201 close arrives while P-101 is delivering — surge and dead-head.",
    expect="CRITICAL · SEQ-001",
    duration_hint=12,
    steps=[
        Step("narrate", note="P-101 on, MOV-201 open, mainline at duty.", delay=0.5),
        _prep("outlet_open", note="Line-up: confirm MOV-201 open"),
        _prep("pump_start", note="Line-up: P-101 at duty"),
        Step("narrate", note="A valid MOV-201 close arrives from a maintenance laptop.", delay=3.0),
        Step("command", "outlet_close", source=ATTACKER, delay=0.5,
             note="Attack: MOV-201 close with P-101 running"),
        Step("narrate", note="Flow collapses; surge drives pressure to shut-off head.", delay=6.0),
    ],
))

register(Scenario(
    id="setpoint_manipulation",
    title="2 — Setpoint manipulation",
    kind="attack",
    narrative="One command jumps the tank farm setpoint from 60 % to 95 %, above the band.",
    expect="HIGH · ROC-001 + ENV-001",
    duration_hint=10,
    steps=[
        Step("narrate", note="Current level setpoint is the normal duty value.", delay=0.5),
        Step("command", "setpoint", value=65, source=OPERATOR, delay=2.0,
             note="Normal operator trim of +5 % — the guard should stay quiet"),
        Step("narrate", note="Now a single large jump arrives.", delay=3.0),
        Step("command", "setpoint", value=95, source=ATTACKER, delay=0.5,
             note="Attack: setpoint driven to 95 %, above the 90 % high limit"),
        Step("narrate", note="The tank farm is being driven toward its high-level limit — ullage gone.", delay=4.0),
    ],
))

register(Scenario(
    id="deadhead_start",
    title="4 — Pump start against a closed MOV-201",
    kind="attack",
    narrative="P-101 started with MOV-201 closed — no path for the crude.",
    expect="HIGH · STATE-002",
    duration_hint=14,
    steps=[
        Step("narrate", note="Operator isolates the segment in the correct order.", delay=0.5),
        _prep("pump_stop", note="Line-up: P-101 stopped first"),
        _prep("outlet_close", note="Line-up: MOV-201 closed after P-101 stopped"),
        Step("narrate", note="Segment isolated. Now an unexpected start arrives.",
             delay=3.0),
        Step("command", "pump_start", source=ATTACKER, delay=0.5,
             note="Attack: start P-101 against closed MOV-201"),
        Step("narrate", note="Pressure rises to shut-off head with zero flow.", delay=5.0),
    ],
))

register(Scenario(
    id="rapid_sequence",
    title="5 — Rapid actuator sequence",
    kind="attack",
    narrative="Five ordinary actuator commands in four seconds.",
    expect="MEDIUM · SEQ-002 + SEQ-003",
    duration_hint=10,
    steps=[
        Step("narrate", note="A scripted burst of actuator commands begins.", delay=0.5),
        Step("command", "pump_start", source=ATTACKER, delay=0.8, note="Burst 1/5"),
        Step("command", "outlet_close", source=ATTACKER, delay=0.8, note="Burst 2/5"),
        Step("command", "outlet_open", source=ATTACKER, delay=0.8, note="Burst 3/5"),
        Step("command", "pump_stop", source=ATTACKER, delay=0.8, note="Burst 4/5"),
        Step("command", "pump_start", source=ATTACKER, delay=0.8, note="Burst 5/5"),
        Step("narrate", note="Five actuator commands inside four seconds, outside maintenance.",
             delay=3.0),
    ],
))

register(Scenario(
    id="setpoint_drift",
    title="3 — Slow setpoint drift",
    kind="attack",
    narrative="Tank farm setpoint nudged +4 % at a time until it leaves the safe band.",
    expect="MEDIUM → HIGH · ROC-002",
    duration_hint=34,
    steps=[
        Step("narrate", note="Each trim below is inside the normal ±5 % operator band.", delay=0.5),
        Step("command", "setpoint", value=64, source=ATTACKER, delay=1.0, note="Drift step 1: 60 -> 64 %"),
        Step("command", "setpoint", value=68, source=ATTACKER, delay=4.0, note="Drift step 2: 68 %"),
        Step("command", "setpoint", value=72, source=ATTACKER, delay=4.0, note="Drift step 3: 72 %"),
        Step("command", "setpoint", value=76, source=ATTACKER, delay=4.0, note="Drift step 4: 76 %"),
        Step("narrate", note="Net movement now exceeds anything an operator would trim in one shift.", delay=1.0),
        Step("command", "setpoint", value=80, source=ATTACKER, delay=3.0, note="Drift step 5: 80 %"),
        Step("command", "setpoint", value=84, source=ATTACKER, delay=4.0, note="Drift step 6: 84 %"),
        Step("command", "setpoint", value=88, source=ATTACKER, delay=4.0, note="Drift step 7: 88 %"),
        Step("command", "setpoint", value=92, source=ATTACKER, delay=4.0, note="Drift step 8: 92 % — outside the band"),
        Step("narrate", note="The tank farm is now being driven above its high-level limit.", delay=3.0),
    ],
))

register(Scenario(
    id="telemetry_replay",
    title="6 — Telemetry & command replay",
    kind="attack",
    narrative="A frozen RTU frame hides a draining tank farm; a captured command is replayed verbatim.",
    expect="HIGH · TEL-002 + CMD-001 · confidence LOW",
    duration_hint=40,
    steps=[
        Step("command", "outlet_open", source=OPERATOR, delay=0.5, remember="captured",
             note="Line-up: a routine operator command the attacker records for later"),
        Step("narrate", note="Telemetry frame captured for replay.", delay=2.0),
        Step("replay", seconds=30.0, delay=1.0, source=ATTACKER,
             note="Attack: recorded frame replayed at 2 Hz, controller silenced"),
        Step("command", "inlet_close", source=ATTACKER, delay=3.0,
             note="Attack: ESD-301 closed behind the frozen picture"),
        Step("narrate", note="Tank farm draining; the display has not moved.", delay=5.0),
        Step("replay_command", key="captured", age_s=300.0, source=ATTACKER, delay=4.0,
             note="Attack: recorded command replayed verbatim — same id, 5 min old"),
        Step("narrate", note="Every decision now rests on a state that is not real.", delay=6.0),
        Step("narrate", note="Telemetry returns — level jumps to reality.", delay=10.0),
        Step("command", "inlet_open", source=OPERATOR, delay=4.0, note="Operator reopens ESD-301"),
    ],
))

register(Scenario(
    id="shutdown",
    title="8 — Planned shutdown",
    kind="legitimate",
    narrative="P-101 off, then MOV-201, then ESD-301 — the correct order.",
    expect="Quiet",
    duration_hint=14,
    steps=[
        Step("narrate", note="Planned shutdown begins.", delay=0.5),
        Step("command", "pump_stop", source=OPERATOR, delay=1.0, note="P-101 stopped first"),
        Step("narrate", note="Flow decays to zero before any valve is touched.", delay=2.0),
        Step("command", "outlet_close", source=OPERATOR, delay=4.0, note="MOV-201 closed after zero flow"),
        Step("command", "inlet_close", source=OPERATOR, delay=4.0, note="ESD-301 closed"),
        Step("narrate", note="Station isolated. Quiet.", delay=2.0),
    ],
))

register(Scenario(
    id="startup",
    title="9 — Planned start-up",
    kind="legitimate",
    narrative="ESD-301, then MOV-201, then P-101.",
    expect="Quiet",
    duration_hint=14,
    steps=[
        Step("narrate", note="Planned start-up begins.", delay=0.5),
        Step("command", "inlet_open", source=OPERATOR, delay=1.0, note="ESD-301 opened"),
        Step("narrate", note="Level confirmed above minimum suction.", delay=3.0),
        Step("command", "outlet_open", source=OPERATOR, delay=2.0, note="MOV-201 lined up before the pump"),
        Step("command", "pump_start", source=OPERATOR, delay=4.0, note="P-101 started into an open mainline"),
        Step("narrate", note="Station at duty. Quiet.", delay=3.0),
    ],
))

register(Scenario(
    id="maintenance",
    title="7 — Legitimate maintenance isolation",
    kind="legitimate",
    narrative="The same isolation commands, under a declared maintenance mode.",
    expect="LOW at most · CTX-001",
    duration_hint=18,
    steps=[
        Step("narrate", note="Maintenance engineer takes the station into MAINTENANCE mode.", delay=0.5),
        Step("command", "maintenance_on", source=MAINTAINER, delay=1.0,
             note="Station declared under maintenance"),
        Step("narrate", note="Now the same P-101 stop and MOV-201 close as an attack sequence.", delay=2.5),
        Step("command", "pump_stop", source=MAINTAINER, delay=2.0, note="P-101 stopped first"),
        Step("command", "outlet_close", source=MAINTAINER, delay=3.0, note="MOV-201 closed after the pump"),
        Step("narrate", note="Same commands, maintenance context: expected.", delay=3.0),
        Step("command", "outlet_open", source=MAINTAINER, delay=3.0, note="Work complete: reopen MOV-201"),
        Step("command", "pump_start", source=MAINTAINER, delay=2.0, note="Restart P-101"),
        Step("command", "maintenance_off", source=MAINTAINER, delay=2.0, note="Return to AUTO"),
    ],
))

register(Scenario(
    id="normal_ops",
    title="10 — Normal operations (false-positive check)",
    kind="legitimate",
    narrative="Small trims and a correctly sequenced valve change.",
    expect="Quiet",
    duration_hint=16,
    steps=[
        Step("narrate", note="Routine shift activity begins.", delay=0.5),
        Step("command", "setpoint", value=62, source=OPERATOR, delay=2.0, note="Trim setpoint +2 %"),
        Step("command", "inlet_open", source=OPERATOR, delay=3.0, note="Confirm ESD-301 open"),
        Step("command", "setpoint", value=58, source=OPERATOR, delay=3.0, note="Trim setpoint -4 %"),
        Step("command", "outlet_open", source=OPERATOR, delay=3.0, note="Confirm MOV-201 open"),
        Step("narrate", note="Nothing unsafe. Quiet.", delay=2.0),
    ],
))


class ScenarioRunner:
    """Executes a scenario against the live MQTT plant, in a background thread."""

    def __init__(self, bus: Bus, on_event: Optional[Callable[[dict], None]] = None) -> None:
        self.bus = bus
        self.on_event = on_event or (lambda event: None)
        self.last_telemetry: Optional[dict[str, Any]] = None
        self.remembered: dict[str, dict[str, Any]] = {}
        self.current: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def note_telemetry(self, frame: dict[str, Any]) -> None:
        self.last_telemetry = frame

    # ------------------------------------------------------------------ control
    def start(self, scenario_id: str) -> dict[str, Any]:
        scenarios = process.domain().SCENARIOS
        if scenario_id not in scenarios:
            return {"ok": False, "error": f"unknown scenario '{scenario_id}'"}
        if self._thread and self._thread.is_alive():
            return {"ok": False, "error": f"scenario '{self.current}' is still running"}
        scenario = scenarios[scenario_id]
        self._stop.clear()
        self.current = scenario_id
        self._thread = threading.Thread(target=self._run, args=(scenario,), daemon=True)
        self._thread.start()
        return {"ok": True, "scenario": scenario_id, "title": scenario.title,
                "duration_hint": scenario.duration_hint}

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def reset_plant(self) -> None:
        self.bus.publish(config.TOPIC_CONTROL, {"reset": True, "source": "operator-hmi"})

    # -------------------------------------------------------------------- steps
    def _run(self, scenario: Scenario) -> None:
        self._narrate(f"SCENARIO START — {scenario.title}", scenario, "START")
        try:
            for step in scenario.steps:
                if self._stop.wait(step.delay):
                    self._narrate(f"SCENARIO ABORTED — {scenario.title}", scenario, "ABORT")
                    return
                self._execute(step, scenario)
            self._narrate(f"SCENARIO COMPLETE — {scenario.title}. Expected: {scenario.expect}",
                          scenario, "END")
        finally:
            self.current = None

    def _execute(self, step: Step, scenario: Scenario) -> None:
        if step.kind == "narrate":
            self._narrate(step.note, scenario, "NOTE")
        elif step.kind == "wait":
            self._stop.wait(step.seconds)
        elif step.kind == "sim":
            # Simulator-only hook (fault inject / clear): the physical world changes,
            # no command is sent, and the guard only sees the consequences.
            self._narrate(step.note, scenario, "STEP")
            self.bus.publish(config.TOPIC_SIM, {step.action: step.value, "source": step.source})
        elif step.kind == "command":
            command = Command(action=step.action, source=step.source, value=step.value)
            self._narrate(step.note, scenario, "STEP")
            envelope = command.to_dict()
            self.bus.publish(config.TOPIC_COMMAND, envelope)
            if step.remember:
                self.remembered[step.remember] = envelope
            log.info("scenario %s -> %s (%s)", scenario.id, step.action, step.source)
        elif step.kind == "replay_command":
            captured = self.remembered.get(step.key)
            if not captured:
                self._narrate("Replay skipped: nothing captured", scenario, "NOTE")
                return
            # Verbatim replay: same id and payload, timestamp from when it was recorded.
            replayed = dict(captured, ts=captured["ts"] - int(step.age_s * 1000))
            self._narrate(step.note, scenario, "STEP")
            self.bus.publish(config.TOPIC_COMMAND, replayed)
        elif step.kind == "replay":
            self._replay(step, scenario)

    def _replay(self, step: Step, scenario: Scenario) -> None:
        frame = dict(self.last_telemetry or {})
        if not frame:
            self._narrate("Replay skipped: no telemetry captured yet", scenario, "NOTE")
            return
        self._narrate(step.note, scenario, "STEP")
        # Silence the real controller, then replay the captured frame verbatim — in the
        # background, so the script can keep attacking behind the frozen picture.
        self.bus.publish(config.TOPIC_SIM, {"telemetry_hold_s": step.seconds, "source": step.source})

        def loop() -> None:
            deadline = time.time() + step.seconds
            while time.time() < deadline and not self._stop.is_set():
                self.bus.publish(config.TOPIC_TELEMETRY, frame)
                self._stop.wait(config.TELEMETRY_PERIOD_S)

        threading.Thread(target=loop, daemon=True).start()

    def _narrate(self, detail: str, scenario: Scenario, phase: str) -> None:
        if not detail:
            return
        event = Event("SCENARIO", scenario.id, {
            "detail": detail, "phase": phase, "kind": scenario.kind,
            "title": scenario.title, "expect": scenario.expect,
        })
        self.bus.publish(config.TOPIC_EVENT, event.to_dict())
        self.on_event(event.to_dict())


def catalogue() -> list[dict[str, Any]]:
    return [{
        "id": s.id, "title": s.title, "kind": s.kind, "narrative": s.narrative,
        "expect": s.expect, "duration_hint": s.duration_hint, "steps": len(s.steps),
    } for s in ordered(process.domain().SCENARIOS)]
