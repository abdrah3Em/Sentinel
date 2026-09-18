"""Feeder integration: simulator -> MQTT -> guard, on the grid topic namespace.

Runs only when a broker is listening; otherwise skipped.  Reuses a running
feeder stack if one is publishing, else starts plant + guard in-process.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("paho.mqtt")

from sentinel import config
from sentinel.bus import Bus
from sentinel.guard.run import GuardService
from sentinel.models import Command
from sentinel.plant.run import PlantService

GRID_TOPICS = {name: f"grid/{getattr(config, name).split('/', 1)[1]}"
               for name in ("TOPIC_TELEMETRY", "TOPIC_COMMAND", "TOPIC_EVENT", "TOPIC_MODE", "TOPIC_ALERT",
                            "TOPIC_ASSESSMENT", "TOPIC_STATUS", "TOPIC_CONTROL", "TOPIC_SIM")}


def broker_up() -> bool:
    try:
        with socket.create_connection((config.MQTT_HOST, config.MQTT_PORT), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not broker_up(), reason="no MQTT broker")


@pytest.fixture(scope="module")
def stack():
    saved = {name: getattr(config, name) for name in GRID_TOPICS}
    for name, topic in GRID_TOPICS.items():          # services read config at call time
        setattr(config, name, topic)
    observer = Bus("gitest").connect()
    seen = {"alerts": [], "telemetry": []}
    observer.subscribe(config.TOPIC_ALERT, lambda t, p: seen["alerts"].append(p))
    observer.subscribe(config.TOPIC_TELEMETRY, lambda t, p: seen["telemetry"].append(p))
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
    assert seen["telemetry"] and "v_bus_kv" in seen["telemetry"][-1], "no feeder telemetry on grid/plant/telemetry"
    try:
        yield observer, seen
    finally:
        for service in services:
            service.running = False
        observer.stop()
        for name, topic in saved.items():
            setattr(config, name, topic)


def wait_for(predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_close_onto_fault_alert_end_to_end(stack):
    observer, seen = stack
    observer.publish(config.TOPIC_CONTROL, {"reset": True, "source": "itest"})
    time.sleep(1.5)
    seen["alerts"].clear()
    observer.publish(config.TOPIC_SIM, {"fault_inject": "S2", "source": "itest"})
    assert wait_for(lambda: seen["telemetry"] and seen["telemetry"][-1].get("protection_tripped")), "no trip"
    time.sleep(1.0)
    observer.publish(config.TOPIC_COMMAND, Command(action="cb_close", source="engineering-laptop").to_dict())
    assert wait_for(lambda: any(a["rule"] == "STATE-001" for a in seen["alerts"]))
    alert = next(a for a in seen["alerts"] if a["rule"] == "STATE-001")
    assert alert["level"] in ("HIGH", "CRITICAL") and alert["score"] >= 80
    assert wait_for(lambda: seen["telemetry"][-1].get("close_onto_fault_count", 0) >= 1)
    observer.publish(config.TOPIC_SIM, {"fault_clear": True, "source": "itest"})


def test_program_coverage_is_respected_end_to_end(stack):
    """A program naming only TS-201 excuses the tie close but not a breaker open."""
    observer, seen = stack
    observer.publish(config.TOPIC_CONTROL, {"reset": True, "source": "itest"})
    time.sleep(1.5)
    seen["alerts"].clear()
    try:
        observer.publish(config.TOPIC_COMMAND, Command(action="switching_program_on", value="SP-9:TS-201").to_dict())
        time.sleep(1.0)
        observer.publish(config.TOPIC_COMMAND, Command(action="tie_close").to_dict())      # covered
        time.sleep(2.0)
        observer.publish(config.TOPIC_COMMAND, Command(action="tie_open").to_dict())
        time.sleep(2.0)
        observer.publish(config.TOPIC_COMMAND, Command(action="cb_open").to_dict())        # not covered
        assert wait_for(lambda: any(a["rule"] == "STATE-002" for a in seen["alerts"]))
        assert all(a["level"] == "LOW" for a in seen["alerts"] if a["rule"] in ("STATE-003", "CTX-001"))
        assert all(a["level"] in ("HIGH", "CRITICAL") for a in seen["alerts"] if a["rule"] == "STATE-002")
    finally:
        for action in ("cb_close", "switching_program_off"):
            observer.publish(config.TOPIC_COMMAND, Command(action=action).to_dict())
            time.sleep(0.5)
        observer.publish(config.TOPIC_CONTROL, {"reset": True, "source": "itest"})
