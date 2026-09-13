"""Central configuration for Sentinel — Critical Infrastructure Command Guard.

Everything tunable lives here so the detection behaviour is auditable in one
place.  No magic numbers hidden inside the rule engine.
"""
from __future__ import annotations

import os

# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
MQTT_HOST = os.environ.get("SENTINEL_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("SENTINEL_MQTT_PORT", "1883"))
MQTT_KEEPALIVE = 30

TOPIC_TELEMETRY = "plant/telemetry"
TOPIC_COMMAND = "plant/command"
TOPIC_EVENT = "plant/event"
TOPIC_MODE = "plant/mode"

TOPIC_ALERT = "guard/alert"
TOPIC_ASSESSMENT = "guard/assessment"
TOPIC_STATUS = "guard/status"

TOPIC_CONTROL = "sentinel/control"   # demo housekeeping (reset), not a plant path

# --------------------------------------------------------------------------
# API / dashboard
# --------------------------------------------------------------------------
API_HOST = os.environ.get("SENTINEL_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("SENTINEL_API_PORT", "8080"))
DB_PATH = os.environ.get(
    "SENTINEL_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sentinel.db"),
)

# --------------------------------------------------------------------------
# Notifications / Dispatcher (Field Engineer Alerts)
# --------------------------------------------------------------------------
WEBHOOK_URL = os.environ.get("SENTINEL_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("SENTINEL_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("SENTINEL_TELEGRAM_CHAT_ID", "")
DISPATCH_MIN_LEVEL = os.environ.get("SENTINEL_DISPATCH_MIN_LEVEL", "HIGH")

# --------------------------------------------------------------------------
# Process simulation
# --------------------------------------------------------------------------
TELEMETRY_PERIOD_S = 0.5          # 2 Hz telemetry, per PRD section 12
SIM_TICK_S = 0.1                  # physics integration step

TANK_CAPACITY_L = 500.0           # litres at 100 %
INLET_FLOW_LPM = 120.0            # inlet valve fully open, gravity fed
PUMP_RATED_FLOW_LPM = 95.0        # free-discharge flow at rated speed
PUMP_SHUTOFF_HEAD_BAR = 6.4       # dead-headed pump pressure (physical ceiling)
PUMP_MIN_SUCTION_LEVEL = 8.0      # below this the pump starts to cavitate

STATIC_HEAD_BAR_AT_FULL = 1.1     # tank hydrostatic contribution at 100 %
LINE_PRESSURE_BASE_BAR = 0.4      # residual line pressure, pump off

# --------------------------------------------------------------------------
# Operating envelope (Detection layer 5)
# --------------------------------------------------------------------------
LEVEL_MIN_PCT = 20.0
LEVEL_MAX_PCT = 90.0
PRESSURE_MAX_BAR = 5.0
PRESSURE_WARN_BAR = 4.0
FLOW_MAX_LPM = 100.0
SETPOINT_MIN = 20.0
SETPOINT_MAX = 90.0

DEFAULT_SETPOINT = 60.0
CONTROL_DEADBAND_PCT = 3.0        # AUTO mode hysteresis around the setpoint

FLOW_ACTIVE_LPM = 5.0             # above this the line is considered "flowing"

# --------------------------------------------------------------------------
# Detection thresholds
# --------------------------------------------------------------------------
TELEMETRY_STALE_S = 3.0           # layer 7 — telemetry older than this is stale
TELEMETRY_HARD_STALE_S = 10.0     # unusable for a safety decision
REPLAY_REPEAT_COUNT = 3           # identical sequence numbers before we call replay

RAPID_WINDOW_S = 5.0              # layer 3 — temporal anomaly window
RAPID_COMMAND_COUNT = 4           # actuator commands inside the window
BURST_WINDOW_S = 30.0
BURST_COMMAND_COUNT = 8

SETPOINT_NORMAL_DELTA = 5.0       # layer 4 — typical operator adjustment
SETPOINT_LARGE_DELTA = 15.0       # beyond this it is an anomaly
DRIFT_WINDOW_S = 600.0            # layer 4 — slow-drift detection window
DRIFT_MIN_STEPS = 3               # small steps before we call it a drift
DRIFT_PROJECTION_MIN = 15.0       # minutes-to-band-edge that counts as imminent

CMD_STALE_S = 30.0                # command timestamps older than this are replay/delay

COMMAND_HISTORY = 40              # commands retained for sequence analysis

PHYSICS_RESIDUAL_LPM = 25.0       # digital-twin flow residual before we alert
PHYSICS_SETTLE_S = 4.0            # how long the mismatch must persist first

TRUSTED_SOURCES = {"operator-hmi", "plc-controller", "scada-auto"}
MAINTENANCE_SOURCES = {"maintenance-laptop", "maintenance-hmi"}

# --------------------------------------------------------------------------
# Risk model (Detection layer weights — PRD sections 24 & 25)
# --------------------------------------------------------------------------
WEIGHTS = {
    "PUMP_RUNNING": 25,
    "FLOW_ACTIVE": 25,
    "CLOSING_DISCHARGE": 35,
    "NOT_MAINTENANCE": 10,
    "DEADHEAD_START": 50,
    "SUCTION_STARVED": 30,
    "DRY_RUN": 35,
    "ENVELOPE_BREACH": 25,
    "ENVELOPE_APPROACH": 12,
    "SETPOINT_LARGE": 15,
    "SETPOINT_EXTREME": 25,
    "SETPOINT_OUT_OF_RANGE": 20,
    "SETPOINT_DRIFT": 25,
    "DRIFT_TRAJECTORY": 15,
    "COMMAND_STALE": 30,
    "COMMAND_DUPLICATE": 40,
    "RAPID_SEQUENCE": 25,
    "BURST_SEQUENCE": 12,
    "UNSAFE_PATTERN": 25,
    "FLAPPING": 15,
    "TELEMETRY_STALE": 25,
    "TELEMETRY_REPLAY": 35,
    "TELEMETRY_REGRESSION": 25,
    "PHYSICS_MISMATCH": 25,
    "UNTRUSTED_SOURCE": 10,
    "MAINTENANCE_CONTEXT": -30,
    "MAINTENANCE_EXPECTED": -20,
    "PUMP_STOPPING": -25,
}

SEVERITY_BANDS = [(80, "CRITICAL"), (60, "HIGH"), (30, "MEDIUM"), (0, "LOW")]
ALERT_MIN_SCORE = 15              # below this we log an assessment but no alert

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def severity_for(score: int) -> str:
    """Map a 0-100 risk score onto the PRD severity bands."""
    for threshold, name in SEVERITY_BANDS:
        if score >= threshold:
            return name
    return "LOW"
