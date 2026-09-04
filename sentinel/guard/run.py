"""Command Guard service.

    python -m sentinel.guard.run

Subscribes : plant/telemetry, plant/command
Publishes  : guard/alert, guard/assessment, guard/status
"""
from __future__ import annotations

import logging
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
            log.info("guard state reset")

    def run(self) -> None:
        self.bus.connect()
        self.bus.subscribe(config.TOPIC_TELEMETRY,
                           lambda topic, payload: self.guard.observe_telemetry(payload))
        self.bus.subscribe(config.TOPIC_COMMAND,
                           lambda topic, payload: self.guard.observe_command(payload))
        self.bus.subscribe(config.TOPIC_CONTROL, self._on_control)
        log.info("command guard observing %s and %s", config.TOPIC_TELEMETRY, config.TOPIC_COMMAND)

        while self.running:
            time.sleep(1.0)
            self.guard.evaluate_process()
            self.bus.publish(config.TOPIC_STATUS, self.guard.status(), retain=True)


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
