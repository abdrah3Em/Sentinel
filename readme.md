# Sentinel — Critical Infrastructure Command Guard

> **Valid commands. Unsafe consequences. Detected in context.**

[![Tests](https://img.shields.io/badge/tests-55%20passed-brightgreen.svg)](tests/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](requirements.txt)
[![Track](https://img.shields.io/badge/Track-Critical%20Infrastructure%20%26%20Energy-orange.svg)](docs/)
[![Architecture](https://img.shields.io/badge/OT%2FICS-Process--Aware-purple.svg)](sentinel/)

**Sentinel** is a process-aware operational technology (OT/ICS) command guard for industrial control systems. It monitors incoming commands and sensor telemetry over industrial protocols (MQTT/Modbus) and answers the fundamental question traditional security tools miss:

> Traditional controls ask: **"Is this command authorized and well-formatted?"**  
> Sentinel asks: **"Does this command make physical sense for the process right now?"**

Sentinel is designed for **Track E (Critical Infrastructure & Energy)**, Challenge **E1 — Catching Unsafe Commands in Your Own Control System**.

---

## 📌 Process Model & Dual-Domain Architecture

To ensure engineering rigor without vaporware, Sentinel employs a **process-agnostic constraint engine** validated across two infrastructure tiers:

1. **Live Reference Implementation (In Code & Live Demo):**  
   * **Process:** Municipal Water Treatment Skid & Pumping Station.  
   * **Assets:** Centrifugal Pump (P-101), Motorized Discharge Valve (V-102), Make-up Inlet Valve (V-101), Suction Tank (T-101), Level/Flow/Pressure Transmitters.  
   * **Physics Modeled:** Dynamic dead-heading (pressure spiking to 6.4 bar shut-off head), suction cavitation, dry-running, hydrostatic pressure, and flow meter lags.  
   * **Full PRD:** [`docs/PRD-v1-water-tank.md`](docs/PRD-v1-water-tank.md).

2. **Phase 2 Expansion Architecture (Formal Specification):**  
   * **Process:** 11 kV Radial Electrical Distribution Feeder.  
   * **Assets:** Tap-changing transformer, feeder circuit breaker, sectionalizer, normally-open tie, and PV plant inverter.  
   * **Physics Modeled:** Closing onto standing faults, illegal source paralleling, and slow tap-changer voltage drift.  
   * **Full Specification:** [`docs/PRD-v2-distribution-grid.md`](docs/PRD-v2-distribution-grid.md).

---

## 🎯 Why This Matters (National Infrastructure Context)

In emerging economies and critical infrastructure utilities:
* **The Cost of Pump Dead-Heading:** An industrial multistage centrifugal pump costs **₦35,000,000–₦50,000,000** to replace and takes months to import. A single protocol-valid command closing a discharge valve while the pump runs destroys mechanical seals and ruptures pipes within seconds.
* **Cascading Grid Collapses:** National transmission grids and distribution networks experience repeated trips due to uncoordinated or spoofed switching commands sent to substation breakers.
* **The OT Real-World Constraint:** **Sentinel never acts on the plant directly.** In safety-critical infrastructure, automated tripping causes spurious blackouts and process shutdowns. Sentinel provides **human-interpretable advisories with forensic proof**, keeping final control with the qualified engineer.

---

## ⚡ Quickstart

### 1. One-Click Launch (Recommended)

Requires Python 3.10+ and Mosquitto (`brew install mosquitto` on macOS or `sudo apt install mosquitto` on Ubuntu):

```bash
# Clone and enter directory
git clone https://github.com/DanonymousCoder/Sentinel.git
cd Sentinel

# Start the full demo stack (broker, plant, guard, web console)
./run.sh
```

Open **`http://localhost:8080`** in your browser. Press `Ctrl-C` to stop.

### 2. Docker Compose

```bash
docker compose up --build
```

### 3. Automated Test Suite

```bash
# Run the 55 pure unit and acceptance tests (runs in < 0.5s)
python3 -m pytest tests -v
```

---

## 🛡️ The 7-Layer Detection Engine

Sentinel evaluates every command across seven deterministic constraint layers:

| Layer | Code | Evaluates | Example Violation |
|---|---|---|---|
| **1. State** | `SEQ-001` / `STATE-002` | Physical actuator conflict | `outlet_close` while pump is delivering flow (dead-head) |
| **2. Sequence** | `SEQ-004` | Multi-step command order | Starting pump and closing discharge within 5 seconds |
| **3. Timing** | `SEQ-002` / `SEQ-003` | Rapid actuator flapping | 5 commands in 4 seconds (actuator fatigue / botnet) |
| **4. Rate of Change** | `ROC-001` / `ROC-002` | Setpoint step and slow drift | Setpoint jumped by 35% or walked +4% per step out of envelope |
| **5. Operating Envelope** | `ENV-001` / `ENV-002` | Physical boundary exceedance | Requesting commands when line pressure > 5.0 bar |
| **6. Operational Context** | `CTX-001` / `SRC-001` | Maintenance vs Auto mode | Suppressing alerts for planned maintenance; flagging untrusted laptop |
| **7. Telemetry Integrity** | `TEL-001` / `TEL-002` | Sensor staleness & replay | Sequence numbers freezing (`seq 41 x 12`) or age > 3.0s |

### Explainable Risk Scoring
Risk is computed as:
$$\text{Risk Score} = \min\left(100, \max\left(0, \sum \text{Finding Weights}\right)\right)$$

* **0–29:** LOW (Logged as assessment)
* **30–59:** MEDIUM
* **60–79:** HIGH
* **80–100:** CRITICAL

Every alert clearly shows its exact point contributions (e.g. `+35 Closing discharge`, `+25 Flow active`, `-20 Maintenance mode`).

---

## 📱 Field Engineer Mobile Alerts (Frictionless Dispatch)

Critical infrastructure engineers are often on-call or inspecting field equipment rather than watching a desk HMI. Sentinel includes an asynchronous **Alert Dispatcher** ([`sentinel/guard/dispatcher.py`](sentinel/guard/dispatcher.py)):

* **Telegram Bot Integration:** Set `SENTINEL_TELEGRAM_BOT_TOKEN` and `SENTINEL_TELEGRAM_CHAT_ID`.
* **Webhook Integration:** Set `SENTINEL_WEBHOOK_URL` (supports Slack, Discord, Microsoft Teams, or custom SMS gateways).
* **Live Format:**
  ```text
  🚨 SENTINEL CRITICAL ADVISORY
  Rule: SEQ-001 (Score: 85/100)
  Summary: Outlet valve close conflicts with running pump
  Equipment: Outlet valve V-102 / Pump P-101
  Command: outlet_close (from maintenance-laptop)
  Why: Command closes the only discharge path while the pump is energised. Flow active at 89 L/min.
  Action: Inhibit valve closure or issue pump_stop prior to line isolation.
  Confidence: HIGH — Sentinel advises only, human decides.
  ```

---

## 🎬 4-Minute Hackathon Demo Script

Run scenarios directly from the **Scenarios** tab on the web UI or via the CLI:

```bash
python3 -m sentinel.attacks.run unsafe_valve
```

| Scenario | Attack Type | What Happens on Screen | Verdict |
|---|---|---|---|
| **1. Unsafe Valve (Flagship)** | Wrong-moment command | Plant is pumping normally. Valid `outlet_close` arrives from engineering laptop. | **CRITICAL · SEQ-001** (Score: 85). Posture goes red; pressure climbs to 6.3 bar. |
| **2. Setpoint Manipulation** | Range violation | Attacker jumps level setpoint from 60% to 95% in a single step. | **HIGH · ROC-001 + ENV-001** |
| **3. Slow Setpoint Drift** | Stealth trajectory | Attacker nudges setpoint +4% at a time. Each step passes basic checks. | **MEDIUM → HIGH · ROC-002** (Projects time to limit). |
| **4. Dead-Head Pump Start** | State mismatch | Outlet is closed; unexpected `pump_start` command arrives. | **HIGH · STATE-002** |
| **5. Rapid Cycling** | Actuator flapping | 5 commands sent in 4 seconds. | **MEDIUM · SEQ-002 / SEQ-003** |
| **6. Telemetry Replay** | Sensor spoofing | Attacker freezes telemetry frame while tank drains; sends old command. | **HIGH · TEL-002 & CMD-001** (Confidence marked LOW). |
| **7. Legitimate Maintenance** | Context awareness | Engineer puts plant in `MAINTENANCE` and sends the exact same valve close. | **NORMAL / LOW · CTX-001** (Recognizes planned isolation). |

---

## 🏗️ Repository Structure

```
Sentinel/
├── run.sh                       # Self-healing, one-click demo startup
├── requirements.txt             # Core dependencies (paho-mqtt, flask, pytest)
├── Dockerfile                   # Containerized runtime
├── docker-compose.yml           # Broker + Plant + Guard + Dashboard stack
├── mosquitto/                   # MQTT broker configuration
│   └── mosquitto.conf
├── sentinel/
│   ├── bus.py                   # Thread-safe MQTT client wrapper
│   ├── config.py                # Auditable thresholds, weights, and ports
│   ├── models.py                # Telemetry, Command, Finding, and Alert dataclasses
│   ├── store.py                 # SQLite WAL event & telemetry history
│   ├── plant/
│   │   ├── simulator.py         # Deterministic physical process simulation
│   │   └── run.py               # Plant telemetry loop & command subscriber
│   ├── guard/
│   │   ├── rules.py             # 7-layer pure detection functions
│   │   ├── risk.py              # Additive risk scoring & narrative generator
│   │   ├── state.py             # Digital twin process mirror & telemetry integrity
│   │   ├── dispatcher.py        # Asynchronous Telegram/Webhook mobile alerts
│   │   ├── engine.py            # Command evaluation & process anomaly engine
│   │   └── run.py               # Guard daemon service
│   ├── attacks/
│   │   ├── scenarios.py         # 10 deterministic attack & operational scripts
│   │   └── run.py               # CLI scenario runner
│   └── api/
│       ├── app.py               # Flask REST API + Server-Sent Events (SSE)
│       └── static/              # Zero-build HMI (HTML5, SVG mimic, CSS, Vanilla JS)
├── tests/
│   ├── test_acceptance.py       # PRD criteria validation tests
│   ├── test_detection.py        # Rule-by-rule detection tests
│   ├── test_simulator.py        # Physical equation & event tests
│   └── test_integration.py      # End-to-end MQTT latency tests
└── docs/
    ├── PRD-v1-water-tank.md     # Reference implementation PRD
    ├── PRD-v2-distribution-grid.md # Phase 2 Grid formal specification
    └── img/                     # UI screenshots
```

---

## 📜 License
Developed for the National Information Security & Critical Infrastructure Hackathon. Open source under the MIT License.
