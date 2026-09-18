"""Guard persistence: SQLite in WAL mode, so a restart forgets nothing that matters.

What survives: command history and ids (sequence rules), setpoint samples
(drift baselines), the learned baseline, the signing verifier's counters and
nonces, the last telemetry frame (so permits, programs and switchgear state are
known before the next frame arrives), every advisory with its evidence, and
every assessment.  One writer per guard process.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, ts INTEGER, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, ts INTEGER, level TEXT, rule TEXT, score INTEGER, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS assessments (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blobs (key TEXT PRIMARY KEY, saved_at REAL NOT NULL, payload TEXT NOT NULL);
"""
BLOB_KEYS = ("ids", "setpoint_samples", "baseline", "verifier", "telemetry", "process_alerted")


class GuardStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or config.GUARD_DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------------ writes
    def add_command(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute("INSERT INTO commands (id, ts, payload) VALUES (?,?,?)",
                            (payload.get("id"), payload.get("ts"), json.dumps(payload)))
            self.db.execute("DELETE FROM commands WHERE rowid NOT IN (SELECT rowid FROM commands ORDER BY rowid DESC LIMIT ?)",
                            (config.COMMAND_HISTORY,))
            self.db.commit()

    def add_alert(self, alert: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO alerts (id, ts, level, rule, score, payload) VALUES (?,?,?,?,?,?)",
                            (alert.get("id"), alert.get("ts"), alert.get("level"), alert.get("rule"),
                             alert.get("score"), json.dumps(alert)))
            self.db.commit()

    def add_assessment(self, assessment: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute("INSERT INTO assessments (ts, payload) VALUES (?,?)",
                            (assessment.get("ts"), json.dumps(assessment)))
            self.db.execute("DELETE FROM assessments WHERE rowid NOT IN (SELECT rowid FROM assessments ORDER BY rowid DESC LIMIT 500)")
            self.db.commit()

    def save_blobs(self, blobs: dict[str, Any]) -> None:
        now = time.time()
        with self.lock:
            for key, value in blobs.items():
                self.db.execute("INSERT OR REPLACE INTO blobs (key, saved_at, payload) VALUES (?,?,?)",
                                (key, now, json.dumps(value)))
            self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('saved_at', ?)", (str(now),))
            self.db.commit()

    def clear(self) -> None:
        with self.lock:
            for table in ("commands", "alerts", "assessments", "blobs", "meta"):
                self.db.execute(f"DELETE FROM {table}")
            self.db.commit()

    # ------------------------------------------------------------------- reads
    def saved_at(self) -> float:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key='saved_at'").fetchone()
        return float(row["value"]) if row else 0.0

    def load(self) -> dict[str, Any]:
        with self.lock:
            commands = [json.loads(r["payload"]) for r in self.db.execute("SELECT payload FROM commands ORDER BY rowid").fetchall()]
            alerts = [json.loads(r["payload"]) for r in self.db.execute("SELECT payload FROM alerts ORDER BY ts").fetchall()]
            assessments = [json.loads(r["payload"]) for r in self.db.execute("SELECT payload FROM assessments ORDER BY rowid").fetchall()]
            blobs = {r["key"]: json.loads(r["payload"]) for r in self.db.execute("SELECT key, payload FROM blobs").fetchall()}
        return {"saved_at": self.saved_at(), "commands": commands, "alerts": alerts, "assessments": assessments, **blobs}

    def close(self) -> None:
        with self.lock:
            self.db.close()
