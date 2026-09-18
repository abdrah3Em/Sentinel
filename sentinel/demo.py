"""The four-minute demo as a test.

    python -m sentinel.demo            # headless: replay the exact sequence, assert every verdict
    python -m sentinel.demo --live     # drive a running console (default http://localhost:8080)

Headless mode uses the offline harness (no broker, no wall clock), so the demo
is deterministic: the same commands, the same physics, the same verdicts.  Live
mode sends the same scenario to the console API and waits for the same
advisories, so a rehearsal on the demo laptop is the same check.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from typing import Any, Callable, Optional

from . import process
from .harness import Rig

ATTACKER = "engineering-laptop"
LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


class Script:
    """Collects timestamped steps and their pass/fail so the table reads like the demo."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, bool]] = []
        self.t0: Optional[float] = None

    def stamp(self, now: float) -> str:
        if self.t0 is None:
            self.t0 = now
        s = int(now - self.t0)
        return f"T+{s // 60:d}:{s % 60:02d}"

    def check(self, when: float, step: str, expected: str, ok: bool, actual: str = "") -> None:
        self.rows.append((self.stamp(when), step, f"{expected}" + (f" → {actual}" if actual else ""), ok))

    def table(self) -> str:
        lines = ["| Time | Step | Expected → observed | Result |", "|---|---|---|---|"]
        for t, step, exp, ok in self.rows:
            lines.append(f"| {t} | {step} | {exp} | {'PASS' if ok else 'FAIL'} |")
        return "\n".join(lines)

    @property
    def passed(self) -> bool:
        return all(ok for *_, ok in self.rows)


# ---------------------------------------------------------------------------- headless
def run_headless() -> Script:
    process.use("grid")
    rig = Rig(seconds_of_warmup=30)
    s = Script()

    # 1. Normal traffic: an operator trim and a PV curtailment.
    rig.send("avc_target", 11.05, settle=6)
    rig.send("pv_curtail", 20, settle=6)
    rig.send("pv_curtail", 0, settle=6)
    quiet = all(_rank(a.level) < 1 for a in rig.guard.alerts)
    s.check(rig.now, "Normal traffic: AVC trim, PV curtail", "nothing above LOW", quiet,
            f"{len(rig.guard.alerts)} advisories")

    # 2. A fault on S2; protection trips CB-101; the hospital bus goes dark.
    rig.sim("fault_inject", "S2", settle=3)
    t = rig.plant.telemetry()
    s.check(rig.now, "Fault on S2 → protection trips CB-101", "CB-101 open, B3 dead, FI-2 set",
            (not t.cb_closed) and t.protection_tripped and not t.supplied["b3"] and t.fault_indicators[1],
            f"cb_closed={t.cb_closed} b3={t.supplied['b3']}")

    # 3. The flagship: a valid cb_close before the fault is cleared.
    before = len(rig.guard.alerts)
    alert = rig.send("cb_close", source=ATTACKER, settle=0)
    ok = alert is not None and alert.rule == "STATE-001" and alert.level == "CRITICAL" and alert.score >= 80
    s.check(rig.now, "cb_close from engineering-laptop onto the fault", "CRITICAL · STATE-001 · score ≥ 80", ok,
            f"{alert.level} · {alert.rule} · {alert.score}" if alert else "no advisory")
    details = " ".join(f["detail"] for f in (alert.findings if alert else []))
    s.check(rig.now, "Advisory names the evidence", "FI-2, S2, hospital bus, source",
            all(k in details for k in ("FI-2", "S2", "Hospital", ATTACKER)))
    rig.advance(1)
    t = rig.plant.telemetry()
    s.check(rig.now, "Physical consequence", "6.5 kA flash, re-trip, close-onto-fault count 1",
            t.close_onto_fault_count == 1 and not t.cb_closed, f"count={t.close_onto_fault_count} cb_closed={t.cb_closed}")

    # 4. Context: the same command after the crew clears the fault and protection is reset.
    rig.sim("fault_clear", settle=2)
    reset_alert = rig.send("protection_reset", settle=2)
    close_alert = rig.send("cb_close", settle=2)
    t = rig.plant.telemetry()
    s.check(rig.now, "Same cb_close after clear + reset", "quiet, all buses supplied",
            reset_alert is None and close_alert is None and all(t.supplied.values()),
            f"reset={reset_alert.rule if reset_alert else 'quiet'} close={close_alert.rule if close_alert else 'quiet'}")

    # 5. The frozen picture: replayed telemetry, an attack behind it, a replayed command.
    rig.send("pv_curtail", 0, settle=2)
    captured = rig.last_command
    n_before = len(rig.guard.alerts)
    rig.hold_and_replay(30)
    rig.advance(4)
    open_alert = rig.send("sw_open", source=ATTACKER, settle=4)
    replay_alert = rig.replay_command(captured, 300)
    tel = [a for a in list(rig.guard.alerts)[n_before:] if a.rule == "TEL-002"]
    s.check(rig.now, "Telemetry frozen and replayed", "TEL-002 raised, confidence LOW",
            bool(tel) and all(a.confidence == "LOW" for a in tel), f"{len(tel)} advisories")
    s.check(rig.now, "sw_open behind the frozen picture", "advisory at LOW confidence",
            open_alert is not None and open_alert.confidence == "LOW",
            f"{open_alert.level} · {open_alert.rule} · conf {open_alert.confidence}" if open_alert else "no advisory")
    s.check(rig.now, "Captured command replayed verbatim", "CMD-001, HIGH or above",
            replay_alert is not None and replay_alert.rule == "CMD-001" and _rank(replay_alert.level) >= 2,
            f"{replay_alert.level} · {replay_alert.rule}" if replay_alert else "no advisory")
    return s


# ---------------------------------------------------------------------------- live
def _get(base: str, path: str, token: str = "") -> Any:
    req = urllib.request.Request(base + path, headers={"X-Sentinel-Token": token} if token else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def _post(base: str, path: str, body: Optional[dict] = None, token: str = "") -> Any:
    data = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Sentinel-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def _wait(pred: Callable[[], bool], timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.5)
    return False


def run_live(base: str, token: str) -> Script:
    s = Script()
    desc = _get(base, "/api/process")
    s.check(time.time(), f"Console at {base}", "grid simulation", desc.get("id") == "grid", desc.get("id", "?"))
    _post(base, "/api/reset", token=token)
    time.sleep(3)
    alerts = lambda: _get(base, "/api/alerts?limit=50")  # noqa: E731

    _post(base, "/api/scenario/normal_ops", token=token)
    time.sleep(26)
    s.check(time.time(), "Scenario 9: normal operations", "nothing above LOW",
            all(_rank(a["level"]) < 1 for a in alerts()), f"{len(alerts())} advisories")

    _post(base, "/api/scenario/close_onto_fault", token=token)
    found = _wait(lambda: any(a["rule"] == "STATE-001" and a["level"] == "CRITICAL" for a in alerts()), 25)
    s.check(time.time(), "Scenario 1: close onto a standing fault", "CRITICAL · STATE-001", found)
    time.sleep(20)
    state = _get(base, "/api/state")
    s.check(time.time(), "Restoration after clear + reset", "all buses supplied, one close-onto-fault",
            all(state["supplied"].values()) and state["close_onto_fault_count"] == 1,
            f"supplied={state['supplied']} count={state['close_onto_fault_count']}")

    n = len(alerts())
    _post(base, "/api/scenario/telemetry_replay", token=token)
    found = _wait(lambda: any(a["rule"] == "TEL-002" and a["confidence"] == "LOW" for a in alerts()[: max(1, len(alerts()) - n)]), 40)
    s.check(time.time(), "Scenario 5: frozen telemetry", "TEL-002 at LOW confidence", found)
    found = _wait(lambda: any(a["rule"] == "CMD-001" for a in alerts()), 30)
    s.check(time.time(), "Captured command replayed", "CMD-001", found)
    return s


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the four-minute demo and assert every verdict")
    parser.add_argument("--live", action="store_true", help="drive a running console instead of the offline harness")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--token", default="", help="operator token for the console's write endpoints")
    args = parser.parse_args()
    script = run_live(args.url, args.token) if args.live else run_headless()
    print(script.table())
    print()
    print("DEMO PASS" if script.passed else "DEMO FAIL")
    return 0 if script.passed else 1


if __name__ == "__main__":
    sys.exit(main())
