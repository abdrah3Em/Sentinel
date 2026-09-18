"""Command Guard service.

    python -m sentinel.guard.run

Subscribes : plant/telemetry, plant/command
Publishes  : guard/alert, guard/assessment, guard/status
"""
from __future__ import annotations

import json
import logging
import os
import time

from .. import config
from ..bus import Bus
from ..models import Alert
from .engine import CommandGuard

log = logging.getLogger("sentinel.guard")


class GuardService:
    def __init__(self) -> None:
        self.bus = Bus("guard")
        self.guard = CommandGuard(on_alert=self._publish_alert,
                                  on_assessment=self._publish_assessment)
        self.running = True

    def _publish_alert(self, alert: Alert) -> None:
        self.bus.publish(config.TOPIC_ALERT, alert.to_dict())
        log.warning("[%s %3d] %s  (%s)", alert.level, alert.score, alert.summary, alert.rule)

    def _publish_assessment(self, assessment: dict) -> None:
        self.bus.publish(config.TOPIC_ASSESSMENT, assessment)

    def _on_control(self, topic: str, payload: dict) -> None:
        if payload.get("reset"):
            self.guard.reset()
            self._save()
            log.info("guard state reset")

    # ---------------------------------------------------------------- persistence
    def _load(self) -> None:
        path = config.GUARD_STATE_PATH
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        age = time.time() - float(data.get("saved_at", 0))
        if age > config.GUARD_STATE_MAX_AGE_S:
            log.info("guard snapshot is %.0f s old — starting clean", age)
            return
        self.guard.restore(data)
        log.info("guard state restored: %d commands, %d advisories", len(data.get("commands", [])),
                 len(data.get("alerts", [])))

    def _save(self) -> None:
        path = config.GUARD_STATE_PATH
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.guard.snapshot(), f)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("could not save guard state: %s", e)

    def run(self) -> None:
        self.bus.connect()
        self.bus.subscribe(config.TOPIC_TELEMETRY,
                           lambda topic, payload: self.guard.observe_telemetry(payload))
        self.bus.subscribe(config.TOPIC_COMMAND,
                           lambda topic, payload: self.guard.observe_command(payload))
        self.bus.subscribe(config.TOPIC_CONTROL, self._on_control)
        log.info("command guard observing %s and %s", config.TOPIC_TELEMETRY, config.TOPIC_COMMAND)
        self._load()

        ticks = 0
        while self.running:
            time.sleep(1.0)
            self.guard.evaluate_process()
            self.bus.publish(config.TOPIC_STATUS, self.guard.status(), retain=True)
            ticks += 1
            if ticks % 10 == 0:
                self._save()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(name)-16s %(levelname)-7s %(message)s")
    service = GuardService()
    try:
        service.run()
    except KeyboardInterrupt:
        service.running = False
        service.bus.stop()


if __name__ == "__main__":
    main()
