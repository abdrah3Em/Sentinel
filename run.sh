#!/usr/bin/env bash
# Start the whole Sentinel demo: broker, plant simulator, command guard, dashboard.
set -euo pipefail
cd "$(dirname "$0")"

LOGS="logs"
mkdir -p "$LOGS" data
PIDS=()

cleanup() {
  echo
  echo "Stopping Sentinel..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "=================================================="
echo " Sentinel — Critical Infrastructure Command Guard "
echo "=================================================="

# 0. Python Environment Setup ----------------------------------------------
if [ -d ".venv" ]; then
  PY=".venv/bin/python"
  PIP=".venv/bin/pip"
elif [ -n "${VIRTUAL_ENV:-}" ]; then
  PY="python"
  PIP="pip"
else
  # Check if python3 is available
  if command -v python3 >/dev/null 2>&1; then
    BASE_PY="python3"
  elif command -v python >/dev/null 2>&1; then
    BASE_PY="python"
  else
    echo "Error: Python 3 is required but not found." >&2
    exit 1
  fi

  # Auto-setup local venv if paho-mqtt or flask are missing in base python
  if ! $BASE_PY -c "import paho.mqtt, flask" 2>/dev/null; then
    echo "  python      creating local virtualenv in .venv..."
    $BASE_PY -m venv .venv
    PY=".venv/bin/python"
    PIP=".venv/bin/pip"
    echo "  python      installing dependencies from requirements.txt..."
    $PIP install -q -r requirements.txt
  else
    PY="$BASE_PY"
  fi
fi

# 1. MQTT broker -----------------------------------------------------------
check_port_1883() {
  (exec 3<>/dev/tcp/127.0.0.1/1883) 2>/dev/null
}

if check_port_1883; then
  echo "  broker      already listening on 1883"
elif command -v mosquitto >/dev/null 2>&1; then
  echo "  broker      starting mosquitto on 1883"
  mosquitto -c mosquitto/mosquitto.conf >"$LOGS/mosquitto.log" 2>&1 &
  PIDS+=($!)
  # Wait up to 3s for broker to be ready
  for _ in {1..15}; do
    if check_port_1883; then break; fi
    sleep 0.2
  done
elif command -v docker >/dev/null 2>&1; then
  echo "  broker      'mosquitto' binary not found; attempting docker fallback..."
  docker run -d --rm --name sentinel-mosquitto -p 1883:1883 -v "$(pwd)/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf" eclipse-mosquitto:latest >/dev/null 2>&1 || true
  sleep 1
else
  echo
  echo "  [ERROR] No MQTT broker found on port 1883 and 'mosquitto' command is not installed." >&2
  echo "  Please install mosquitto or run with Docker:" >&2
  echo "    macOS:   brew install mosquitto && brew services start mosquitto" >&2
  echo "    Ubuntu:  sudo apt install mosquitto mosquitto-clients" >&2
  echo "    Docker:  docker compose up --build" >&2
  echo
  exit 1
fi

# 2. Services --------------------------------------------------------------
echo "  plant       starting process simulator..."
$PY -m sentinel.plant.run >"$LOGS/plant.log" 2>&1 &
PIDS+=($!)
sleep 0.5

echo "  guard       starting command guard..."
$PY -m sentinel.guard.run >"$LOGS/guard.log" 2>&1 &
PIDS+=($!)
sleep 0.5

API_PORT="${SENTINEL_API_PORT:-8080}"
echo "  dashboard   starting web console on port $API_PORT..."
$PY -m sentinel.api.app >"$LOGS/api.log" 2>&1 &
PIDS+=($!)

echo
echo "✔ SENTINEL is running: http://localhost:$API_PORT"
echo "  Attack CLI:  $PY -m sentinel.attacks.run --list"
echo "  Logs in:     ./$LOGS/"
echo "  Press Ctrl-C to stop."
echo
wait
