"""Replay every scenario offline and report what the guard concluded.

    python -m sentinel.evaluate            # print the results table
    python -m sentinel.evaluate --write    # also refresh the Results section of readme.md

Deterministic: no broker, no wall clock, simulator noise off.  Each scenario's
"expected" verdict is parsed from the scenario itself, so the table doubles as
an acceptance check (see tests/test_evaluate.py).
"""
from __future__ import annotations

import argparse
import os
import re
from typing import Any

from . import process
from .attacks.base import ordered
from .harness import Rig

LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
PROCESS_NAMES = {"grid": "Grid — 11 kV feeder", "pipeline": "Pipeline pump station"}


def required_level(scenario) -> tuple[str, bool]:
    """(level, is_attack): attacks must reach `level`; legitimate ones must not exceed it."""
    found = [lvl for lvl in LEVELS if re.search(rf"\b{lvl}\b", scenario.expect)]
    if scenario.kind == "attack":
        return (max(found, key=LEVELS.index) if found else "HIGH"), True
    return "LOW", False


def run_scenario(process_id: str, scenario) -> dict[str, Any]:
    process.use(process_id)
    rig = Rig(seconds_of_warmup=30)
    remembered: dict[str, dict] = {}
    detected_at = None
    for index, step in enumerate(scenario.steps, start=1):
        rig.advance(step.delay)
        before = len(rig.guard.alerts)
        if step.kind == "command":
            rig.send(step.action, step.value, step.source, settle=0)
            if step.remember:
                remembered[step.remember] = rig.last_command
        elif step.kind == "sim":
            rig.sim(step.action, step.value, settle=0)
        elif step.kind == "replay":
            rig.hold_and_replay(step.seconds)
        elif step.kind == "replay_command" and step.key in remembered:
            rig.replay_command(remembered[step.key], step.age_s)
        elif step.kind == "wait":
            rig.advance(step.seconds)
        if detected_at is None and any(LEVELS.index(a.level) >= 2 for a in list(rig.guard.alerts)[before:]):
            detected_at = index
    rig.advance(3)
    alerts = list(rig.guard.alerts)
    worst = max(alerts, key=lambda a: a.score, default=None)
    level, is_attack = required_level(scenario)
    above_low = [a for a in alerts if LEVELS.index(a.level) > 0]
    if is_attack:
        passed = worst is not None and LEVELS.index(worst.level) >= LEVELS.index(level)
    else:
        passed = not above_low
    return {
        "process": process_id, "id": scenario.id, "title": scenario.title, "kind": scenario.kind,
        "expect": scenario.expect, "required": level, "worst_level": worst.level if worst else "—",
        "worst_rule": worst.rule if worst else "—", "worst_score": worst.score if worst else 0,
        "advisories": len(alerts), "above_low": len(above_low), "steps": len(scenario.steps),
        "detected_at": detected_at, "passed": passed,
        "low_confidence": sum(1 for a in alerts if a.confidence == "LOW"),
    }


def evaluate(process_id: str) -> list[dict[str, Any]]:
    process.use(process_id)
    scenarios = ordered(process.domain().SCENARIOS)
    return [run_scenario(process_id, s) for s in scenarios]


def markdown(results: dict[str, list[dict[str, Any]]]) -> str:
    lines = []
    for pid, rows in results.items():
        attacks = [r for r in rows if r["kind"] == "attack"]
        legit = [r for r in rows if r["kind"] != "attack"]
        caught = sum(1 for r in attacks if r["passed"])
        quiet = sum(1 for r in legit if r["passed"])
        lines.append(f"**{PROCESS_NAMES.get(pid, pid)}** — attacks detected {caught}/{len(attacks)}, "
                     f"legitimate operations with nothing above LOW {quiet}/{len(legit)}")
        lines.append("")
        lines.append("| Scenario | Kind | Expected | Worst advisory | Score | Detected at step | Above LOW |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            worst = f"{r['worst_level']} · {r['worst_rule']}" if r["worst_level"] != "—" else "none"
            step = f"{r['detected_at']} of {r['steps']}" if r["detected_at"] else "—"
            lines.append(f"| {r['title']} | {r['kind']} | {r['expect']} | {worst} | {r['worst_score']} | {step} | {r['above_low']} |")
        lines.append("")
    return "\n".join(lines)


def write_readme(text: str, path: str = "readme.md") -> bool:
    start, end = "<!-- results:start -->", "<!-- results:end -->"
    if not os.path.exists(path):
        return False
    body = open(path).read()
    if start not in body or end not in body:
        return False
    a, b = body.index(start) + len(start), body.index(end)
    open(path, "w").write(body[:a] + "\n" + text + "\n" + body[b:])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay every scenario offline and report the verdicts")
    parser.add_argument("--write", action="store_true", help="refresh the Results section of readme.md")
    args = parser.parse_args()
    results = {pid: evaluate(pid) for pid in process.KNOWN}
    text = markdown(results)
    print(text)
    if args.write:
        print("readme updated" if write_readme(text) else "readme markers not found")
    return 0 if all(r["passed"] for rows in results.values() for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
