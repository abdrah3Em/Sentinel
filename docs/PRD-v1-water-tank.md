# Product Requirements Document — Critical Infrastructure Command Guard

**Project:** E1 — Catching Unsafe Commands in Your Own Control System
**Track:** Critical Infrastructure & Energy
**Product:** Process-Aware OT/ICS Cybersecurity Command Guard
**Document Type:** Full Hackathon PRD
**Target:** Functional prototype + live demonstration
**Primary environment:** Simulated industrial control system
**Recommended plant model:** Water treatment / tank pumping system

---

# 1. Executive Summary

## 1.1 Product vision

**Critical Infrastructure Command Guard** is a process-aware cybersecurity system designed to detect **valid but unsafe commands** issued to an industrial control system.

Traditional cybersecurity controls often ask:

> "Is this command authorized and correctly formatted?"

Our system asks a more important OT question:

> **"Does this command make sense for the physical process right now?"**

The system continuously observes:

* Industrial control commands
* Sensor telemetry
* Equipment states
* Command sequences
* Timing
* Operating limits
* Maintenance status
* Telemetry freshness

It then evaluates whether a command is consistent with the current physical process.

The system provides an **engineer-facing advisory**, explaining:

1. What happened
2. What equipment is affected
3. Why the command is suspicious
4. How severe the situation is
5. What the engineer should verify

Importantly, the system **does not automatically shut down the process**.

The final decision remains with the human engineer, directly matching the challenge requirement.

---

# 2. Challenge Understanding

The challenge describes a control system in which commands such as:

* start pump
* stop pump
* open valve
* close valve
* modify setpoints

may be perfectly valid from a protocol perspective while being dangerous because of their relationship to the physical process.

For example:

### Scenario

A pump is running.

Water is flowing.

An attacker sends:

```text
CLOSE_OUTLET_VALVE
```

The command may be:

* correctly formatted
* sent through the correct protocol
* apparently authorized
* accepted by the controller

Yet physically, the command could cause the pump to operate against a closed discharge path, creating an unsafe pressure condition.

A traditional IT-focused IDS might not detect this.

Our system does.

---

# 3. Problem Statement

Industrial control systems have a fundamental characteristic that distinguishes them from conventional IT systems:

> **Cyber events have physical consequences.**

A command that appears harmless at the network level can cause:

* excessive pressure
* loss of flow
* pump damage
* overheating
* tank overflow
* equipment shutdown
* unsafe operating conditions
* cascading process failures

The challenge is therefore not simply detecting malicious packets.

The real problem is:

> **How can we determine whether an otherwise valid command is inconsistent with the current physical state of the system?**

---

# 4. Proposed Solution

We propose a **Process-Aware Command Guard** positioned between conventional cybersecurity monitoring and industrial process monitoring.

The system combines three categories of information.

### Cyber information

```text
Who sent the command?
What command was sent?
When was it sent?
What sequence of commands occurred?
```

### Process information

```text
What is the tank level?
Is the pump running?
Are valves open?
What is the pressure?
What is the flow?
What is the current setpoint?
```

### Operational context

```text
Is the plant in AUTO mode?
Is maintenance active?
Is this a scheduled operation?
Is the telemetry current?
```

The system combines these signals to calculate a **risk assessment**.

---

# 5. Product Goals

## Primary goals

### G1 — Detect valid but unsafe commands

The system must detect commands that are syntactically valid but physically inappropriate.

### G2 — Understand process context

The system must evaluate commands against the current state of the simulated plant.

### G3 — Detect abnormal sequences

The system must identify suspicious command sequences rather than evaluating commands independently.

### G4 — Detect telemetry manipulation

The system should identify stale, replayed, or inconsistent telemetry.

### G5 — Reduce false positives

The system should understand operational context such as maintenance mode.

### G6 — Provide explainable alerts

Every alert should answer:

> What happened?

> Why is it dangerous?

> What should the engineer check?

### G7 — Keep humans in control

The product must not automatically execute emergency actions based solely on its own detection.

---

# 6. Non-Goals

For the hackathon prototype, we should **not** attempt to:

* connect to a real power plant
* connect to real PLCs
* control real industrial equipment
* deploy malware
* attack third-party infrastructure
* automatically disable industrial equipment
* build a complete enterprise SIEM
* implement every industrial protocol
* replace a safety instrumented system
* claim that the prototype provides certified industrial safety

The demonstration should remain entirely inside a controlled simulation.

---

# 7. Target Users

## 7.1 Control-room engineer

The primary user.

They need to know:

* what command occurred
* whether it is abnormal
* what physical equipment is affected
* whether the process is currently at risk
* what action should be investigated

---

## 7.2 OT cybersecurity analyst

Needs visibility into:

* command patterns
* anomalies
* attack sequences
* telemetry manipulation
* source behavior
* historical events

---

## 7.3 Maintenance engineer

Needs the system to understand that some unusual commands are legitimate during maintenance.

For example:

```text
Maintenance mode
       ↓
Pump STOP
       ↓
Outlet valve CLOSE
```

Normally this might look suspicious.

But in maintenance mode, it can be completely legitimate.

---

## 7.4 Plant operator

Needs a simple interface showing:

* current process state
* warnings
* affected equipment
* severity
* recommended verification

---

# 8. Core Product Concept

The most important concept in the entire project is:

# Command + Context = Risk

A command should never be evaluated in isolation.

For example:

```text
COMMAND:
Close outlet valve
```

By itself:

```text
Risk = unknown
```

But:

```text
Pump = ON
Flow = 85 L/min
Pressure = 2.8 bar
Maintenance = OFF
```

changes the assessment:

```text
Risk = HIGH
```

Whereas:

```text
Pump = OFF
Maintenance = ON
```

could result in:

```text
Risk = LOW
```

This is the central innovation of the prototype.

---

# 9. Proposed System Architecture

```text
                     ┌──────────────────────┐
                     │     Web HMI          │
                     │                      │
                     │ Process State        │
                     │ Alerts               │
                     │ Recommendations      │
                     └──────────┬───────────┘
                                │
                                │
                     ┌──────────▼───────────┐
                     │   Command Guard      │
                     │                      │
                     │ Rule Engine          │
                     │ Risk Scoring         │
                     │ Sequence Analysis    │
                     │ Context Engine       │
                     │ Telemetry Validation │
                     └──────────┬───────────┘
                                │
                         MQTT telemetry
                         MQTT commands
                                │
              ┌─────────────────▼─────────────────┐
              │            MQTT Broker             │
              │             Mosquitto              │
              └──────────────┬────────────────────┘
                             │
             ┌───────────────┴───────────────┐
             │                               │
    ┌────────▼─────────┐           ┌─────────▼────────┐
    │ Process          │           │ Attack           │
    │ Simulator        │           │ Simulator        │
    │                  │           │                  │
    │ Tank             │           │ Valid commands   │
    │ Pump             │           │ Replay           │
    │ Valves           │           │ Manipulation     │
    │ Pressure         │           │ Sequences        │
    │ Flow             │           │                  │
    └──────────────────┘           └──────────────────┘
```

---

# 10. Technology Stack

## Backend

**Python**

Recommended because it allows rapid development of:

* simulation
* MQTT communication
* detection algorithms
* machine learning
* APIs

---

## Industrial communication

**MQTT**

MQTT is suitable for the prototype because it gives us:

* publish/subscribe architecture
* lightweight messaging
* easy simulation
* simple attack scenarios
* straightforward integration

Example topics:

```text
plant/telemetry
plant/command
plant/event
guard/alert
```

---

## MQTT broker

**Mosquitto**

Runs locally or inside Docker.

---

## Web backend

**Flask**

Provides the monitoring dashboard and REST API.

---

## Frontend

For the first prototype:

```text
HTML
CSS
JavaScript
```

For the polished version:

```text
React
```

The frontend should be designed as an industrial monitoring interface rather than a generic SaaS dashboard.

---

## Database

For the MVP:

```text
SQLite
```

For an extended version:

```text
PostgreSQL
```

---

## Containerization

```text
Docker
Docker Compose
```

This allows the entire demonstration environment to be started easily.

---

# 11. Simulated Industrial Process

We should use a **water tank and pump system** because it is intuitive for judges.

The simulated system contains:

### Tank

Parameters:

```text
Level
Pressure
```

### Pump

States:

```text
ON
OFF
```

### Inlet valve

States:

```text
OPEN
CLOSED
```

### Outlet valve

States:

```text
OPEN
CLOSED
```

### Controller

Contains:

```text
Operating mode
Setpoint
Maintenance state
```

---

# 12. Process Variables

The simulator should produce telemetry approximately every 500 ms.

Example:

```json
{
  "ts": 1756980000000,
  "seq": 1045,
  "tank_level": 64.3,
  "pressure": 2.82,
  "flow": 84.6,
  "pump": true,
  "inlet_valve": true,
  "outlet_valve": true,
  "setpoint": 60,
  "mode": "AUTO",
  "maintenance": false
}
```

---

# 13. Command Model

Commands should use a standard structure.

Example:

```json
{
  "id": "8b7e...",
  "ts": 1756980000000,
  "source": "operator-hmi",
  "action": "pump_start"
}
```

Valve command:

```json
{
  "id": "9d13...",
  "ts": 1756980000000,
  "source": "operator-hmi",
  "action": "outlet_close"
}
```

Setpoint command:

```json
{
  "id": "af32...",
  "ts": 1756980000000,
  "source": "operator-hmi",
  "action": "setpoint",
  "value": 95
}
```

---

# 14. Command Types

The initial system should support:

| Command           | Description               |
| ----------------- | ------------------------- |
| `pump_start`      | Start pump                |
| `pump_stop`       | Stop pump                 |
| `outlet_open`     | Open outlet               |
| `outlet_close`    | Close outlet              |
| `inlet_open`      | Open inlet                |
| `inlet_close`     | Close inlet               |
| `setpoint`        | Change operating setpoint |
| `maintenance_on`  | Enter maintenance         |
| `maintenance_off` | Exit maintenance          |

---

# 15. Detection Engine

This is the heart of the project.

The detection engine should not be a single rule.

It should contain multiple detection layers.

---

# 16. Detection Layer 1 — State-Based Detection

The system compares a command against current equipment states.

## Example

Current state:

```text
Pump: ON
Flow: 85 L/min
Outlet: OPEN
```

Incoming command:

```text
Outlet CLOSE
```

The engine evaluates:

```text
Pump ON
+
Flow > 0
+
Outlet CLOSE
=
Unsafe sequence
```

Result:

```text
HIGH RISK
```

---

# 17. Detection Layer 2 — Command Sequence Analysis

Some commands are not dangerous individually.

The sequence can be dangerous.

Example:

```text
PUMP_START
OUTLET_CLOSE
SETPOINT_95
```

may be much more concerning than any individual command.

The system therefore maintains a recent command window:

```text
Last 5 seconds
Last 30 seconds
Last 5 commands
Last 20 commands
```

---

# 18. Detection Layer 3 — Temporal Anomaly Detection

Attackers may issue commands much faster than normal operators.

Example:

```text
00:00 pump_start
00:01 outlet_close
00:02 outlet_open
00:03 pump_stop
00:04 pump_start
```

The system can identify:

> Four actuator commands within five seconds outside maintenance mode.

This becomes a medium-risk indicator.

---

# 19. Detection Layer 4 — Rate-of-Change Detection

Setpoints should normally change gradually.

Example:

```text
Current setpoint = 60
Requested = 95
```

Change:

```text
+35
```

If normal operator changes are typically:

```text
±5
```

the system raises an alert.

---

# 20. Detection Layer 5 — Operating Envelope

The plant should have safe operating boundaries.

Example:

```text
Tank level:
20% → 90%

Pressure:
0 → 5 bar

Normal flow:
0 → 100 L/min
```

A command that pushes the process toward an abnormal region increases risk.

---

# 21. Detection Layer 6 — Maintenance Context

This is extremely important.

Without context, a security system generates false positives.

Example:

```text
AUTO MODE

Pump STOP
Outlet CLOSE
```

Potentially suspicious.

But:

```text
MAINTENANCE MODE

Pump STOP
Outlet CLOSE
```

Likely legitimate.

Therefore:

```text
Risk(command | process, context)
```

rather than:

```text
Risk(command)
```

---

# 22. Detection Layer 7 — Telemetry Freshness

The system must also ask:

> "Can I trust the process state I'm looking at?"

Suppose the HMI displays:

```text
Pump: OFF
Flow: 0
```

but the telemetry is actually:

```text
30 seconds old
```

An attacker could potentially exploit this discrepancy.

The system should therefore track:

```text
timestamp
sequence number
arrival time
```

---

# 23. Replay Detection

Every telemetry message should contain:

```text
timestamp
sequence number
```

Example:

```text
1001
1002
1003
1004
1005
```

If the detector receives:

```text
1005
1005
1005
1005
```

it can flag:

> Telemetry sequence did not advance.

Similarly:

```text
Telemetry timestamp = 30 seconds old
```

should reduce confidence in the displayed state.

---

# 24. Risk Scoring

Rather than simply saying:

```text
Attack = TRUE
```

the system should calculate a risk score.

Example:

```text
State conflict             +40
Unsafe sequence            +25
Large setpoint change      +15
Telemetry anomaly          +20
Maintenance context       -30
--------------------------------
Total                       70
```

Then:

```text
0–29     LOW
30–59    MEDIUM
60–79    HIGH
80–100   CRITICAL
```

For the MVP, deterministic scoring is preferable to an opaque ML model because it is:

* explainable
* easy to demonstrate
* easy to debug
* easy for judges to understand

---

# 25. Example Risk Calculation

Suppose:

```text
Pump = ON
Flow = 85
Outlet = OPEN
Maintenance = OFF
```

Command:

```text
Outlet CLOSE
```

Possible calculation:

```text
Pump running                 +25
Flow detected                +25
Closing discharge path       +35
Not in maintenance           +10
--------------------------------
Risk                          95
```

Result:

```text
HIGH
```

---

# 26. Alert Structure

Every alert should contain:

```json
{
  "level": "HIGH",
  "rule": "SEQ-001",
  "summary": "Outlet valve close conflicts with running pump",
  "equipment": "Outlet valve / pump",
  "why": "Pump is ON and flow is non-zero...",
  "recommendation": "Verify operator intent...",
  "score": 95
}
```

This is much stronger than:

```text
WARNING: suspicious activity
```

because the engineer can immediately understand the reasoning.

---

# 27. User Interface

The HMI should have four primary areas.

## A. Plant status

Show:

```text
Tank Level
Pressure
Flow
Pump
Inlet Valve
Outlet Valve
Setpoint
Operating Mode
```

---

## B. Risk indicator

Example:

```text
SYSTEM STATUS

NORMAL
```

or:

```text
HIGH-RISK COMMAND DETECTED
```

---

## C. Alert timeline

Example:

```text
09:14:32  HIGH
Outlet valve close conflicts with running pump

09:12:10  MEDIUM
Unusually large setpoint change

09:08:44  LOW
Rapid actuator sequence
```

---

## D. Explainability panel

When an alert is selected:

```text
COMMAND
Outlet CLOSE

SOURCE
maintenance-laptop

CURRENT STATE
Pump       ON
Flow       85 L/min
Pressure   2.8 bar
Mode       AUTO

WHY THIS MATTERS
Closing the outlet while the pump is running
removes the normal discharge path.

RECOMMENDED ACTION
Verify operator intent and sequence the pump
before closing the outlet.

RISK
95 / 100
```

This should be one of the strongest UI elements.

---

# 28. Dashboard Layout

Recommended layout:

```text
┌───────────────────────────────────────────────────────┐
│ CRITICAL INFRASTRUCTURE COMMAND GUARD                 │
│ Process-aware OT Security                             │
├───────────────────────────────────────────────────────┤
│                                                       │
│ SYSTEM STATUS: HIGH RISK                              │
│                                                       │
├──────────┬──────────┬──────────┬─────────────────────┤
│ LEVEL    │ PRESSURE │ FLOW     │ PUMP                │
│ 64.3%    │ 2.8 bar  │ 85 L/min │ ON                  │
├──────────┼──────────┼──────────┼─────────────────────┤
│ OUTLET   │ INLET    │ SETPOINT │ MODE                │
│ OPEN     │ OPEN     │ 60       │ AUTO                │
├─────────────────────┬─────────────────────────────────┤
│ PROCESS             │ ENGINEER ADVISORY              │
│                     │                                 │
│ Tank visualization  │ HIGH                            │
│                     │ Outlet valve close              │
│                     │ conflicts with running pump    │
│                     │                                 │
│                     │ Why: ...                        │
│                     │                                 │
│                     │ Recommended: ...               │
├─────────────────────┴─────────────────────────────────┤
│ EVENT TIMELINE                                        │
└───────────────────────────────────────────────────────┘
```

---

# 29. Process Visualization

A simple animated process diagram would significantly improve the demo.

Example:

```text
                 INLET
                   |
                   v
             ┌───────────┐
             │           │
             │   TANK    │
             │   64%     │
             │           │
             └─────┬─────┘
                   |
                   v
                [PUMP]
                   |
                   |
              [OUTLET]
                   |
                   v
                 FLOW
```

During normal operation:

```text
TANK → PUMP → OUTLET
```

During the attack:

```text
TANK → PUMP → X CLOSED
```

The UI can visually show why the command is dangerous.

---

# 30. Attack Simulation

The attack simulator should **not** focus on real-world exploitation.

Instead, it should generate controlled, deterministic attack scenarios against our simulated environment.

This lets judges see the detection capability without needing dangerous infrastructure.

---

# 31. Attack Scenario 1 — Unsafe Valve Command

### Initial state

```text
Pump = ON
Flow = 85
Outlet = OPEN
Maintenance = OFF
```

### Attacker command

```text
Outlet CLOSE
```

### Expected result

```text
HIGH
SEQ-001
```

Message:

> Outlet valve close conflicts with a running pump.

This should be the flagship demonstration.

---

# 32. Attack Scenario 2 — Setpoint Manipulation

Normal:

```text
Setpoint = 60
```

Attacker:

```text
Setpoint = 95
```

Detector:

```text
Requested change = +35
```

Expected:

```text
MEDIUM/HIGH
```

Reason:

> Unusually large setpoint change outside normal operator behavior.

---

# 33. Attack Scenario 3 — Pump Start With Closed Outlet

State:

```text
Pump = OFF
Outlet = CLOSED
```

Command:

```text
Pump START
```

Detector:

```text
HIGH
```

Reason:

> Pump start requested while the discharge path is closed.

---

# 34. Attack Scenario 4 — Rapid Command Sequence

Commands:

```text
PUMP_START
OUTLET_CLOSE
OUTLET_OPEN
PUMP_STOP
PUMP_START
```

within a few seconds.

Detector:

```text
MEDIUM
```

Reason:

> Abnormally rapid actuator sequence outside maintenance mode.

---

# 35. Attack Scenario 5 — Telemetry Replay

Attacker sends old telemetry:

```text
timestamp = 30 seconds old
sequence = 5
```

repeatedly.

Detector notices:

```text
sequence number not advancing
```

and:

```text
telemetry age > threshold
```

Alert:

> Command decision is based on stale or replay-suspect telemetry.

This is an excellent second-stage demonstration because it shows that the system protects the **integrity of the information used for decisions**, not merely commands.

---

# 36. Legitimate Maintenance Scenario

This is critical for demonstrating intelligence.

Normal mode:

```text
AUTO
```

Commands:

```text
Pump STOP
Outlet CLOSE
```

could generate a warning.

But:

```text
MAINTENANCE
```

followed by:

```text
Pump STOP
Outlet CLOSE
```

should be treated differently.

The detector should say:

```text
Context: MAINTENANCE

Assessment:
Expected maintenance activity
```

This demonstrates that we are not simply building a collection of crude rules.

---

# 37. False Positive Reduction

The system should consider:

```text
Operating mode
Maintenance status
Previous commands
Current equipment state
Operator context
Telemetry freshness
Command frequency
```

before generating a high-confidence alert.

This gives us a strong hackathon narrative:

> **We don't just detect anomalies. We understand why they might be happening.**

---

# 38. REST API

The backend should expose:

### Current state

```http
GET /api/state
```

Response:

```json
{
  "tank_level": 64.3,
  "pressure": 2.8,
  "flow": 85,
  "pump": true,
  "outlet_valve": true,
  "mode": "AUTO"
}
```

---

### Alerts

```http
GET /api/alerts
```

---

### Events

```http
GET /api/events
```

---

### System status

```http
GET /api/status
```

Example:

```json
{
  "status": "HIGH_RISK",
  "risk_score": 95,
  "telemetry_fresh": true
}
```

---

# 39. MQTT Topics

Recommended topic architecture:

```text
plant/
    telemetry
    command
    event
    mode

guard/
    alert
    assessment
    status
```

---

# 40. Data Model

## Telemetry

```text
Telemetry
---------
id
timestamp
sequence
tank_level
pressure
flow
pump_state
inlet_valve
outlet_valve
setpoint
operating_mode
maintenance
```

---

## Commands

```text
Command
-------
id
timestamp
source
action
value
```

---

## Alerts

```text
Alert
-----
id
timestamp
severity
rule
score
summary
equipment
reason
recommendation
command_id
```

---

## Events

```text
Event
-----
id
timestamp
type
source
payload
```

---

# 41. Security Model

Although this is a prototype, we should design the architecture around a few important principles.

## Separation of observation and control

The Command Guard should be primarily an **observer**.

```text
Process
   |
   +---- telemetry ----> Guard
   |
   +---- commands -----> Guard
```

The guard should not directly control the plant.

---

## Human-in-the-loop

The system should produce:

```text
DETECTION
    ↓
EXPLANATION
    ↓
RECOMMENDATION
    ↓
ENGINEER DECISION
```

not:

```text
DETECTION
    ↓
AUTOMATIC SHUTDOWN
```

This aligns directly with the challenge.

---

# 42. MVP Requirements

## Must Have

### M1

Simulated industrial process.

### M2

MQTT communication.

### M3

Command simulator.

### M4

Telemetry simulator.

### M5

State-based detection.

### M6

Sequence detection.

### M7

Setpoint anomaly detection.

### M8

Maintenance context.

### M9

Telemetry freshness detection.

### M10

Risk score.

### M11

Engineer-readable alert.

### M12

Web dashboard.

### M13

At least three attack scenarios.

### M14

At least one legitimate maintenance scenario.

---

# 43. Should Have

If time allows:

### S1

Historical event timeline.

### S2

Risk score graph.

### S3

Process animation.

### S4

Attack replay.

### S5

Command source tracking.

### S6

CSV export.

### S7

Detection statistics.

---

# 44. Could Have

For a more advanced submission:

### C1

Machine learning anomaly detection.

### C2

Digital twin.

### C3

Graph-based process modeling.

### C4

Behavioral baseline.

### C5

Multi-stage attack detection.

### C6

MITRE ATT&CK for ICS mapping.

### C7

Role-based access control.

### C8

Cryptographically signed telemetry.

---

# 45. Machine Learning Extension

I would **not make ML the foundation of the first prototype**.

A deterministic process model is easier to prove.

However, ML can become a second detection layer.

For example, train a model on normal command sequences.

Normal:

```text
PUMP_STOP
OUTLET_CLOSE
```

Abnormal:

```text
OUTLET_CLOSE
PUMP_START
SETPOINT_95
OUTLET_OPEN
PUMP_STOP
```

Possible models:

```text
Isolation Forest
One-Class SVM
Autoencoder
LSTM
```

For the hackathon, **Isolation Forest** or a simple statistical model would be the easiest extension.

---

# 46. Digital Twin Extension

This could make the project significantly stronger.

Instead of simply storing:

```text
Pump = ON
Valve = CLOSED
```

we model how the physical system should behave.

For example:

```text
Pump ON + Valve OPEN
        ↓
Flow increases
        ↓
Tank level changes
        ↓
Pressure remains within expected range
```

If telemetry says:

```text
Pump ON
Valve OPEN
Flow = 0
```

then the physical behavior does not match the expected model.

That becomes another anomaly.

---

# 47. Physics-Based Detection

A future detector could compare:

```text
Expected flow
```

against:

```text
Measured flow
```

and calculate:

```text
Residual = |Expected - Measured|
```

If:

```text
Residual > threshold
```

the system raises an alert.

This is much more interesting from a cybersecurity perspective because the detector is using **physical laws as a security signal**.

---

# 48. Advanced Architecture

The final version could therefore become:

```text
                  COMMAND
                     |
                     v
              ┌──────────────┐
              │ Command      │
              │ Analysis     │
              └──────┬───────┘
                     |
                     v
             ┌───────────────┐
             │ Risk Engine   │
             └───────┬───────┘
                     |
        ┌────────────┼────────────┐
        v            v            v
   State Rules   Sequence     ML Model
        |         Rules          |
        └────────────┼────────────┘
                     |
                     v
             ┌───────────────┐
             │ Digital Twin  │
             │ / Physics     │
             └───────┬───────┘
                     |
                     v
             ENGINEER ALERT
```

---

# 49. Functional Requirements

## FR-001 — Telemetry ingestion

The system shall ingest telemetry from the simulated process.

---

## FR-002 — Command ingestion

The system shall observe commands sent to the simulated process.

---

## FR-003 — State synchronization

The system shall maintain the latest known process state.

---

## FR-004 — Command evaluation

Every consequential command shall be evaluated against the current state.

---

## FR-005 — Sequence evaluation

The system shall maintain a history of recent commands.

---

## FR-006 — Context evaluation

The system shall consider maintenance mode.

---

## FR-007 — Telemetry validation

The system shall evaluate telemetry freshness and sequence continuity.

---

## FR-008 — Risk calculation

The system shall assign a risk score.

---

## FR-009 — Alert generation

The system shall generate alerts when configured thresholds are exceeded.

---

## FR-010 — Explainability

Every HIGH or CRITICAL alert shall contain an explanation and recommendation.

---

## FR-011 — Human approval

The system shall not automatically perform a protective process action.

---

## FR-012 — Dashboard

The system shall display process state and alerts.

---

# 50. Non-Functional Requirements

## Performance

Detection latency should be:

```text
< 1 second
```

for the simulated environment.

---

## Availability

The monitoring dashboard should continue operating if individual attack simulations stop.

---

## Explainability

Every detection should be understandable by a non-cybersecurity engineer.

---

## Safety

No real industrial control system should be required.

---

## Reproducibility

A judge should be able to run:

```bash
docker compose up
```

and reproduce the environment.

---

# 51. Testing Strategy

## Unit testing

Test:

```text
risk calculation
state rules
sequence rules
telemetry freshness
maintenance handling
```

---

## Integration testing

Test:

```text
Simulator
    ↓
MQTT
    ↓
Detector
    ↓
Dashboard
```

---

## Attack testing

Run:

```text
Unsafe valve closure
Unsafe pump start
Setpoint manipulation
Rapid sequence
Telemetry replay
```

---

## False-positive testing

Run legitimate:

```text
maintenance
normal pump operation
normal valve operation
normal setpoint adjustment
```

The detector should remain quiet or produce low-severity notices.

---

# 52. Acceptance Criteria

The MVP is successful if:

### Test 1

Normal plant operation produces no HIGH alert.

### Test 2

Closing the outlet while pump and flow are active produces HIGH risk.

### Test 3

Starting the pump with the outlet closed produces HIGH risk.

### Test 4

A large setpoint jump produces MEDIUM/HIGH risk.

### Test 5

Maintenance mode reduces false positives for legitimate maintenance sequences.

### Test 6

Replayed telemetry is identified as stale or sequence-inconsistent.

### Test 7

The dashboard explains why each major alert occurred.

### Test 8

No automatic shutdown occurs.

---

# 53. Development Roadmap

## Phase 1 — Process simulator

Build:

```text
Tank
Pump
Inlet
Outlet
Pressure
Flow
```

Time:

**2–4 hours**

---

## Phase 2 — MQTT infrastructure

Build:

```text
Mosquitto
Telemetry topic
Command topic
Event topic
```

Time:

**1–2 hours**

---

## Phase 3 — Command Guard

Implement:

```text
State engine
Rule engine
Risk score
Alert engine
```

Time:

**4–6 hours**

---

## Phase 4 — Dashboard

Implement:

```text
Process state
Risk status
Alerts
Recommendations
Timeline
```

Time:

**4–8 hours**

---

## Phase 5 — Attack simulator

Implement:

```text
Unsafe command
Setpoint attack
Sequence attack
Replay attack
```

Time:

**2–4 hours**

---

## Phase 6 — Polish

Add:

```text
Process animation
Charts
Attack replay
Better UI
Demo mode
```

Time:

**4–8 hours**

---

# 54. Recommended Team Structure

If you have 4 people:

## Person 1 — OT/Process Engineer

Responsible for:

* process model
* operating constraints
* unsafe sequences
* engineering interpretation

---

## Person 2 — Cybersecurity Engineer

Responsible for:

* detection engine
* attack scenarios
* sequence analysis
* telemetry integrity

---

## Person 3 — Backend Engineer

Responsible for:

* MQTT
* APIs
* database
* integration

---

## Person 4 — Frontend/Product

Responsible for:

* dashboard
* process visualization
* alert UX
* presentation

---

# 55. What We Should NOT Do

This is important.

Do not spend most of the hackathon building:

```text
Fancy authentication
Complex login system
Huge database
Generic SIEM
Packet sniffer
Network scanner
Traditional port scanner
Generic IDS
```

Those features are not the core of the challenge.

The judges already know cybersecurity.

The differentiator is:

> **Understanding the relationship between a cyber command and a physical process.**

---

# 56. What Makes This Potentially Competitive

The project can be positioned around five ideas.

### 1. Protocol-valid does not mean physically safe

This directly attacks the challenge.

### 2. Context-aware detection

The same command can be:

```text
Safe
```

or:

```text
Dangerous
```

depending on plant state.

### 3. Physics-informed security

The system understands:

```text
Pump
Valve
Flow
Pressure
Tank level
```

rather than treating everything as generic network traffic.

### 4. Explainable detection

The engineer gets:

```text
What happened
Why it matters
What equipment is affected
What to verify
```

### 5. Human-in-the-loop

The system assists engineers rather than pretending to replace them.

---

# 57. The Winning Demo

I would structure the presentation around a single story.

## Step 1 — Everything is normal

Show:

```text
Tank: 64%
Pump: ON
Flow: 85 L/min
Outlet: OPEN
Pressure: 2.8 bar
```

Dashboard:

```text
SYSTEM STATUS
NORMAL
```

---

## Step 2 — Send a completely valid command

Attacker sends:

```text
OUTLET_CLOSE
```

Do not initially tell the judges it is malicious.

The system receives it.

---

## Step 3 — Show the detector reasoning

Immediately:

```text
HIGH RISK
```

Then:

```text
Pump: ON
Flow: 85 L/min
Outlet: OPEN → CLOSE
Maintenance: OFF
```

Then:

> Closing the outlet while the pump is operating removes the normal discharge path and may create an unsafe pressure condition.

This is your "wow" moment.

---

# 58. Second Wow Moment — Context

Now switch to:

```text
MAINTENANCE MODE
```

Send:

```text
PUMP_STOP
OUTLET_CLOSE
```

The system does not scream:

```text
ATTACK!
```

Instead:

```text
MAINTENANCE CONTEXT
Expected sequence
```

That demonstrates intelligence.

---

# 59. Third Wow Moment — Telemetry Manipulation

Replay an old state.

Dashboard:

```text
Telemetry age: 30 sec
Sequence: 5
```

Detector:

```text
HIGH
TELEMETRY INTEGRITY WARNING
```

Message:

> The displayed process state may no longer represent the live plant.

This demonstrates that the system protects the **decision-making process**, not merely commands.

---

# 60. Presentation Narrative

Do not pitch it as:

> "We built an MQTT IDS."

Pitch it as:

> **"We built a security layer that understands what the command means to the machine."**

Then explain:

> "An attacker does not need to send an invalid command. They only need to send a valid command at the wrong time."

Then demonstrate:

```text
VALID COMMAND
      +
WRONG PROCESS STATE
      =
PHYSICAL RISK
```

---

# 61. Suggested Product Name

The working name:

# Command Guard

Full name:

> **Critical Infrastructure Command Guard**

Alternative names:

### ProcessShield

> Process-aware security for industrial control systems.

### OTGuard

> Context-aware protection for operational technology.

### PhysSec

> Physical-process-aware cybersecurity.

### SENTINEL-OT

> Security and Event Network Intelligence Layer for Operational Technology.

For a hackathon, I would use:

# CommandGuard

with the tagline:

> **Valid commands. Unsafe consequences. Detected in context.**

---

# 62. Final Product Definition

At the end of the hackathon, the product should be able to demonstrate this complete chain:

```text
                   INDUSTRIAL PROCESS
                          |
                          v
                   Sensor Telemetry
                          |
                          v
                   MQTT Infrastructure
                          |
              ┌───────────┴───────────┐
              │                       │
          Commands                 Telemetry
              │                       │
              └───────────┬───────────┘
                          v
                  COMMAND GUARD
                          |
          ┌───────────────┼────────────────┐
          │               │                │
          v               v                v
      State Rules     Sequence Rules   Telemetry
                                      Integrity
          │               │                │
          └───────────────┼────────────────┘
                          v
                    RISK ENGINE
                          |
                          v
                 EXPLAINABLE ALERT
                          |
                          v
                  HUMAN ENGINEER
                          |
                          v
                   FINAL DECISION
```

---

# 63. The Core MVP in One Sentence

> **CommandGuard continuously compares control commands against the live physical state, operational context, command history, and telemetry integrity to identify valid commands that could produce unsafe physical consequences, while keeping the final decision with a human engineer.**

That should essentially become the **thesis of the entire project**.

---

# 64. Recommended Priority

If your hackathon time becomes limited, build in this exact order:

| Priority | Component                  | Importance |
| -------- | -------------------------- | ---------: |
| 1        | Process simulator          |   Critical |
| 2        | MQTT communication         |   Critical |
| 3        | State-based detector       |   Critical |
| 4        | Risk scoring               |   Critical |
| 5        | Engineer alert/explanation |   Critical |
| 6        | Dashboard                  |   Critical |
| 7        | Maintenance context        |  Very High |
| 8        | Attack simulator           |  Very High |
| 9        | Telemetry replay detection |       High |
| 10       | Sequence detection         |       High |
| 11       | Process animation          |     Medium |
| 12       | Database/history           |     Medium |
| 13       | ML                         |   Optional |
| 14       | Digital twin               |   Advanced |

**Do not start with ML.** Get the deterministic process-aware detector working first. Once the core demonstration is reliable, ML or a digital-twin layer can be added as an enhancement.

The strongest version of this project is **not the one with the most cybersecurity features**. It is the one where a judge can watch a perfectly legitimate command arrive, see the system understand the current physical state, see exactly why that command is dangerous, and then see the same command become legitimate when the operational context changes.
