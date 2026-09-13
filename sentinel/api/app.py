"""Dashboard backend: REST + server-sent events.

    python -m sentinel.api.app

This process is the only writer to SQLite.  It observes the same MQTT topics as
an operator would see, keeps a live picture for the HMI, and streams updates to
the browser over SSE (no extra dependencies, no websocket server to babysit).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Deque

from flask import Flask, Response, jsonify, request, send_from_directory

from .. import config
from ..attacks.scenarios import ScenarioRunner, catalogue
from ..bus import Bus
from ..guard.catalogue import catalogue as rule_catalogue, thresholds
from ..models import Command, now_ms
from ..store import Store

log = logging.getLogger("sentinel.api")


class Hub:
    """Fan-out of live updates to every connected browser."""

    def __init__(self) -> None:
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(q)

    def broadcast(self, kind: str, data: Any) -> None:
        message = json.dumps({"type": kind, "data": data})
        with self.lock:
            targets = list(self.clients)
        for q in targets:
            try:
                q.put_nowait(message)
            except queue.Full:
                self.unsubscribe(q)


class Dashboard:
    def __init__(self) -> None:
        self.store = Store()
        self.hub = Hub()
        self.bus = Bus("api")
        self.runner = ScenarioRunner(self.bus)
        self.state: dict[str, Any] = {}
        self.status: dict[str, Any] = {"status": "STARTING", "level": "NORMAL", "risk_score": 0,
                                       "headline": "Waiting for telemetry", "telemetry_fresh": False}
        self.alerts: Deque[dict] = deque(maxlen=200)
        self.events: Deque[dict] = deque(maxlen=400)
        self.assessments: Deque[dict] = deque(maxlen=200)
        self.trend: Deque[dict] = deque(maxlen=300)
        self._last_persist = 0.0
        self._telemetry_count = 0

    # ------------------------------------------------------------------ ingest
    def start(self) -> None:
        self.bus.connect()
        self.bus.subscribe(config.TOPIC_TELEMETRY, self._on_telemetry)
        self.bus.subscribe(config.TOPIC_EVENT, self._on_event)
        self.bus.subscribe(config.TOPIC_COMMAND, self._on_command)
        self.bus.subscribe(config.TOPIC_ALERT, self._on_alert)
        self.bus.subscribe(config.TOPIC_ASSESSMENT, self._on_assessment)
        self.bus.subscribe(config.TOPIC_STATUS, self._on_status)
        threading.Thread(target=self._housekeeping, daemon=True).start()

    def _on_telemetry(self, topic: str, payload: dict) -> None:
        self.state = payload
        self.runner.note_telemetry(payload)
        self._telemetry_count += 1
        self.trend.append({"ts": payload.get("ts"), "tank_level": payload.get("tank_level"),
                           "pressure": payload.get("pressure"), "flow": payload.get("flow"),
                           "setpoint": payload.get("setpoint")})
        self.hub.broadcast("telemetry", payload)
        if self._telemetry_count % 4 == 0:          # persist at ~0.5 Hz, plenty for a trend
            self.store.add_telemetry(payload)

    def _on_event(self, topic: str, payload: dict) -> None:
        self.events.appendleft(payload)
        self.store.add_event(payload)
        self.hub.broadcast("event", payload)

    def _on_command(self, topic: str, payload: dict) -> None:
        self.store.add_command(payload)
        self.hub.broadcast("command", payload)

    def _on_alert(self, topic: str, payload: dict) -> None:
        self.alerts.appendleft(payload)
        self.store.add_alert(payload)
        self.hub.broadcast("alert", payload)

    def _on_assessment(self, topic: str, payload: dict) -> None:
        self.assessments.appendleft(payload)
        command = payload.get("command", {})
        if command:
            self.store.add_command(command, payload.get("verdict", ""), payload.get("score", 0))
        self.hub.broadcast("assessment", payload)

    def _on_status(self, topic: str, payload: dict) -> None:
        self.status = payload
        self.hub.broadcast("status", payload)

    def _housekeeping(self) -> None:
        while True:
            time.sleep(60)
            self.store.prune()


dashboard = Dashboard()
app = Flask(__name__, static_folder="static", static_url_path="/static")


# --------------------------------------------------------------------- routes
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    return jsonify(dashboard.state)


@app.get("/api/status")
def api_status():
    return jsonify(dashboard.status)


@app.get("/api/alerts")
def api_alerts():
    limit = int(request.args.get("limit", 50))
    return jsonify(list(dashboard.alerts)[:limit])


@app.get("/api/events")
def api_events():
    limit = int(request.args.get("limit", 100))
    return jsonify(list(dashboard.events)[:limit])


@app.get("/api/assessments")
def api_assessments():
    limit = int(request.args.get("limit", 50))
    return jsonify(list(dashboard.assessments)[:limit])


@app.get("/api/trend")
def api_trend():
    return jsonify(list(dashboard.trend))


@app.get("/api/stats")
def api_stats():
    by_level: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for alert in dashboard.alerts:
        by_level[alert["level"]] = by_level.get(alert["level"], 0) + 1
        by_rule[alert["rule"]] = by_rule.get(alert["rule"], 0) + 1
    return jsonify({
        "alerts_total": len(dashboard.alerts),
        "by_level": by_level,
        "by_rule": by_rule,
        "commands_evaluated": len(dashboard.assessments),
        "telemetry_frames": dashboard._telemetry_count,
        "guard": dashboard.status,
    })


@app.get("/api/rules")
def api_rules():
    return jsonify({"rules": rule_catalogue(), "thresholds": thresholds()})


@app.get("/api/scenarios")
def api_scenarios():
    return jsonify({"scenarios": catalogue(), "running": dashboard.runner.current})


@app.post("/api/scenario/<scenario_id>")
def api_run_scenario(scenario_id: str):
    result = dashboard.runner.start(scenario_id)
    return jsonify(result), (200 if result.get("ok") else 409)


@app.post("/api/scenario/stop")
def api_stop_scenario():
    dashboard.runner.stop()
    return jsonify({"ok": True})


@app.post("/api/command")
def api_command():
    """Operator console. The guard observes this exactly like any other command."""
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    if not action:
        return jsonify({"ok": False, "error": "action required"}), 400
    command = Command(action=action, source=body.get("source", "operator-hmi"),
                      value=body.get("value"))
    dashboard.bus.publish(config.TOPIC_COMMAND, command.to_dict())
    return jsonify({"ok": True, "command": command.to_dict()})


@app.post("/api/reset")
def api_reset():
    dashboard.runner.stop()
    dashboard.runner.reset_plant()
    dashboard.alerts.clear()
    dashboard.events.clear()
    dashboard.assessments.clear()
    dashboard.store.clear()
    dashboard.status = {"status": "NORMAL", "level": "NORMAL", "risk_score": 0,
                        "headline": "No unsafe command detected", "telemetry_fresh": True}
    dashboard.hub.broadcast("status", dashboard.status)
    dashboard.hub.broadcast("reset", {"ts": now_ms()})
    return jsonify({"ok": True})


@app.get("/api/export/alerts.csv")
def api_export():
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "level", "score", "rule", "summary", "equipment",
                     "context", "command", "source", "why", "recommendation"])
    for alert in reversed(dashboard.alerts):
        command = alert.get("command") or {}
        writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert["ts"] / 1000)),
            alert["level"], alert["score"], alert["rule"], alert["summary"],
            alert.get("equipment", ""), alert.get("context", ""),
            command.get("action", ""), command.get("source", ""),
            alert.get("why", ""), alert.get("recommendation", ""),
        ])
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sentinel-alerts.csv"})


@app.get("/api/stream")
def api_stream():
    def generate():
        q = dashboard.hub.subscribe()
        try:
            snapshot = json.dumps({"type": "snapshot", "data": {
                "state": dashboard.state,
                "status": dashboard.status,
                "alerts": list(dashboard.alerts)[:40],
                "events": list(dashboard.events)[:60],
                "assessments": list(dashboard.assessments)[:40],
                "trend": list(dashboard.trend),
                "scenarios": catalogue(),
                "rules": rule_catalogue(),
                "thresholds": thresholds(),
            }})
            yield f"data: {snapshot}\n\n"
            while True:
                try:
                    message = q.get(timeout=15)
                    yield f"data: {message}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            dashboard.hub.unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(name)-16s %(levelname)-7s %(message)s")
    dashboard.start()
    log.info("dashboard on http://localhost:%s", config.API_PORT)
    app.run(host=config.API_HOST, port=config.API_PORT, threaded=True,
            debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
