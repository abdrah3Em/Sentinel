#!/usr/bin/env bash
# Start the whole Sentinel demo: broker, plant simulator, command guard, dashboard.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
LOGS=logs; mkdir -p "$LOGS" data
PIDS=()

cleanup() {
  echo
  echo "stopping sentinel..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 1. MQTT broker -----------------------------------------------------------
if (exec 3<>/dev/tcp/127.0.0.1/1883) 2>/dev/null; then
  echo "  broker      already listening on 1883"
else
  echo "  broker      starting mosquitto on 1883"
  mosquitto -c mosquitto/mosquitto.conf >"$LOGS/mosquitto.log" 2>&1 &
  PIDS+=($!)
  sleep 1
fi

# 2. Services --------------------------------------------------------------
echo "  plant       process simulator"
$PY -m sentinel.plant.run >"$LOGS/plant.log" 2>&1 & PIDS+=($!)
sleep 0.5
echo "  guard       command guard"
$PY -m sentinel.guard.run >"$LOGS/guard.log" 2>&1 & PIDS+=($!)
sleep 0.5
echo "  dashboard   http://localhost:${SENTINEL_API_PORT:-8080}"
$PY -m sentinel.api.app >"$LOGS/api.log" 2>&1 & PIDS+=($!)

echo
echo "SENTINEL is running.  Open http://localhost:${SENTINEL_API_PORT:-8080}"
echo "Scenario CLI:  python3 -m sentinel.attacks.run --list"
echo "Logs in ./logs   ·   Ctrl-C to stop"
wait
