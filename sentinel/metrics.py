"""Quantified results, generated — never typed by hand.

    python -m sentinel.metrics            # print docs/RESULTS.md to stdout
    python -m sentinel.metrics --write    # write docs/RESULTS.md and the README metrics block
    python -m sentinel.metrics --check    # exit 1 if the committed files differ from a fresh run

Everything here comes from replaying the scenario corpus through the offline
harness (deterministic: no broker, no wall clock, simulator noise off) plus a
timed run of the guard's own evaluation function.
"""
from __future__ import annotations

import argparse
import os
import re
import statistics
import subprocess
import sys
import time
from typing import Any

from . import evaluate, process
from .attacks.base import ordered
from .harness import Rig
from .models import Command

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_PATH = os.path.join(ROOT, "docs", "RESULTS.md")
README_PATH = os.path.join(ROOT, "readme.md")
LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# The brief names four attack types.  Which rules catch each, and which test proves it.
COVERAGE = [
    ("Command injection (a valid command nobody issued)",
     "STATE-002 (open a healthy feeder), STATE-005 (isolate the hospital), SEQ-001 (pipeline valve slam), SRC-001",
     "tests/test_grid_acceptance.py::test_open_healthy_feeder_names_the_hospital, "
     "tests/test_acceptance.py::test_outlet_close_with_pump_running_is_high_risk"),
    ("Valid command at the wrong moment",
     "STATE-001 (close onto fault), STATE-004 (energise under permit), STATE-003 (uncontrolled parallel), STATE-002 (pump start dead-headed)",
     "tests/test_grid_acceptance.py::test_close_onto_fault_is_critical_and_names_the_fault, "
     "tests/test_acceptance.py::test_pump_start_with_closed_outlet_is_high_risk"),
    ("Replay of old traffic",
     "CMD-001 (replayed command), TEL-002 (replayed telemetry), TEL-003 (injected frames), confidence LOW",
     "tests/test_grid_acceptance.py::test_replayed_command_is_flagged, "
     "tests/test_grid_acceptance.py::test_replay_is_flagged_and_confidence_is_low, "
     "tests/test_acceptance.py::test_replayed_command_is_flagged"),
    ("Slow setpoint drift",
     "ROC-002 (drift with time-to-limit), ROC-001 (large step), ENV-001 (outside the band)",
     "tests/test_grid_acceptance.py::test_slow_drift_is_flagged_before_the_limit_with_time_to_limit, "
     "tests/test_acceptance.py::test_slow_setpoint_drift_is_caught_before_it_leaves_the_band"),
]


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered_values = sorted(values)
    k = max(0, min(len(ordered_values) - 1, int(round(p * (len(ordered_values) - 1)))))
    return ordered_values[k]


def scenario_rows() -> dict[str, list[dict[str, Any]]]:
    return {pid: evaluate.evaluate(pid) for pid in process.KNOWN}


def advisory_latency(results: dict[str, list[dict[str, Any]]]) -> dict[str, list[float]]:
    """Simulated seconds from the step that provoked an advisory to the advisory, per rule."""
    latencies: dict[str, list[float]] = {}
    for pid in process.KNOWN:
        process.use(pid)
        for scenario in ordered(process.domain().SCENARIOS):
            rig = Rig(seconds_of_warmup=30)
            remembered: dict[str, dict] = {}
            for step in scenario.steps:
                rig.advance(step.delay)
                issued = rig.now
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
                else:
                    continue
                # Process-level advisories (integrity, physics) arrive on the periodic check
                # after the provoking step; command advisories arrive synchronously.
                rig.advance(6)
                for alert in list(rig.guard.alerts)[before:]:
                    latencies.setdefault(alert.rule, []).append(max(0.0, alert.ts / 1000.0 - issued))
    return latencies


def engine_latency_ms(samples: int = 400) -> list[float]:
    """Wall-clock time the guard takes to judge one command, feeder profile, with history warm."""
    process.use("grid")
    rig = Rig(seconds_of_warmup=60)
    for i in range(10):
        rig.send("avc_target", 11.0 + (i % 3) * 0.02, settle=2)
    out: list[float] = []
    actions = ["cb_close", "cb_open", "tie_close", "avc_target", "pv_curtail", "sw_open"]
    for i in range(samples):
        action = actions[i % len(actions)]
        value = 11.02 if action == "avc_target" else (10 if action == "pv_curtail" else None)
        command = Command(action=action, source="operator-hmi", value=value)
        command.ts = int(rig.now * 1000)
        t0 = time.perf_counter()
        rig.guard.observe_command(command.to_dict(), now=rig.now)
        out.append((time.perf_counter() - t0) * 1000.0)
        rig.now += 0.05
    return out


def baseline_training() -> dict[str, dict[str, Any]]:
    """Replay every legitimate scenario of a process through one guard, in order, three times
    over (a shift's worth of traffic), and report what the baseline learned."""
    out: dict[str, dict[str, Any]] = {}
    for pid in process.KNOWN:
        process.use(pid)
        rig = Rig(seconds_of_warmup=30)
        legit = [s for s in ordered(process.domain().SCENARIOS) if s.kind != "attack"]
        for _ in range(3):
            for scenario in legit:
                for step in scenario.steps:
                    rig.advance(step.delay)
                    if step.kind == "command":
                        rig.send(step.action, step.value, step.source, settle=0)
                    elif step.kind == "sim":
                        rig.sim(step.action, step.value, settle=0)
                    elif step.kind == "wait":
                        rig.advance(step.seconds)
                rig.advance(20)
        out[pid] = rig.guard.state.baseline.summary()
    return out


def confusion(results: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    m = {"tp": 0, "fn": 0, "tn": 0, "fp": 0}
    for rows in results.values():
        for r in rows:
            if r["kind"] == "attack":
                m["tp" if r["passed"] else "fn"] += 1
            else:
                m["tn" if r["passed"] else "fp"] += 1
    return m


INTEGRATION = ["tests/test_integration.py", "tests/test_grid_integration.py"]


def test_count() -> tuple[int, int]:
    """(offline tests passed, integration tests collected).

    The offline suite is deterministic on any machine; the integration tests need a live
    broker and a guard sharing this checkout's signing master, so they are counted, not run."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
                           *(f"--ignore={p}" for p in INTEGRATION)], capture_output=True, text=True, cwd=ROOT)
    passed = 0
    for line in proc.stdout.splitlines()[::-1]:
        m = re.search(r"(\d+) passed", line)
        if m:
            passed = int(m.group(1))
            break
    collected = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                                *INTEGRATION], capture_output=True, text=True, cwd=ROOT)
    integration = sum(1 for line in collected.stdout.splitlines() if "::" in line)
    return passed, integration


def build() -> tuple[str, str]:
    """(RESULTS.md body, README metrics block)."""
    results = scenario_rows()
    lat = advisory_latency(results)
    eng = engine_latency_ms()
    m = confusion(results)
    passed, skipped = test_count()
    legit = [r for rows in results.values() for r in rows if r["kind"] != "attack"]
    attacks = [r for rows in results.values() for r in rows if r["kind"] == "attack"]
    fp_above_low = sum(r["above_low"] for r in legit)
    fp_low = sum(r["advisories"] - r["above_low"] for r in legit)
    flagship = next(r for r in results["grid"] if r["id"] == "close_onto_fault")
    grid_lat = [v for k, v in lat.items() if k in ("STATE-001", "STATE-002", "STATE-003", "STATE-004", "STATE-005")]
    flat = [x for xs in grid_lat for x in xs]

    L = []
    L.append("# Results")
    L.append("")
    L.append("Generated by `make metrics` from a replay of every scenario through the offline harness "
             "(no broker, no wall clock, simulator noise off) and a timed run of the guard. Do not edit by hand.")
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append(f"- Tests: **{passed} offline tests pass** on any machine; {skipped} further integration tests run against a live broker (`make test`).")
    L.append(f"- Attacks detected at their required level: **{m['tp']} of {m['tp'] + m['fn']}** across both processes.")
    L.append(f"- Legitimate scenarios with an advisory above LOW: **{fp_above_low}** (absolute count) out of {len(legit)} scenarios; "
             f"LOW-only advisories in those scenarios: {fp_low}.")
    L.append(f"- Flagship (close onto a standing fault): **{flagship['worst_level']} · {flagship['worst_rule']} · score {flagship['worst_score']}**, "
             f"raised at step {flagship['detected_at']} of {flagship['steps']}.")
    L.append(f"- Guard evaluation time per command (feeder, warm history, {len(eng)} samples): "
             f"p50 **{_pct(eng, 0.5):.2f} ms**, p95 **{_pct(eng, 0.95):.2f} ms**, max **{max(eng):.2f} ms**.")
    if flat:
        L.append(f"- Advisory latency for wrong-moment switching rules (simulated time from the command to the advisory): "
                 f"p50 **{_pct(flat, 0.5):.1f} s**, p95 **{_pct(flat, 0.95):.1f} s**, max **{max(flat):.1f} s** "
                 "(command rules judge before the controller acts; process rules wait for the next periodic check).")
    L.append("")
    L.append("## Confusion matrix (every scenario, both processes)")
    L.append("")
    L.append("| | Advisory ≥ required level | Nothing above LOW |")
    L.append("|---|---|---|")
    L.append(f"| Attack scenarios ({len(attacks)}) | **{m['tp']}** true positives | {m['fn']} false negatives |")
    L.append(f"| Legitimate scenarios ({len(legit)}) | {m['fp']} false positives | **{m['tn']}** true negatives |")
    L.append("")
    L.append("## Coverage of the brief's four attack types")
    L.append("")
    L.append("| Attack type in the brief | Rules that catch it | Test that proves it |")
    L.append("|---|---|---|")
    for kind, rules, tests in COVERAGE:
        L.append(f"| {kind} | {rules} | `{tests.replace(', ', '`, `')}` |")
    L.append("")
    L.append("## Detection latency per rule")
    L.append("")
    L.append("Simulated seconds from the provoking step to the advisory, over the whole corpus.")
    L.append("")
    L.append("| Rule | Advisories | p50 s | p95 s | max s |")
    L.append("|---|---|---|---|---|")
    for rule in sorted(lat):
        v = lat[rule]
        L.append(f"| {rule} | {len(v)} | {_pct(v, 0.5):.1f} | {_pct(v, 0.95):.1f} | {max(v):.1f} |")
    L.append("")
    L.append("## Engine evaluation time")
    L.append("")
    L.append("| Samples | p50 ms | p95 ms | max ms | mean ms |")
    L.append("|---|---|---|---|---|")
    L.append(f"| {len(eng)} | {_pct(eng, 0.5):.2f} | {_pct(eng, 0.95):.2f} | {max(eng):.2f} | {statistics.mean(eng):.2f} |")
    L.append("")
    L.append("## Learned baseline, trained on the legitimate corpus")
    L.append("")
    L.append("Three passes over every legitimate scenario of each process, one guard, in order. "
             "The learner needs 20 samples before it speaks; below that the configured thresholds rule alone.")
    L.append("")
    for pid, b in baseline_training().items():
        L.append(f"**{evaluate.PROCESS_NAMES.get(pid, pid)}** — configured cadence: {b['configured']['cadence']}; "
                 f"configured range: {b['configured']['range']}")
        L.append("")
        L.append("| Learned | Source / action | Value | Samples |")
        L.append("|---|---|---|---|")
        for src, s in sorted(b["sources"].items()):
            cadence = (f"median {s['median_interval_s']} s · floor {s['floor_interval_s']} s · EWMA {s['ewma_interval_s']} s"
                       if s["median_interval_s"] is not None else "—")
            L.append(f"| cadence | {src} | {cadence}{'' if s['ready'] else ' (learning)'} | {s['samples']} |")
            if s["mix"]:
                L.append(f"| command mix | {src} | " + " · ".join(f"{a} {int(f * 100)} %" for a, f in s["mix"].items()) + f" | {sum(1 for _ in s['mix'])} actions |")
        for action, v in sorted(b["actions"].items()):
            L.append(f"| range | {action} | {v['p05']} – {v['p95']}{'' if v['ready'] else ' (learning)'} | {v['samples']} |")
        L.append("")
    L.append("## Every scenario")
    L.append("")
    L.append(evaluate.markdown(results))
    body = "\n".join(L).rstrip() + "\n"

    block = "\n".join([
        f"- **{passed} offline tests pass**; {skipped} more run end to end over a live broker.",
        f"- **{m['tp']}/{m['tp'] + m['fn']} attack scenarios** detected at their required level; "
        f"**{fp_above_low} false positives** above LOW across {len(legit)} legitimate scenarios.",
        f"- Flagship close-onto-fault: **{flagship['worst_level']} · score {flagship['worst_score']}**, "
        f"raised the moment the command arrives (evaluation p95 **{_pct(eng, 0.95):.2f} ms**).",
        "- Full tables: [docs/RESULTS.md](docs/RESULTS.md).",
    ]) + "\n"
    return body, block


def splice(path: str, start: str, end: str, block: str) -> str:
    text = open(path).read()
    if start not in text or end not in text:
        raise SystemExit(f"{path}: markers {start!r}/{end!r} not found")
    a, b = text.index(start) + len(start), text.index(end)
    return text[:a] + "\n" + block + text[b:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    body, block = build()
    readme = splice(README_PATH, "<!-- metrics:start -->", "<!-- metrics:end -->", block)
    if args.check:
        current = open(RESULTS_PATH).read() if os.path.exists(RESULTS_PATH) else ""
        drift = []
        if _normalise(current) != _normalise(body):
            drift.append("docs/RESULTS.md")
        if _normalise(open(README_PATH).read()) != _normalise(readme):
            drift.append("readme.md")
        if drift:
            print("metrics drift in: " + ", ".join(drift))
            return 1
        print("metrics up to date")
        return 0
    if args.write:
        open(RESULTS_PATH, "w").write(body)
        open(README_PATH, "w").write(readme)
        print("wrote docs/RESULTS.md and the README metrics block")
        return 0
    print(body)
    return 0


def _normalise(text: str) -> str:
    """Timing numbers vary run to run; compare everything except the timed lines."""
    keep = []
    for line in text.splitlines():
        if " ms" in line or re.match(r"^\| \d+ \| [\d.]+ \| [\d.]+ \| [\d.]+ \| [\d.]+ \|$", line):
            continue                                   # wall-clock timings vary run to run
        keep.append(line.rstrip())
    return "\n".join(keep).strip()


if __name__ == "__main__":
    sys.exit(main())
