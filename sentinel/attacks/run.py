"""Attack simulator CLI.

    python -m sentinel.attacks.run --list
    python -m sentinel.attacks.run unsafe_valve
    python -m sentinel.attacks.run all
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .. import config
from ..bus import Bus
from .scenarios import SCENARIOS, ScenarioRunner, catalogue


def main() -> int:
    parser = argparse.ArgumentParser(description="Sentinel attack/operations scenario simulator")
    parser.add_argument("scenario", nargs="?", help="scenario id, or 'all'")
    parser.add_argument("--list", action="store_true", help="list available scenarios")
    args = parser.parse_args()

    if args.list or not args.scenario:
        print(f"{'ID':<24} {'KIND':<12} EXPECTED GUARD VERDICT")
        for item in catalogue():
            print(f"{item['id']:<24} {item['kind']:<12} {item['expect']}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    bus = Bus("attacks").connect()
    runner = ScenarioRunner(bus)
    bus.subscribe(config.TOPIC_TELEMETRY, lambda topic, payload: runner.note_telemetry(payload))
    time.sleep(1.0)   # let a telemetry frame arrive before a replay scenario

    ids = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for scenario_id in ids:
        result = runner.start(scenario_id)
        if not result.get("ok"):
            print(result.get("error"), file=sys.stderr)
            return 1
        print(f"running: {result['title']}")
        while runner.running:
            time.sleep(0.3)
        time.sleep(3.0)
    bus.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
