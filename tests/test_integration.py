"""PRD section 51 — integration: simulator -> MQTT -> detector -> dashboard.

Runs only when a broker is listening on the configured port; otherwise skipped.
Spins up its own plant and guard services in-process against the live broker.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from sentinel import config
from sentinel.bus import Bus
from sentinel.guard.run import GuardService
from sentinel.models import Command
from sentinel.plant.run import PlantService


def broker_up() -> bool:
    try:
        with socket.create_connection((config.MQTT_HOST, config.MQTT_PORT), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not broker_up(), reason="no MQTT broker on "
                                f"{config.MQTT_HOST}:{config.MQTT_PORT}")


@pytest.fixture(scope="module")
def stack():
    observer = Bus("itest").connect()
    seen = {"alerts": [], "telemetry": [], "status": []}
    observer.subscribe(config.TOPIC_ALERT, lambda t, p: seen["alerts"].append(p))
    observer.subscribe(config.TOPIC_TELEMETRY, lambda t, p: seen["telemetry"].append(p))
    observer.subscribe(config.TOPIC_STATUS, lambda t, p: seen["status"].append(p))

    # Reuse a running demo stack if there is one (two plants on one broker would
    # fight); otherwise start plant + guard in-process for the duration.
    time.sleep(2.0)
    services = []
    if not seen["telemetry"]:
        plant, guard = PlantService(), GuardService()
        services = [plant, guard]
        threading.Thread(target=plant.run, daemon=True).start()
        threading.Thread(target=guard.run, daemon=True).start()
        deadline = time.time() + 10
        while time.time() < deadline and len(seen["telemetry"]) < 4:
            time.sleep(0.2)
    assert seen["telemetry"], "plant never published telemetry over MQTT"
    yield observer, seen
    for service in services:
        service.running = False
    observer.stop()


def wait_for(predicate, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_end_to_end_unsafe_valve_alert_under_one_second(stack):
    observer, seen = stack
    observer.publish(config.TOPIC_COMMAND, Command("outlet_open").to_dict())
    observer.publish(config.TOPIC_COMMAND, Command("pump_start").to_dict())
    time.sleep(3.0)
    before = len(seen["alerts"])
    sent = time.time()
    observer.publish(config.TOPIC_COMMAND,
                     Command("outlet_close", source="maintenance-laptop").to_dict())
    assert wait_for(lambda: len(seen["alerts"]) > before)
    latency = time.time() - sent
    alert = seen["alerts"][-1]
    assert alert["rule"] == "SEQ-001" and alert["level"] in ("HIGH", "CRITICAL")
    assert latency < 1.0, f"detection latency {latency:.2f}s"        # NFR: < 1 s
    # Physical consequence shows up in telemetry: flow collapses, pressure climbs.
    assert wait_for(lambda: seen["telemetry"][-1]["flow"] < 5 and
                    seen["telemetry"][-1]["pressure"] > config.PRESSURE_MAX_BAR, timeout=12)


def test_guard_status_is_published_and_reflects_the_alert(stack):
    observer, seen = stack
    assert wait_for(lambda: seen["status"] and seen["status"][-1]["level"] in ("HIGH", "CRITICAL"),
                    timeout=4)
    status = seen["status"][-1]
    assert status["telemetry_fresh"] is True
    assert status["commands_seen"] >= 3


def test_maintenance_isolation_is_quiet_end_to_end(stack):
    observer, seen = stack
    observer.publish(config.TOPIC_COMMAND, Command("outlet_open").to_dict())
    time.sleep(2.5)
    observer.publish(config.TOPIC_COMMAND, Command("maintenance_on", source="maintenance-hmi").to_dict())
    time.sleep(2.0)
    observer.publish(config.TOPIC_COMMAND, Command("pump_stop", source="maintenance-hmi").to_dict())
    time.sleep(3.0)
    before = len(seen["alerts"])
    observer.publish(config.TOPIC_COMMAND, Command("outlet_close", source="maintenance-hmi").to_dict())
    time.sleep(2.0)
    new = seen["alerts"][before:]
    assert all(a["level"] in ("LOW",) for a in new), [(a["level"], a["rule"]) for a in new]
    observer.publish(config.TOPIC_COMMAND, Command("maintenance_off", source="maintenance-hmi").to_dict())
