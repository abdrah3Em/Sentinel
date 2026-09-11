"""SQLite history for telemetry, commands, alerts and events.

Single writer (the API process), WAL mode, bounded retention — enough for the
timeline, the risk trend and CSV export without becoming a database project.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, seq INTEGER, tank_level REAL, pressure REAL, flow REAL,
    pump INTEGER, inlet_valve INTEGER, outlet_valve INTEGER, setpoint REAL,
    mode TEXT, maintenance INTEGER
);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts);

CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY, ts INTEGER NOT NULL, source TEXT, action TEXT, value REAL,
    verdict TEXT, score INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(ts);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, ts INTEGER NOT NULL, level TEXT, rule TEXT, score INTEGER,
    summary TEXT, equipment TEXT, reason TEXT, recommendation TEXT,
    command_id TEXT, context TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, ts INTEGER NOT NULL, type TEXT, source TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Store:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or config.DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------------ writes
    def add_telemetry(self, frame: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO telemetry (ts, seq, tank_level, pressure, flow, pump, inlet_valve,"
                " outlet_valve, setpoint, mode, maintenance) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (frame.get("ts"), frame.get("seq"), frame.get("tank_level"), frame.get("pressure"),
                 frame.get("flow"), int(bool(frame.get("pump"))), int(bool(frame.get("inlet_valve"))),
                 int(bool(frame.get("outlet_valve"))), frame.get("setpoint"), frame.get("mode"),
                 int(bool(frame.get("maintenance")))))
            self.db.commit()

    def add_command(self, command: dict[str, Any], verdict: str = "", score: int = 0) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO commands (id, ts, source, action, value, verdict, score)"
                " VALUES (?,?,?,?,?,?,?)",
                (command.get("id"), command.get("ts"), command.get("source"), command.get("action"),
                 command.get("value"), verdict, score))
            self.db.commit()

    def add_alert(self, alert: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO alerts (id, ts, level, rule, score, summary, equipment,"
                " reason, recommendation, command_id, context, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (alert.get("id"), alert.get("ts"), alert.get("level"), alert.get("rule"),
                 alert.get("score"), alert.get("summary"), alert.get("equipment"), alert.get("why"),
                 alert.get("recommendation"), alert.get("command_id"), alert.get("context"),
                 json.dumps(alert)))
            self.db.commit()

    def add_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO events (id, ts, type, source, payload) VALUES (?,?,?,?,?)",
                (event.get("id"), event.get("ts"), event.get("type"), event.get("source"),
                 json.dumps(event.get("payload", {}))))
            self.db.commit()

    # ------------------------------------------------------------------- reads
    def alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT payload FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, ts, type, source, payload FROM events ORDER BY ts DESC LIMIT ?",
                (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "type": r["type"], "source": r["source"],
                 "payload": json.loads(r["payload"])} for r in rows]

    def commands(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM commands ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def trend(self, limit: int = 240) -> list[dict[str, Any]]:
        """Recent telemetry, oldest first, for the trend chart."""
        with self.lock:
            rows = self.db.execute(
                "SELECT ts, tank_level, pressure, flow, setpoint FROM telemetry"
                " ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def clear(self) -> None:
        with self.lock:
            for table in ("telemetry", "commands", "alerts", "events"):
                self.db.execute(f"DELETE FROM {table}")
            self.db.commit()

    def prune(self, keep_telemetry: int = 5000) -> None:
        with self.lock:
            self.db.execute(
                "DELETE FROM telemetry WHERE id NOT IN"
                " (SELECT id FROM telemetry ORDER BY id DESC LIMIT ?)", (keep_telemetry,))
            self.db.commit()
