"""Human-readable catalogue of the detection rules, for the dashboard and docs."""
from __future__ import annotations

from .. import config
from .risk import RULE_NARRATIVE

W = config.WEIGHTS

RULES = [
    {"id": "SEQ-001", "layer": "State", "trigger": "outlet_close while the pump is running",
     "weights": [("Pump running", W["PUMP_RUNNING"]), ("Closing discharge path", W["CLOSING_DISCHARGE"]),
                 ("Flow active", W["FLOW_ACTIVE"]), ("Not in maintenance", W["NOT_MAINTENANCE"]),
                 ("Pump stop already commanded", W["PUMP_STOPPING"])]},
    {"id": "STATE-002", "layer": "State", "trigger": "pump_start while the outlet valve is closed",
     "weights": [("Dead-head start", W["DEADHEAD_START"]), ("Not in maintenance", W["NOT_MAINTENANCE"])]},
    {"id": "STATE-003", "layer": "State", "trigger": "pump_start below minimum suction level",
     "weights": [("Suction starved", W["SUCTION_STARVED"]), ("Dry run", W["DRY_RUN"])]},
    {"id": "STATE-004", "layer": "State", "trigger": "inlet_close while the pump draws the tank toward the low limit",
     "weights": [("Suction starved", W["SUCTION_STARVED"])]},
    {"id": "ROC-001", "layer": "Rate of change",
     "trigger": f"setpoint step beyond ±{config.SETPOINT_NORMAL_DELTA:.0f} % / {config.SETPOINT_LARGE_DELTA:.0f} %",
     "weights": [("Large step", W["SETPOINT_LARGE"]), ("Extreme step", W["SETPOINT_EXTREME"])]},
    {"id": "ROC-002", "layer": "Rate of change",
     "trigger": f"≥ {config.DRIFT_MIN_STEPS} small setpoint steps whose net change over {config.DRIFT_WINDOW_S/60:.0f} min "
                f"exceeds {config.SETPOINT_LARGE_DELTA:.0f} %",
     "weights": [("Cumulative drift", W["SETPOINT_DRIFT"]), ("Leaves band soon", W["DRIFT_TRAJECTORY"])]},
    {"id": "ENV-001", "layer": "Envelope",
     "trigger": f"setpoint outside {config.SETPOINT_MIN:.0f}–{config.SETPOINT_MAX:.0f} %",
     "weights": [("Out of range", W["SETPOINT_OUT_OF_RANGE"])]},
    {"id": "ENV-002", "layer": "Envelope",
     "trigger": f"load-adding command with pressure above {config.PRESSURE_WARN_BAR:.0f} / {config.PRESSURE_MAX_BAR:.0f} bar",
     "weights": [("Approaching limit", W["ENVELOPE_APPROACH"]), ("Limit breached", W["ENVELOPE_BREACH"])]},
    {"id": "SEQ-002", "layer": "Timing",
     "trigger": f"≥ {config.RAPID_COMMAND_COUNT} actuator commands in {config.RAPID_WINDOW_S:.0f} s, "
                f"or ≥ {config.BURST_COMMAND_COUNT} in {config.BURST_WINDOW_S:.0f} s",
     "weights": [("Rapid sequence", W["RAPID_SEQUENCE"]), ("Burst", W["BURST_SEQUENCE"])]},
    {"id": "SEQ-003", "layer": "Sequence", "trigger": "same actuator driven both ways inside the window",
     "weights": [("Flapping", W["FLAPPING"])]},
    {"id": "SEQ-004", "layer": "Sequence", "trigger": "known unsafe pattern, e.g. pump_start → outlet_close",
     "weights": [("Unsafe pattern", W["UNSAFE_PATTERN"])]},
    {"id": "CMD-001", "layer": "Integrity",
     "trigger": f"command timestamp older than {config.CMD_STALE_S:.0f} s, or a message id already processed",
     "weights": [("Stale timestamp", W["COMMAND_STALE"]), ("Duplicate id", W["COMMAND_DUPLICATE"])]},
    {"id": "TEL-001", "layer": "Integrity", "trigger": f"newest telemetry older than {config.TELEMETRY_STALE_S:.0f} s",
     "weights": [("Stale", W["TELEMETRY_STALE"])]},
    {"id": "TEL-002", "layer": "Integrity",
     "trigger": f"sequence number repeated ≥ {config.REPLAY_REPEAT_COUNT} times",
     "weights": [("Replay", W["TELEMETRY_REPLAY"])]},
    {"id": "TEL-003", "layer": "Integrity", "trigger": "sequence went backwards with an older timestamp",
     "weights": [("Regression", W["TELEMETRY_REGRESSION"])]},
    {"id": "PHY-001", "layer": "Physics",
     "trigger": f"flow residual > {config.PHYSICS_RESIDUAL_LPM:.0f} L/min for {config.PHYSICS_SETTLE_S:.0f} s",
     "weights": [("Physics mismatch", W["PHYSICS_MISMATCH"])]},
    {"id": "SRC-001", "layer": "Context", "trigger": "unrecognised source, or maintenance host outside maintenance",
     "weights": [("Untrusted source", W["UNTRUSTED_SOURCE"])]},
    {"id": "CTX-001", "layer": "Context", "trigger": "plant in MAINTENANCE and command is an isolation/restoration step",
     "weights": [("Maintenance context", W["MAINTENANCE_CONTEXT"])]},
]


def catalogue() -> list[dict]:
    return [{**r, "title": RULE_NARRATIVE.get(r["id"], {}).get("summary", ""),
             "weights": [{"label": k, "weight": v} for k, v in r["weights"]]} for r in RULES]


POLICY = {
    "when_unsure": ("Sentinel never blocks a command. When it cannot trust the state it is reasoning "
                    "about — stale or replayed telemetry, instruments that disagree with the physics, "
                    "no telemetry at all — it still raises the advisory, marks it LOW or REDUCED "
                    "confidence, says exactly what it could not verify, and asks for the plant to be "
                    "confirmed by other means before anyone acts."),
    "never_blocks": ("Commands in the safe direction (pump stop, valve open, maintenance on) are never "
                     "flagged as unsafe on their own. Blocking a genuine safety action can cause the "
                     "accident it was meant to prevent, so the guard has no write path to the plant."),
    "human_decides": ("Every advisory ends with one recommended verification step. The engineer "
                      "decides; Sentinel records what it saw and why it was concerned."),
}


def thresholds() -> dict:
    return {
        "severity_bands": [{"min": m, "level": l} for m, l in config.SEVERITY_BANDS],
        "alert_min_score": config.ALERT_MIN_SCORE,
        "envelope": {"level_pct": [config.LEVEL_MIN_PCT, config.LEVEL_MAX_PCT],
                     "pressure_bar": [0, config.PRESSURE_MAX_BAR], "pressure_warn_bar": config.PRESSURE_WARN_BAR,
                     "flow_lpm": [0, config.FLOW_MAX_LPM], "setpoint_pct": [config.SETPOINT_MIN, config.SETPOINT_MAX]},
        "telemetry": {"stale_s": config.TELEMETRY_STALE_S, "replay_repeat_count": config.REPLAY_REPEAT_COUNT,
                      "period_s": config.TELEMETRY_PERIOD_S},
        "timing": {"rapid_window_s": config.RAPID_WINDOW_S, "rapid_count": config.RAPID_COMMAND_COUNT,
                   "burst_window_s": config.BURST_WINDOW_S, "burst_count": config.BURST_COMMAND_COUNT},
        "trusted_sources": sorted(config.TRUSTED_SOURCES),
        "maintenance_sources": sorted(config.MAINTENANCE_SOURCES),
        "policy": POLICY,
    }
