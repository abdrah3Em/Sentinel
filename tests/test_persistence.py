"""Kill the guard mid-scenario, restart it, and detection must still fire with its memory intact."""
import os
import tempfile

from sentinel import config
from sentinel.guard.engine import CommandGuard
from sentinel.guard.store import GuardStore
from sentinel.models import Command
from tests.helpers import Rig


def persist(rig: Rig, store: GuardStore) -> None:
    for c in rig.guard.state.history.items:
        store.add_command(c.to_unsigned_dict())
    for a in rig.guard.alerts:
        store.add_alert(a.to_dict())
    for a in rig.guard.assessments:
        store.add_assessment(a)
    store.save_blobs(rig.guard.blobs())


def test_restart_keeps_history_baselines_permits_and_still_detects():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "guard.db")
        rig = Rig()
        # A shift of ordinary traffic teaches the baseline; a drift begins; a program and permit are declared.
        for i in range(24):
            rig.send("setpoint", 58 + (i % 5), settle=3)
        for value in (64, 68, 72):
            rig.send("setpoint", value, source="maintenance-laptop", settle=4)
        rig.send("maintenance_on", source="maintenance-hmi", settle=2)
        store = GuardStore(path)
        persist(rig, store)
        store.close()
        del store

        # --- the guard process dies here ---------------------------------------------------
        fresh = CommandGuard()
        store = GuardStore(path)
        data = store.load()
        fresh.restore(data)
        assert len(fresh.state.history.items) >= 20
        assert len(fresh.state.baseline.values["setpoint"]) >= 20          # learned range survived
        assert fresh.state.telemetry is not None and fresh.state.telemetry.mode == "MAINTENANCE"   # permit/mode context
        assert fresh.commands_seen == rig.guard.commands_seen
        assert len(fresh.alerts) == len(rig.guard.alerts)

        # Hand the restarted guard the live picture and continue the drift: it still knows the
        # earlier steps, so the trajectory rule fires on the next step and a replay is still a replay.
        rig.guard = fresh
        rig.advance(2)
        alert = rig.send("setpoint", 76, source="maintenance-laptop", settle=1)
        assert alert is not None and "ROC-002" in {f["rule"] for f in alert.findings}
        captured = rig.last_command
        replay = rig.replay_command(captured, age_s=1.0)
        assert replay is not None and "CMD-001" in {f["rule"] for f in replay.findings}
        store.close()


def test_stale_persisted_state_is_discarded():
    with tempfile.TemporaryDirectory() as tmp:
        store = GuardStore(os.path.join(tmp, "guard.db"))
        store.save_blobs({"ids": ["x"]})
        assert store.load()["saved_at"] > 0
        store.clear()
        assert store.load()["saved_at"] == 0.0
        store.close()


def test_store_is_wal_and_bounded():
    with tempfile.TemporaryDirectory() as tmp:
        store = GuardStore(os.path.join(tmp, "guard.db"))
        assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        for i in range(config.COMMAND_HISTORY + 25):
            store.add_command(Command(action="cb_open").to_unsigned_dict())
        assert len(store.load()["commands"]) == config.COMMAND_HISTORY
        store.close()
