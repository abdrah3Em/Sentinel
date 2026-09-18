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

# --------------------------------------------------------------------------
# Simulated process: "grid" (11 kV distribution feeder, PRD rev 2 — the flagship
# console) or "pipeline" (crude oil pipeline pump station — the portability proof
# for the same detector core).  Each process runs as its own plant + guard +
# console; they share one broker under separate topic namespaces.
# --------------------------------------------------------------------------
PROCESS = os.environ.get("SENTINEL_PROCESS", "grid").strip().lower()
CONSOLE_PORTS = {
    "grid": int(os.environ.get("SENTINEL_GRID_PORT", "8080")),
    "pipeline": int(os.environ.get("SENTINEL_PIPELINE_PORT", "8081")),
}

MODBUS_PORTS = {"grid": 5020, "pipeline": 5021}
MODBUS_PORT = int(os.environ.get("SENTINEL_MODBUS_PORT", MODBUS_PORTS.get(PROCESS, 0)))   # 0 disables

TOPIC_NS = os.environ.get("SENTINEL_TOPIC_NS", PROCESS)   # e.g. grid/plant/telemetry, pipeline/plant/telemetry
TOPIC_TELEMETRY = f"{TOPIC_NS}/plant/telemetry"
TOPIC_COMMAND = f"{TOPIC_NS}/plant/command"
TOPIC_EVENT = f"{TOPIC_NS}/plant/event"
TOPIC_MODE = f"{TOPIC_NS}/plant/mode"

TOPIC_ALERT = f"{TOPIC_NS}/guard/alert"
TOPIC_ASSESSMENT = f"{TOPIC_NS}/guard/assessment"
TOPIC_STATUS = f"{TOPIC_NS}/guard/status"

TOPIC_CONTROL = f"{TOPIC_NS}/sentinel/control"   # demo housekeeping (reset), not a plant path
TOPIC_SIM = f"{TOPIC_NS}/plant/sim"              # simulator-only hooks (fault inject, telemetry hold) — never the guard

# --------------------------------------------------------------------------
# API / dashboard
# --------------------------------------------------------------------------
API_HOST = os.environ.get("SENTINEL_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("SENTINEL_API_PORT", CONSOLE_PORTS.get(PROCESS, 8080)))
GUARD_DB_PATH = os.environ.get(
    "SENTINEL_GUARD_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", f"guard-{PROCESS}.db"),
)
GUARD_STATE_MAX_AGE_S = 3600.0        # older persisted state is ignored on start
DB_PATH = os.environ.get(
    "SENTINEL_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", f"sentinel-{PROCESS}.db"),
)

# --------------------------------------------------------------------------
# Notifications / Dispatcher (Field Engineer Alerts)
# --------------------------------------------------------------------------
CONSOLE_TOKEN_PATH = os.environ.get(
    "SENTINEL_CONSOLE_TOKEN_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "console-token"),
)


def _console_token() -> str:
    """The operator token every mutating console endpoint requires.

    Taken from SENTINEL_CONSOLE_TOKEN if set; otherwise generated once on first run,
    stored next to the databases and printed by run.sh and the console log."""
    token = os.environ.get("SENTINEL_CONSOLE_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(CONSOLE_TOKEN_PATH) as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    import secrets
    token = secrets.token_urlsafe(18)
    try:
        os.makedirs(os.path.dirname(CONSOLE_TOKEN_PATH), exist_ok=True)
        fd = os.open(CONSOLE_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
    except OSError:
        pass
    return token


CONSOLE_TOKEN = _console_token()

# Signed command envelopes (sentinel/signing.py): sources that hold a key.  Anything
# else — a Modbus client, an unknown host — is unkeyed and its commands are unsigned.
SIGNING_MASTER_PATH = os.environ.get(
    "SENTINEL_SIGNING_MASTER_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "signing-master"),
)
EXTRA_SIGNING_SOURCES = set(filter(None, os.environ.get("SENTINEL_SIGNING_SOURCES", "itest,gitest").split(",")))
WEBHOOK_URL = os.environ.get("SENTINEL_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.environ.get("SENTINEL_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("SENTINEL_TELEGRAM_CHAT_ID", "")
DISPATCH_MIN_LEVEL = os.environ.get("SENTINEL_DISPATCH_MIN_LEVEL", "HIGH")

# --------------------------------------------------------------------------
# Process simulation — pipeline pump station (flows in m³/h, level in %, bar)
# --------------------------------------------------------------------------
TELEMETRY_PERIOD_S = 0.5          # 2 Hz telemetry, per PRD section 12
SIM_TICK_S = 0.1                  # physics integration step

TANK_CAPACITY_L = 500.0           # tank volume units at 100 % (flow units per minute × 60 = m³/h)
INLET_FLOW_LPM = 120.0            # ESD-301 fully open, tank farm feed
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
# Distribution feeder F1 (PRD revision 2, sections 11-12, 19-20)
# --------------------------------------------------------------------------
GRID_NOMINAL_KV = 11.0
GRID_SOURCE_KV = 33.0
GRID_F2_KV = 11.0                      # alternate feeder, modelled as a strong source
GRID_TAP_STEP_PCT = 1.25
GRID_TAP_MIN, GRID_TAP_MAX = -8, 8
GRID_TAP_STEP_TIME_S = 4.0             # OLTC mechanical step time
GRID_AVC_DEADBAND_PCT = 0.75
GRID_AVC_TARGET_MIN_KV, GRID_AVC_TARGET_MAX_KV = 10.6, 11.4
GRID_MANUAL_OVERRIDE_S = 60.0          # a manual tap command holds the AVC off this long
GRID_V_MIN_KV, GRID_V_MAX_KV = 10.34, 11.66          # statutory ±6 %
GRID_V_WARN_LOW_KV, GRID_V_WARN_HIGH_KV = 10.5, 11.5
GRID_I_RATING_A, GRID_I_WARN_A = 400.0, 360.0
# Feeder F1: BB-101 -S1- B1 -[SW-102]- S2 - B2 -S3- B3 -[TS-201]- B4 -S4- BB-201 (feeder F2), B4 -S5- B5
GRID_SECTIONS = {"S1": (0.40, 0.60), "S2": (0.60, 0.90), "S3": (0.40, 0.60),      # R, X in ohms
                 "S4": (0.50, 0.75), "S5": (0.45, 0.65), "TIE": (0.05, 0.08)}
GRID_LOADS_KW = {"b1": 1800.0, "b2": 2400.0, "b3": 900.0, "b4": 2000.0, "b5": 1200.0}
GRID_CUSTOMERS = {"b1": 1800, "b2": 45, "b3": 1, "b4": 30, "b5": 950}
GRID_SOURCE_Z_OHM = 0.977              # 33/11 kV source impedance seen at the busbar (6.5 kA bolted fault)
GRID_POWER_FLOW_SWEEPS = 4             # forward-backward sweep iterations per step
GRID_CRITICAL_BUS = "b3"               # the hospital
GRID_LOAD_PF = 0.95
GRID_PV_RATED_KW = 2000.0
GRID_FAULT_CURRENT_KA = 6.5
GRID_TRIP_DELAY_S = 0.10               # protection operating time
GRID_FAULT_FLASH_S = 0.15              # fault current flows this long on a close-onto-fault
GRID_PARALLEL_STANDING_S = 60.0
GRID_DIURNAL_PERIOD_S = 600.0          # a "day" of load in ten minutes
GRID_AVC_NORMAL_DELTA_KV = 0.15        # layer 4 — a normal operator trim of the AVC target
GRID_AVC_LARGE_DELTA_KV = 0.40
GRID_TAP_NORMAL_DELTA, GRID_TAP_LARGE_DELTA = 1, 3
GRID_DRIFT_NET_KV = 0.30               # cumulative AVC-target movement that counts as a drift
GRID_PHYSICS_RESIDUAL_KV = 0.30        # PHY-001 — bus voltage disagreeing with tap and source
GRID_LOADED_A = 10.0                   # above this the feeder breaker is "carrying load"
GRID_TRUSTED_SOURCES = {"operator-hmi", "scada-auto", "dms-controller"}
GRID_PROGRAM_SOURCES = {"engineering-laptop", "field-crew"}   # accepted inside a switching program

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
    # --- distribution feeder (PRD rev 2 section 24) ---
    "FAULT_PRESENT": 40,
    "PROTECTION_TRIPPED": 20,
    "CRITICAL_LOAD": 15,
    "LOSS_OF_SUPPLY": 35,
    "BREAKER_LOADED": 20,
    "UNCONTROLLED_PARALLEL": 30,
    "PTW_ENERGISE": 35,
    "PTW_CREW": 25,
    "TAP_AT_LIMIT": 30,
    "CURTAIL_UNDER_STRESS": 25,
    "BREAKER_PUMPING": 15,
    "NOT_IN_PROGRAM": 10,
    "PROGRAM_CONTEXT": -30,
    "FAULT_CLEARED": -25,
    "ALTERNATE_PATH": -25,
    "BASELINE_DEVIATION": 10,
    "SIGNATURE_INVALID": 30,       # claims a keyed source, no or bad signature
    "SEQUENCE_REPLAY": 40,         # valid signature, but sequence/nonce/time say replay
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
