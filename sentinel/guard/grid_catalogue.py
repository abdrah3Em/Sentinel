"""Human-readable catalogue of the feeder detection rules, for the dashboard and docs."""
from __future__ import annotations

from .. import config

W = config.WEIGHTS

RULES = [
    {"id": "STATE-001", "layer": "State", "trigger": "cb_close while a fault is present or protection is tripped",
     "weights": [("Fault present", W["FAULT_PRESENT"]), ("Protection tripped", W["PROTECTION_TRIPPED"]),
                 ("Critical load downstream", W["CRITICAL_LOAD"]), ("Not in a program", W["NOT_IN_PROGRAM"])]},
    {"id": "STATE-002", "layer": "State", "trigger": "cb_open with load on the feeder and TS-201 open",
     "weights": [("Loss of supply", W["LOSS_OF_SUPPLY"]), ("Breaker carrying load", W["BREAKER_LOADED"]),
                 ("Critical load", W["CRITICAL_LOAD"]), ("Not in a program", W["NOT_IN_PROGRAM"]),
                 ("Alternate path", W["ALTERNATE_PATH"])]},
    {"id": "STATE-003", "layer": "State", "trigger": "a close that leaves CB-101, SW-102 and TS-201 all closed",
     "weights": [("Uncontrolled parallel", W["UNCONTROLLED_PARALLEL"]), ("Not in a program", W["NOT_IN_PROGRAM"])]},
    {"id": "STATE-004", "layer": "State", "trigger": "a close that energises a section under permit-to-work",
     "weights": [("Energise under permit", W["PTW_ENERGISE"]), ("Crew on conductors", W["PTW_CREW"])]},
    {"id": "STATE-005", "layer": "State", "trigger": "sw_open while B2/B3 are fed only through SW-102",
     "weights": [("Loss of supply", W["LOSS_OF_SUPPLY"]), ("Critical load", W["CRITICAL_LOAD"]),
                 ("Not in a program", W["NOT_IN_PROGRAM"])]},
    {"id": "STATE-006", "layer": "State", "trigger": "manual tap step while the busbar is outside statutory limits",
     "weights": [("Tap at limit", W["TAP_AT_LIMIT"])]},
    {"id": "STATE-007", "layer": "State",
     "trigger": f"pv_curtail ≥ 90 % while the feeder carries more than {config.GRID_I_WARN_A:.0f} A",
     "weights": [("Curtail under stress", W["CURTAIL_UNDER_STRESS"])]},
    {"id": "ROC-001", "layer": "Rate of change",
     "trigger": f"avc_target step beyond ±{config.GRID_AVC_NORMAL_DELTA_KV:.2f} / {config.GRID_AVC_LARGE_DELTA_KV:.1f} kV, "
                f"or tap_set beyond {config.GRID_TAP_NORMAL_DELTA} / {config.GRID_TAP_LARGE_DELTA} steps",
     "weights": [("Large step", W["SETPOINT_LARGE"]), ("Extreme step", W["SETPOINT_EXTREME"])]},
    {"id": "ROC-002", "layer": "Rate of change",
     "trigger": f"≥ {config.DRIFT_MIN_STEPS} small avc_target steps whose net change over "
                f"{config.DRIFT_WINDOW_S/60:.0f} min exceeds {config.GRID_DRIFT_NET_KV:.1f} kV",
     "weights": [("Cumulative drift", W["SETPOINT_DRIFT"]), ("Limit reached soon", W["DRIFT_TRAJECTORY"])]},
    {"id": "ENV-001", "layer": "Envelope",
     "trigger": f"target outside {config.GRID_V_MIN_KV:.2f}–{config.GRID_V_MAX_KV:.2f} kV",
     "weights": [("Out of range", W["SETPOINT_OUT_OF_RANGE"])]},
    {"id": "ENV-002", "layer": "Envelope",
     "trigger": f"close or voltage-raising command with the busbar above {config.GRID_V_WARN_HIGH_KV:.1f} kV "
                f"or the feeder above {config.GRID_I_WARN_A:.0f} A",
     "weights": [("Approaching limit", W["ENVELOPE_APPROACH"]), ("Limit breached", W["ENVELOPE_BREACH"])]},
    {"id": "SEQ-002", "layer": "Timing",
     "trigger": f"≥ {config.RAPID_COMMAND_COUNT} switching commands in {config.RAPID_WINDOW_S:.0f} s, "
                f"or ≥ {config.BURST_COMMAND_COUNT} in {config.BURST_WINDOW_S:.0f} s",
     "weights": [("Rapid sequence", W["RAPID_SEQUENCE"]), ("Burst", W["BURST_SEQUENCE"])]},
    {"id": "SEQ-003", "layer": "Sequence", "trigger": "same device opened and closed inside the window",
     "weights": [("Breaker pumping", W["BREAKER_PUMPING"])]},
    {"id": "SEQ-004", "layer": "Sequence",
     "trigger": "known unsafe pattern, e.g. protection_reset → cb_close with the fault present",
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
     "trigger": f"busbar voltage disagrees with tap and source by > {config.GRID_PHYSICS_RESIDUAL_KV:.1f} kV "
                f"for {config.PHYSICS_SETTLE_S:.0f} s, or zero current with load connected",
     "weights": [("Physics mismatch", W["PHYSICS_MISMATCH"])]},
    {"id": "SRC-001", "layer": "Context",
     "trigger": "unrecognised source, or engineering/field host outside a switching program",
     "weights": [("Untrusted source", W["UNTRUSTED_SOURCE"])]},
    {"id": "BASE-001", "layer": "Baseline", "trigger": "cadence or value outside this source's / action's learned history (≥ 20 samples)",
     "weights": [("Baseline deviation", W["BASELINE_DEVIATION"])]},
    {"id": "CTX-001", "layer": "Context", "trigger": "switching program active and the command is a switching step",
     "weights": [("Program context", W["PROGRAM_CONTEXT"])]},
    {"id": "CTX-002", "layer": "Context", "trigger": "cb_close after the fault is cleared and protection reset",
     "weights": [("Fault cleared and reset", W["FAULT_CLEARED"])]},
    {"id": "CTX-003", "layer": "Context", "trigger": "cb_open with TS-201 closed (load transfers to F2)",
     "weights": [("Alternate path", W["ALTERNATE_PATH"])]},
]

POLICY = {
    "when_unsure": ("Stale, replayed or physically inconsistent telemetry never silences an advisory: it is "
                    "raised at LOW or REDUCED confidence with a statement of what could not be verified."),
    "never_blocks": ("Safe-direction commands (opening a faulted breaker, cancelling a permit, AVC to AUTO) are "
                     "never flagged alone, and the guard has no write path to any switchgear or setpoint."),
    "human_decides": "Every advisory ends with one verification step. The control engineer decides.",
    "limits": ("Sentinel sees only what the broker carries: a spoofed source label passes SRC-001 and rewritten "
               "sequence numbers pass TEL-002, so those weigh little; physics and wrong-moment rules judge the "
               "consequence. The feeder model is one radial feeder with algebraic voltage drop, one fault at a "
               "time and one permit at a time."),
}


def thresholds() -> dict:
    return {
        "severity_bands": [{"min": m, "level": l} for m, l in config.SEVERITY_BANDS],
        "alert_min_score": config.ALERT_MIN_SCORE,
        "envelope": {"voltage_kv": [config.GRID_V_MIN_KV, config.GRID_V_MAX_KV],
                     "voltage_warn_kv": [config.GRID_V_WARN_LOW_KV, config.GRID_V_WARN_HIGH_KV],
                     "current_a": [0, config.GRID_I_RATING_A], "current_warn_a": config.GRID_I_WARN_A,
                     "tap": [config.GRID_TAP_MIN, config.GRID_TAP_MAX],
                     "parallel_s": config.GRID_PARALLEL_STANDING_S},
        "telemetry": {"stale_s": config.TELEMETRY_STALE_S, "replay_repeat_count": config.REPLAY_REPEAT_COUNT,
                      "period_s": config.TELEMETRY_PERIOD_S},
        "timing": {"rapid_window_s": config.RAPID_WINDOW_S, "rapid_count": config.RAPID_COMMAND_COUNT,
                   "burst_window_s": config.BURST_WINDOW_S, "burst_count": config.BURST_COMMAND_COUNT},
        "trusted_sources": sorted(config.GRID_TRUSTED_SOURCES),
        "maintenance_sources": sorted(config.GRID_PROGRAM_SOURCES),
        "policy": POLICY,
    }
