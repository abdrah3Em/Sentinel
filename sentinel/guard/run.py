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
from .store import GuardStore

log = logging.getLogger("sentinel.guard")


class GuardService:
    def __init__(self, store_path: str | None = None) -> None:
        self.bus = Bus("guard")
        self.store = GuardStore(store_path) if store_path else GuardStore()
        self.guard = CommandGuard(on_alert=self._publish_alert,
                                  on_assessment=self._publish_assessment)
        self.running = True

    def _publish_alert(self, alert: Alert) -> None:
        self.store.add_alert(alert.to_dict())
        self.bus.publish(config.TOPIC_ALERT, alert.to_dict())
        log.warning("[%s %3d] %s  (%s)", alert.level, alert.score, alert.summary, alert.rule)

    def _publish_assessment(self, assessment: dict) -> None:
        self.store.add_assessment(assessment)
        self.bus.publish(config.TOPIC_ASSESSMENT, assessment)

    def _on_command(self, topic: str, payload: dict) -> None:
        self.store.add_command(payload)
        self.guard.observe_command(payload)
        self._save()

    def _on_control(self, topic: str, payload: dict) -> None:
        if payload.get("reset"):
            self.guard.reset()
            self.store.clear()
            log.info("guard state reset")

    # ---------------------------------------------------------------- persistence
    def _load(self) -> None:
        data = self.store.load()
        if not data.get("saved_at"):
            return
        age = time.time() - data["saved_at"]
        if age > config.GUARD_STATE_MAX_AGE_S:
            log.info("persisted guard state is %.0f s old — starting clean", age)
            self.store.clear()
            return
        self.guard.restore(data)
        log.info("guard state restored: %d commands, %d advisories, baseline %d sources",
                 len(data.get("commands", [])), len(data.get("alerts", [])),
                 len((data.get("baseline") or {}).get("intervals", {})))

    def _save(self) -> None:
        try:
            self.store.save_blobs(self.guard.blobs())
        except Exception as e:      # noqa: BLE001 — persistence must never take the guard down
            log.warning("could not save guard state: %s", e)

    def run(self) -> None:
        self.bus.connect()
        self.bus.subscribe(config.TOPIC_TELEMETRY,
                           lambda topic, payload: self.guard.observe_telemetry(payload))
        self.bus.subscribe(config.TOPIC_COMMAND, self._on_command)
        self.bus.subscribe(config.TOPIC_CONTROL, self._on_control)
        log.info("command guard observing %s and %s", config.TOPIC_TELEMETRY, config.TOPIC_COMMAND)
        self._load()

        ticks = 0
        while self.running:
            time.sleep(1.0)
            self.guard.evaluate_process()
            self.bus.publish(config.TOPIC_STATUS, self.guard.status(), retain=True)
            ticks += 1
            if ticks % 5 == 0:
                self._save()
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
