# Product Requirements Document — Sentinel: Critical Infrastructure Command Guard

**Project:** E1 — Catching Unsafe Commands in Your Own Control System
**Track:** Critical Infrastructure & Energy
**Product:** Process-Aware OT/ICS Cybersecurity Command Guard
**Document Type:** Full Hackathon PRD — revision 2 (distribution-grid process)
**Target:** Functional prototype + live demonstration
**Primary environment:** Simulated distribution feeder controlled over MQTT
**Process model:** One 11 kV radial feeder with a tap-changing transformer, a feeder breaker, a sectionaliser, a normally-open tie, a PV plant and three load groups (one critical)
**Supersedes:** revision 1 (water tank / pump skid), archived at `docs/PRD-v1-water-tank.md`

---

# 0. Why this revision

Revision 1 built Sentinel around a water tank, a pump and two valves, because the
challenge text offers that as a minimal example. Re-reading the brief, the actual
requirement is *"a working simulated process controlled over Modbus TCP or MQTT"*
— the tank is a suggestion, not a specification.

The track is **Critical Infrastructure & Energy**. A distribution feeder is a
closer fit, and it gives sharper versions of every attack the brief names:

| Brief | Tank version | Grid version |
|---|---|---|
| Inject a command | close the outlet | open a feeder breaker — three load groups, including a hospital, lose supply |
| Valid command at the wrong moment | close the outlet with the pump running | **close a breaker onto a standing fault** — the same `close` an operator issues fifty times a week, issued at the wrong instant |
| Replay old traffic | replay a calm tank level | replay a healthy 50.00 Hz / 11.0 kV picture while the real feeder drifts out of limits or is already dead |
| Slowly drift a setpoint | walk the level setpoint | walk the voltage-control target one tap at a time until the feeder sits outside statutory limits |
| Legitimate maintenance | pump stop, valve close | a **planned switching operation** — open breakers, transfer load through the tie, isolate a section — indistinguishable from an attack at the message level |

Everything that made revision 1 work is retained: MQTT transport, the seven-layer
detection engine, additive explainable risk scoring, telemetry-integrity checks,
confidence statements, the dashboard, the scenario runner and the test
structure. What changes is the physics, the equipment vocabulary, the rules that
encode "what makes sense for this process", and the scenarios.

**Scope discipline.** The simulator gets one feeder, one breaker, one tap
changer, one inverter setpoint, three loads, a sectionaliser and a tie. Nothing
more. Most of the effort goes to the detector, not the power flow.

---

# 1. Executive Summary

## 1.1 Product vision

**Sentinel** is a process-aware cybersecurity system that detects **valid but
unsafe commands** issued to an electrical distribution control system.

Conventional controls ask:

> "Is this command authorised and correctly formatted?"

Sentinel asks the question that matters in operational technology:

> **"Does this command make sense for the network right now?"**

It continuously observes control commands, feeder telemetry, switchgear
positions, protection state, command sequences, timing, statutory limits,
switching-program (maintenance) status and telemetry freshness, and evaluates
whether each command is consistent with the physical state of the network.

The output is an **engineer-facing advisory** that states what happened, which
equipment is affected, why it is dangerous, how severe it is, what to verify —
and how confident Sentinel is in the picture it reasoned from.

Sentinel **never operates the network itself.** It advises. A human decides.

## 1.2 One-sentence definition

> **Sentinel continuously compares control commands against the live electrical
> state of a distribution feeder, its switching context, its command history and
> the integrity of its telemetry, to identify protocol-valid commands that would
> produce unsafe physical consequences — while keeping every decision with a
> human engineer.**

---

# 2. Challenge Understanding

The brief (Track E, challenge E1) says, in summary:

* Systems that open switches and start equipment talk over Modbus and MQTT; the
  protocols check nothing. A correctly formatted command is obeyed.
* The hardest cases are the ones where nothing looks broken: the command is
  valid, the format is correct, the sender looks legitimate, but it is sent at
  the wrong moment or to equipment in a state where it causes harm.
* Assume the attacker can send perfectly valid, well-formatted commands.
* Keeping the plant running matters more than keeping secrets; blocking a genuine
  safety command can cause the accident you were trying to prevent.
* Normal traffic is repetitive, so anything unusual stands out — but legitimate
  maintenance also looks unusual.
* The person reading the alert is an engineer under pressure, not a security
  specialist.
* The system must not shut the process down by itself. It advises; a human
  decides.

**What to build:** a working simulated process over Modbus TCP or MQTT showing
normal running, start-up, shut-down and at least one legitimate maintenance
activity; attack scripts for injection, replay, wrong-moment commands and slow
setpoint drift; a detector tested on that data; and an alert screen that tells
an engineer what happened, to which equipment, why it matters — plus a clear
statement of what the system does when it is unsure.

### The grid reading of the brief

A distribution control room issues breaker closes, tap changes and inverter
setpoints all day. Every one of them is protocol-valid. The dangerous ones are:

* **Closing a breaker onto a fault.** Protection tripped the feeder for a reason.
  A `close` sent before the fault is cleared drives thousands of amps into the
  fault, stresses the breaker, damages cables and joints, and can injure a crew
  working on the section.
* **Opening a healthy breaker.** No fault, no switching program — three load
  groups go dark, one of them a hospital.
* **Paralleling two sources without a plan.** Closing the tie while both feeders
  are live creates a loop, circulating current and protection that no longer
  grades.
* **Walking the voltage target out of limits** one tap at a time, each step
  inside the tap changer's normal range.
* **Freezing the control-room picture** with replayed telemetry so a healthy
  50 Hz, 11 kV feeder is displayed while the real one is drifting or dead.

Each of these is indistinguishable from routine operation at the message level.
That is the problem Sentinel solves.

---

# 3. Problem Statement

Distribution networks have the defining property of operational technology:

> **Cyber events have physical consequences.**

A command that is harmless on the wire can cause loss of supply to critical
customers, fault-current damage to switchgear and cables, statutory voltage
violations, uncontrolled parallels between sources, protection
mis-coordination, and hazards to field crews.

The problem is not detecting malicious packets. It is:

> **How do we determine whether an otherwise valid command is inconsistent with
> the current physical state of the network, its switching context, and what
> the control room can currently trust?**

---

# 4. Proposed Solution

Sentinel sits between conventional network security monitoring and the
distribution management system, combining three kinds of information.

**Cyber information**

```text
Who sent the command?      What command?      When?      In what sequence?
Has this message been seen before?
```

**Electrical information**

```text
Is the breaker closed?     Is a fault present?     Has protection tripped?
What are the bus voltages?     What is the feeder current?
What tap is the transformer on?     How much PV is exporting?
Which load groups are supplied, and from where?
```

**Operational context**

```text
Is a switching program in force?     Is a permit-to-work issued on a section?
Is voltage control in AUTO?     Is the telemetry current and consistent?
```

From these it computes a **risk assessment** for every consequential command and
a **confidence** in the state it reasoned from.

---

# 5. Product Goals

| Goal | Statement |
|---|---|
| **G1** | Detect commands that are protocol-valid but electrically unsafe |
| **G2** | Evaluate every command against the live network state |
| **G3** | Detect abnormal command sequences and timing, not just single commands |
| **G4** | Detect stale, replayed or inconsistent telemetry — and replayed commands |
| **G5** | Recognise planned switching and permit-to-work context to avoid false positives |
| **G6** | Produce explainable advisories: what, which equipment, why, what to verify |
| **G7** | State confidence and what the system does when it is unsure |
| **G8** | Keep humans in control — no automatic protective action, ever |

---

# 6. Non-Goals

For the hackathon prototype Sentinel will **not**:

* connect to a real substation, RTU, relay, inverter or DMS
* implement DNP3, IEC 61850 or IEC 60870-5-104
* solve full AC power flow or model transient stability
* replace protection relays or a safety instrumented system
* operate any switchgear or change any setpoint on its own
* build a SIEM, packet capture or network scanner
* claim certified functional safety

The demonstration stays entirely inside a controlled simulation.

---

# 7. Target Users

**Control-room engineer / dispatcher (primary).** Needs to know: what command
occurred, whether it fits the network state, which equipment and customers are
affected, how severe it is, what to check.

**OT security analyst.** Needs visibility into command patterns, sources,
attack sequences, telemetry manipulation and history.

**Switching / field engineer.** Needs the system to understand that a planned
switching program legitimately involves opening breakers, closing ties and
isolating sections.

**Network operator (management).** Needs a single screen: posture, live
single-line diagram, advisories, recommended verification.

---

# 8. Core Product Concept

# Command + Context = Risk

A command is never evaluated in isolation.

```text
COMMAND:  Close feeder breaker CB-101
```

By itself: risk unknown.

```text
Fault present on section S2            Protection tripped 40 s ago
Fault not cleared                      Permit-to-work on S2: none
Switching program: none                Source: engineering-laptop
```

→ **Risk CRITICAL.** Closing onto an uncleared fault.

```text
Fault cleared, indicator reset          Switching program: SP-0417 active
Section proven dead and isolated        Source: operator-hmi
```

→ **Risk LOW.** Expected restoration step.

Same message. Different network. Different answer.

---

# 9. System Architecture

```text
                     ┌──────────────────────────┐
                     │        Dashboard         │
                     │  Single-line mimic       │
                     │  Advisories · Timeline   │
                     │  Scenarios · Rules       │
                     └────────────┬─────────────┘
                                  │ REST + SSE
                     ┌────────────▼─────────────┐
                     │      Command Guard       │
                     │  State engine            │
                     │  Rule engine (7 layers)  │
                     │  Risk scoring            │
                     │  Telemetry integrity     │
                     │  Confidence / policy     │
                     └────────────┬─────────────┘
                                  │ MQTT (observe only)
                     ┌────────────▼─────────────┐
                     │   Mosquitto MQTT broker  │
                     └──────┬────────────┬──────┘
                            │            │
              ┌─────────────▼───┐   ┌────▼──────────────┐
              │ Feeder simulator│   │ Attack simulator  │
              │ Transformer/OLTC│   │ Injection         │
              │ Breaker, switch │   │ Wrong-moment close│
              │ Tie, PV, loads  │   │ Tap drift         │
              │ Fault model     │   │ Replay            │
              └─────────────────┘   └───────────────────┘
```

The guard has **no write path** to the simulator. It subscribes and publishes
advisories.

---

# 10. Technology Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.10+ | simulator, guard, API, scenarios, tests |
| Transport | **MQTT** (Eclipse Mosquitto) | pub/sub, trivially observable, trivially attackable |
| Web backend | Flask | REST + server-sent events |
| Frontend | HTML / CSS / vanilla JS | no build step; light and dark themes |
| History | SQLite | telemetry, commands, advisories, events |
| Packaging | `run.sh` and Docker Compose | judge runs one command |
| Tests | pytest | unit, acceptance, live-broker integration |

### On the protocol

Transmission SCADA speaks DNP3 and IEC 61850; distribution-edge equipment —
inverters (SunSpec), meters, RTUs, battery systems — speaks Modbus and,
increasingly, MQTT. The brief accepts either Modbus TCP or MQTT. Sentinel uses
MQTT because it makes the observe-only architecture and the attack scenarios
simple and honest. The detection logic is protocol-agnostic: a Modbus mapping is
a transport adapter, not a redesign.

### On the physics

Sentinel uses a **single radial feeder with algebraic voltage drop** (see §12).
This is deliberately crude and stated as such. `pandapower` could replace it
with proper AC power flow on an IEEE test feeder with little effort, but the
detector — not the power flow — is what is being assessed.

---

# 11. Simulated Process — Feeder F1

## 11.1 Single-line diagram

```text
  33 kV grid supply (strong source)
        │
     ┌──┴──┐  T1  33/11 kV, 10 MVA
     │ OLTC│  taps −8 … +8, 1.25 % per step, AVC in AUTO
     └──┬──┘
        │  11 kV busbar BB-101              V_bus
        ■  CB-101   feeder circuit breaker   [protection: overcurrent, trips on fault]
        │
        │  Section S1  (2 km)
        ●  B1  Residential  ~1.8 MW                      Fault indicator FI-1
        │
        ■  SW-102  sectionaliser (load-break switch)
        │
        │  Section S2  (3 km)
        ●  B2  Industrial  ~2.4 MW     ◄── PV plant 2.0 MWp, inverter setpoint    FI-2
        │
        │  Section S3  (2 km)
        ●  B3  Hospital  ~0.9 MW  (CRITICAL load)                                 FI-3
        │
        ■  TS-201  tie switch, normally OPEN
        │
     Feeder F2 (alternate supply, modelled as a strong source at 11.0 kV)
```

## 11.2 Equipment

| Tag | Equipment | States / range |
|---|---|---|
| T1 / OLTC | 33/11 kV transformer with on-load tap changer | tap −8 … +8; 4 s mechanical step time |
| AVC | Automatic voltage control on T1 | target 10.6–11.4 kV, deadband ±0.75 %, AUTO/MANUAL |
| CB-101 | Feeder circuit breaker | OPEN / CLOSED; trips on fault; `protection_tripped` latch |
| SW-102 | Sectionaliser between S1 and S2 | OPEN / CLOSED |
| TS-201 | Tie to feeder F2 | OPEN / CLOSED (normally open) |
| PV-1 | 2.0 MWp PV plant at B2 | curtailment setpoint 0–100 %; trips when de-energised |
| B1, B2, B3 | Load groups | residential, industrial, hospital (critical) |
| FI-1..3 | Fault indicators per section | set by a fault, cleared by field repair |
| SP | Switching program | none / active (id, sections covered) |
| PTW | Permit-to-work | none / section id |

---

# 12. Process Variables and Physics

The simulator produces telemetry every **500 ms** and integrates physics over
**measured wall-clock time**, so durations in the logs are real seconds.

## 12.1 Voltages

```text
V_bus  = 11.0 kV × (1 + tap × 0.0125) × (V_source / 33 kV)
```

Per section, with the load and PV downstream of that section:

```text
ΔV_section ≈ (P × R + Q × X) / V        (P in W, Q in var, V in V)
V_next     = V_prev − ΔV_section
```

Section impedances: S1 0.40 + j0.60 Ω, S2 0.60 + j0.90 Ω, S3 0.40 + j0.60 Ω.
Loads at 0.95 power factor lagging. PV exports at unity power factor and
*reduces* the net P flowing through S1 and S2.

This ignores reactive-power control, mutual coupling and unbalance. At peak
load the feeder drops about 4–5 %, which is why the AVC needs to sit a few taps
up — and why a tap changer walked to +8 puts the busbar at +10 % against a
statutory band of **±6 %** (10.34–11.66 kV).

## 12.2 Currents and loading

```text
I_feeder = S_through_S1 / (√3 × V_bus)          rating 400 A
```

## 12.3 Frequency

50.00 Hz ± 0.02 Hz noise while connected to the grid. A section with no source
reads 0 Hz and 0 V ("dead"). Frequency exists in the telemetry chiefly so the
replay attack can display a healthy value.

## 12.4 Supply state and customers

A bus is **supplied** if there is a closed path to T1 (through CB-101 and the
switches upstream of it) or to F2 (through TS-201 and the switches between).
Unsupplied buses report 0 V, their load is lost, and `customers_off` and
**customer-minutes-lost (CML)** accumulate — the hospital bus counts as
critical.

## 12.5 Faults and protection

* A fault is injected on a section by the scenario runner (via `plant/sim`).
  The section's fault indicator sets and **CB-101 trips in 100 ms**
  (`protection_tripped = true`, breaker OPEN).
* The fault persists until `fault_clear` (field repair, also via `plant/sim`).
* **Closing CB-101 while a fault is present:** fault current 6.5 kA flows for
  150 ms, the breaker re-trips, `close_onto_fault_count` increments and
  `switchgear_stress` accumulates. This is the physical consequence the flagship
  demo shows.
* A `cb_close` with `protection_tripped` latched and no `protection_reset` is
  refused by the relay in a real plant; the simulator **accepts it** — the
  point of the exercise is that the controller does not second-guess a valid
  command.

## 12.6 Parallels and loops

If CB-101, SW-102 and TS-201 are all CLOSED, F1 and F2 are paralleled. The
simulator models a circulating current proportional to the voltage difference
between the two sources (`parallel_s` accumulates). Short parallels are normal
during load transfer; a standing parallel is not.

## 12.7 AVC (automatic voltage control)

In AUTO, the AVC raises or lowers the tap (respecting the 4 s step time) to hold
`V_bus` at `avc_target` within the deadband. Manual tap commands override the
AVC for 60 s. Changing `avc_target` changes what the AVC aims at — this is the
grid analogue of a process setpoint and the target of the drift attack.

## 12.8 Load and PV profiles

Loads follow a slow diurnal curve (compressed so a full cycle takes about ten
minutes) with noise. PV follows an irradiance random walk between 30 % and 90 %
of rated during "day". This keeps the network visibly alive without hiding the
effect of a command.

---

# 13. Telemetry Model

```json
{
  "ts": 1756980000000,
  "seq": 1045,
  "frequency_hz": 50.01,
  "v_source_kv": 33.1,
  "tap": 3,
  "avc_target_kv": 11.0,
  "avc_mode": "AUTO",
  "v_bus_kv": 11.04,
  "v_b1_kv": 10.91,
  "v_b2_kv": 10.78,
  "v_b3_kv": 10.71,
  "i_feeder_a": 231.4,
  "p_feeder_kw": 4160.0,
  "load_b1_kw": 1820.0,
  "load_b2_kw": 2380.0,
  "load_b3_kw": 905.0,
  "pv_kw": 1240.0,
  "pv_curtail_pct": 0,
  "cb_closed": true,
  "sw_closed": true,
  "tie_closed": false,
  "protection_tripped": false,
  "fault_present": false,
  "fault_section": null,
  "fault_indicators": [false, false, false],
  "supplied": {"b1": true, "b2": true, "b3": true},
  "customers_off": 0,
  "cml": 0.0,
  "close_onto_fault_count": 0,
  "switchgear_stress": 0.0,
  "parallel_s": 0.0,
  "switching_program": null,
  "permit_to_work": null
}
```

---

# 14. Command Model

```json
{ "id": "8b7e…", "ts": 1756980000000, "source": "operator-hmi", "action": "cb_close" }
{ "id": "9d13…", "ts": 1756980000000, "source": "operator-hmi", "action": "avc_target", "value": 11.1 }
```

| Command | Equipment | Description |
|---|---|---|
| `cb_open` / `cb_close` | CB-101 | open / close the feeder breaker |
| `protection_reset` | CB-101 relay | clear the trip latch after a fault |
| `sw_open` / `sw_close` | SW-102 | sectionaliser |
| `tie_open` / `tie_close` | TS-201 | tie to feeder F2 |
| `tap_raise` / `tap_lower` | OLTC | one tap step (manual override) |
| `tap_set` (value) | OLTC | go to a tap position |
| `avc_target` (value, kV) | AVC | voltage-control target (**the drift setpoint**) |
| `avc_auto` / `avc_manual` | AVC | control mode |
| `pv_curtail` (value, %) | PV-1 | inverter active-power limit |
| `switching_program_on` (value: id) / `switching_program_off` | SP | declare / clear a planned switching program |
| `ptw_issue` (value: section) / `ptw_cancel` | PTW | permit-to-work on a section |

Simulator-only hooks on `plant/sim` (used by scenarios, never by the guard):
`fault_inject` (section), `fault_clear`, `telemetry_hold_s`, `reset`.

---

# 15. Detection Engine

Seven layers, as in revision 1. Each rule is a pure function that returns
weighted, human-readable **findings**. The risk engine sums them.

---

# 16. Layer 1 — State-Based Detection

| Rule | Fires when | Why it is unsafe |
|---|---|---|
| **STATE-001 Close onto fault** | `cb_close` while `fault_present` or `protection_tripped` and not reset | drives fault current into a standing fault; damages switchgear and cables; endangers crews |
| **STATE-002 Open under load without transfer** | `cb_open` while CB carries load and no alternate path (tie open) | loss of supply to all downstream load, including the critical bus |
| **STATE-003 Uncontrolled parallel** | `tie_close` while CB-101 and SW-102 are closed | loops two sources; circulating current; protection no longer grades |
| **STATE-004 Energise section under permit** | `sw_close`, `tie_close` or `cb_close` that energises a section with an active permit-to-work | live-line hazard to a working crew |
| **STATE-005 Isolate critical bus** | `sw_open` while B3 is fed only through S2 | drops the hospital |
| **STATE-006 Tap step at limit** | `tap_raise` when `V_bus` is already above the statutory band (or `tap_lower` below it) | pushes the feeder further outside limits |
| **STATE-007 Curtail during islanded / deficit** | `pv_curtail` to 100 % while the feeder is heavily loaded and near its rating | removes the local support that keeps the feeder inside limits |

Flagship example — current state:

```text
Fault present: S2          Protection tripped: yes, 40 s ago         Fault cleared: no
Switching program: none    Permit-to-work: none                       Source: engineering-laptop
```

Incoming: `cb_close` → **STATE-001, CRITICAL.**

---

# 17. Layer 2 — Command Sequence Analysis

The guard keeps the last 40 commands with timestamps and evaluates known
patterns:

| Pattern | Meaning |
|---|---|
| `protection_reset → cb_close` within seconds of a trip, no `fault_clear` | forcing a re-close onto a fault |
| `tie_open → sw_open → cb_open` outside a switching program | isolating the feeder piece by piece |
| `avc_manual → tap_raise × n` | disabling control and driving voltage by hand |
| `switching_program_off → cb_open` immediately | clearing the context that would have excused the command |

---

# 18. Layer 3 — Temporal Anomaly Detection

Switching is slow. Operators wait for indications between steps. The guard
flags **≥ 4 switching commands in 5 s** or **≥ 8 in 30 s**, and **breaker
pumping** (`cb_open`/`cb_close` on the same device inside 5 s).

---

# 19. Layer 4 — Rate of Change

* **ROC-001** — a single `avc_target` or `tap_set` step larger than an operator
  would make (> 1 tap / > 0.15 kV).
* **ROC-002 Slow drift** — ≥ 3 small steps whose **net** movement over 10 min
  exceeds 0.3 kV or 4 taps, with a projected **time to the statutory limit** at
  the current rate. Each step alone passes ROC-001; only the trajectory gives it
  away.

---

# 20. Layer 5 — Operating Envelope

| Quantity | Limit |
|---|---|
| Bus voltages | 11 kV ± 6 % (10.34–11.66 kV) statutory |
| Feeder current | 400 A rating; warn at 360 A |
| Tap | −8 … +8 |
| Standing parallel | > 60 s |

A command that pushes toward or beyond a limit adds risk (**ENV-001**
setpoint outside band, **ENV-002** command issued while already near a limit).

---

# 21. Layer 6 — Operational Context

| Context | Effect |
|---|---|
| **Switching program active** covering the affected equipment | isolation, transfer and restoration commands are expected: −30 |
| **Permit-to-work** on a section | *energising* that section is more dangerous, not less: +35 (STATE-004); isolating it is expected |
| **Fault cleared and protection reset** | `cb_close` is the expected restoration step |
| **Alternate path available** (tie closed, sectionaliser positioned) | opening a breaker no longer drops load |

This is the layer that turns "engineer does a planned switching operation" from
a false positive into a quiet log.

---

# 22. Layer 7 — Telemetry Integrity

The guard trusts nothing it cannot verify:

* **TEL-001** newest frame older than 3 s
* **TEL-002** sequence number repeated ≥ 3 times (replay)
* **TEL-003** sequence regressed with an *older* timestamp (injection); a
  lower sequence with a *newer* timestamp is a controller restart, not an attack
* **CMD-001** command with a timestamp > 30 s old or in the future, or a message
  id already processed (replayed traffic)
* **PHY-001** telemetry disagrees with the physics: breaker closed and loads
  present but zero current; bus voltage inconsistent with tap and load for
  more than 4 s

Integrity findings **add** to the score of any command evaluated on an
untrusted picture. Uncertainty raises the advisory; it never suppresses it.

---

# 23. Replay Detection

Same mechanism as revision 1: sequence continuity, frame age, arrival gap, and
a clean-run counter that clears a past regression after 20 good frames so the
demo is repeatable. The grid replay scenario freezes a healthy frame, opens the
tie and sectionaliser behind it so the hospital bus goes dead while the display
shows 11 kV, then replays a captured command verbatim.

---

# 24. Risk Scoring

Additive, deterministic, clamped 0–100. Every point is traceable to a named
finding.

| Band | Severity |
|---|---|
| 0–29 | LOW |
| 30–59 | MEDIUM |
| 60–79 | HIGH |
| 80–100 | CRITICAL |

Advisories are raised at ≥ 15; everything is logged as an assessment.

| Finding | Weight |
|---|---|
| Fault present on the section being energised | +40 |
| Protection tripped and not reset | +20 |
| Critical load downstream / affected | +15 |
| Loss of supply with no alternate path | +35 |
| Breaker carrying load | +20 |
| Uncontrolled parallel | +30 |
| Energising a section under permit-to-work | +35 |
| Tap step while outside statutory band | +30 |
| Setpoint outside statutory band | +20 |
| Large single setpoint step / extreme step | +15 / +25 |
| Cumulative drift / limit reached soon | +25 / +15 |
| Rapid switching sequence / burst | +25 / +12 |
| Breaker pumping | +15 |
| Known unsafe pattern | +25 |
| Telemetry stale / replay / regression | +25 / +35 / +25 |
| Command stale timestamp / duplicate id | +30 / +40 |
| Physics mismatch | +25 |
| Untrusted source | +10 |
| Not in a switching program | +10 |
| **Switching program covers this equipment** | **−30** |
| **Fault cleared and protection reset** | **−25** |
| **Alternate path available** | **−25** |

---

# 25. Example Risk Calculation

```text
State:    fault on S2, protection tripped 40 s ago, fault not cleared,
          B3 (hospital) downstream, no switching program
Command:  cb_close   from engineering-laptop

Fault present on section being energised   +40
Protection tripped, not reset               +20
Critical load downstream                    +15
Not in a switching program                  +10
Source not recognised for this mode         +10
--------------------------------------------------
Risk                                         95   CRITICAL
```

Same command after `fault_clear`, `protection_reset`, switching program active:

```text
Fault cleared and protection reset          −25
Switching program covers CB-101             −30
--------------------------------------------------
Risk                                          0   no advisory
```

---

# 26. Alert Structure

```json
{
  "level": "CRITICAL",
  "rule": "STATE-001",
  "score": 95,
  "summary": "Breaker close onto an uncleared fault",
  "equipment": "CB-101 / Section S2 / Hospital bus B3",
  "why": "Protection tripped CB-101 40 s ago for a fault on S2 and the fault indicator is still set. Closing now drives fault current into the fault: switchgear and cable damage, and a hazard to anyone on the section.",
  "recommendation": "Confirm with the field crew that the fault is located and cleared, reset protection, then close under a switching instruction. If nobody in the control room issued this close, treat the source as compromised.",
  "confidence": "HIGH",
  "uncertainty": "Based on fresh, sequence-consistent telemetry (age 0.3 s, seq 1045). Sentinel advises only; it has not acted on the network.",
  "command": {"action": "cb_close", "source": "engineering-laptop", "...": "..."},
  "state": {"fault_present": true, "protection_tripped": true, "...": "..."},
  "findings": [{"rule": "STATE-001", "weight": 40, "detail": "Fault indicator FI-2 set 40 s ago; fault not cleared"}]
}
```

---

# 27. User Interface

Five views in a sidebar application shell (light and dark themes):

**Overview** — security posture strip (severity, headline, score on a banded
scale, context, telemetry integrity), KPI cards (busbar voltage, hospital bus
voltage, feeder current, tap / AVC target) with sparklines, equipment row
(CB-101, SW-102, TS-201, PV-1, switching program), **single-line mimic**, latest
advisory, real-time trend, recent activity.

**Advisories** — filterable table with sticky detail: command, source, equipment,
state at evaluation, why, weighted contributions, recommended action,
confidence statement.

**Timeline** — every command (with the guard's verdict), physical event,
protection operation, scenario narration; millisecond wall-clock stamps; search.

**Scenarios** — the scripted attacks and legitimate operations, plus an operator
console.

**Detection rules** — operating policy (what the system does when unsure), risk
model thresholds, rule catalogue with weights.

---

# 28. Dashboard Layout

```text
┌ Sidebar ┐ ┌──────────────────────────────────────────────────────────────┐
│Overview │ │ POSTURE  CRITICAL RISK  Breaker close onto an uncleared fault │
│Advisor. │ │ Score 95 ▮▮▮▮▮▮▮▮▮▯  Context: none  Telemetry: trusted        │
│Timeline │ ├───────────┬───────────┬───────────┬───────────────────────────┤
│Scenarios│ │ V busbar  │ V hospital│ I feeder  │ Tap +3 · AVC 11.0 kV AUTO │
│Rules    │ ├───────────┴───────────┴───────────┴───────────────────────────┤
│         │ │ CB-101 CLOSED · SW-102 CLOSED · TS-201 OPEN · PV 1.2 MW · SP — │
│         │ ├──────────────────────────────┬────────────────────────────────┤
│         │ │ SINGLE-LINE MIMIC            │ LATEST ADVISORY                │
│         │ │  T1 ▭ CB ■ S1 ● B1 ■ S2 ● B2 │  CRITICAL · STATE-001          │
│         │ │   ● B3 ■ TS ── F2   ⚡ fault  │  why · contributions · action  │
│         │ ├──────────────────────────────┼────────────────────────────────┤
│         │ │ TREND  V / I / tap, real time│ RECENT ACTIVITY                │
└─────────┘ └──────────────────────────────┴────────────────────────────────┘
```

---

# 29. Process Visualisation

The mimic is a live **single-line diagram**: buses coloured by voltage band,
breakers and switches drawn open/closed, animated power-flow dashes on
energised sections, a fault flash on a faulted section, dead sections greyed
out, the hospital bus marked critical, tap position and PV output annotated.
During the flagship attack the judge sees the fault marker still present when
the close arrives, the fault-current flash, and the breaker re-open.

---

# 30. Attack Simulation

Deterministic, scripted, reproducible, entirely inside the simulator. Each
scenario narrates itself on the timeline.

# 31. Scenario 1 — Command injection: open a healthy feeder breaker

State: feeder healthy, all buses supplied, tie open.
Attacker (`engineering-laptop`): `cb_open`.
Expected: **HIGH / STATE-002** — loss of supply to B1, B2, B3 including the
hospital, with no alternate path. Physics: three buses go dead, `customers_off`
and CML climb.

# 32. Scenario 2 — Valid command at the wrong moment: close onto a fault (flagship)

Scenario injects a fault on S2 → protection trips CB-101; FI-2 set; hospital
dark. A completely ordinary `cb_close` arrives from an engineering laptop before
`fault_clear`.
Expected: **CRITICAL / STATE-001**. Physics: 6.5 kA for 150 ms, re-trip,
`close_onto_fault_count` = 1, switchgear stress.

# 33. Scenario 3 — Slow drift: walk the voltage target out of limits

Attacker raises `avc_target` by 0.1 kV every few seconds: 11.0 → 11.1 → … →
11.8 kV. Each step is inside normal trimming. The AVC obediently taps up.
Expected: **MEDIUM / ROC-002** while still inside the statutory band, with a
projected time-to-limit; **HIGH** once `V_bus` crosses 11.66 kV (ENV-001 adds).

# 34. Scenario 4 — Rapid switching sequence / breaker pumping

`cb_open, cb_close, cb_open, cb_close, tie_close` inside four seconds.
Expected: **MEDIUM+ / SEQ-002 + SEQ-003** (rapid sequence, breaker pumping);
STATE-003 if the tie close creates a parallel.

# 35. Scenario 5 — Telemetry and command replay

A routine operator command is recorded. Controller telemetry is suppressed and
a healthy frame (50.00 Hz, 11.0 kV, all supplied) is replayed for 30 s. Behind
it the attacker opens SW-102 — B2 and B3 go dead while the display shows them
supplied — and then replays the captured command verbatim (same id, old
timestamp).
Expected: **TEL-002** escalating MEDIUM → HIGH as the frame ages; **CMD-001**
CRITICAL; every advisory marked **Confidence LOW**; on return the display snaps
to the real dead buses and the sequence counter jumps.

# 36. Scenario 6 — Legitimate planned switching (must stay quiet)

`switching_program_on SP-0417` (covers S1, CB-101, SW-102, TS-201) →
`tie_close` (short parallel) → `sw_open` (B2, B3 now from F2) → `cb_open`
(S1 dead for work) → `ptw_issue S1` → work → `ptw_cancel` → `cb_close` →
`sw_close` → `tie_open` → `switching_program_off`.
Expected: **no advisory above LOW.** The same `tie_close`, `sw_open`, `cb_open`
outside a program would each raise HIGH.

Also: **Scenario 7 — Fault, locate, clear, restore** (protection trip, crew
clears, `fault_clear`, `protection_reset`, `cb_close` → quiet: expected
restoration); **Scenario 8 — Planned outage and restoration** (shutdown /
start-up of the feeder in the correct order); **Scenario 9 — Normal operations**
(AVC trims, PV curtailment for a constraint, routine tap changes).

---

# 37. False-Positive Reduction

Considered before any HIGH advisory: switching-program coverage, permit-to-work,
fault-cleared and protection-reset state, alternate-path availability, AVC mode,
previous commands, telemetry trust, command frequency, source.

> We don't just detect anomalies. We understand why they might be happening.

---

# 38. REST API

`GET /api/state · /api/status · /api/alerts · /api/events · /api/assessments ·
/api/trend · /api/stats · /api/scenarios · /api/rules · /api/export/alerts.csv ·
/api/stream (SSE)`
`POST /api/command · /api/scenario/<id> · /api/scenario/stop · /api/reset`

`/api/status` example:

```json
{"status": "CRITICAL_RISK", "risk_score": 95, "telemetry_fresh": true,
 "telemetry_trusted": true, "context": "NONE", "commands_seen": 12}
```

# 39. MQTT Topics

```text
plant/telemetry     feeder frames, 2 Hz
plant/command       control commands (anyone can publish — that is the point)
plant/event         physical and protection events, command acceptance
plant/mode          retained: AVC mode, switching program, permit
plant/sim           simulator hooks: fault_inject, fault_clear, telemetry_hold_s, reset
guard/alert         advisories
guard/assessment    verdict for every command
guard/status        retained posture
sentinel/control    demo housekeeping (reset)
```

# 40. Data Model

**Telemetry** — as §13. **Command** — id, ts, source, action, value.
**Alert** — id, ts, level, rule, score, summary, equipment, why, recommendation,
confidence, uncertainty, command, state, findings, context, suppressed_score.
**Event** — id, ts, type, source, payload.

# 41. Security Model

**Observation and control are separated.** The guard subscribes to telemetry and
commands and publishes advisories. It has no topic it can write that the
simulator listens to.

**Human-in-the-loop.** DETECTION → EXPLANATION → RECOMMENDATION → ENGINEER
DECISION. Never DETECTION → AUTOMATIC SWITCHING.

**What the system does when it is unsure.** When telemetry is stale or
replayed, when instruments disagree with the physics, or when there is no
telemetry at all, Sentinel still raises the advisory, marks it LOW or REDUCED
confidence, states exactly what it could not verify, and asks for the network
state to be confirmed by other means. It never blocks. Protection operations and
commands whose physics justifies them (opening a faulted feeder, curtailing an
overloaded export) are never flagged as unsafe on their own — in a grid,
"safe direction" is context-dependent, so the policy is stated per rule rather
than per command.

---

# 42. MVP — Must Have

| | |
|---|---|
| M1 | Simulated feeder with transformer/OLTC, breaker, sectionaliser, tie, PV, three loads, fault model |
| M2 | MQTT communication |
| M3 | Command simulator (operator console + scenario runner) |
| M4 | Telemetry at 2 Hz with sequence numbers, wall-clock timestamps |
| M5 | State-based detection: close-onto-fault, open-under-load, uncontrolled parallel, energise-under-permit |
| M6 | Sequence and timing detection |
| M7 | Setpoint anomaly: large step and slow drift with time-to-limit |
| M8 | Switching-program and permit-to-work context |
| M9 | Telemetry freshness, replay, regression; command replay |
| M10 | Additive risk score with per-finding explanation |
| M11 | Engineer-readable advisory with confidence and uncertainty statement |
| M12 | Dashboard with single-line mimic |
| M13 | Scenarios: injection, close-onto-fault, drift, rapid switching, replay |
| M14 | Legitimate scenarios: planned switching, fault-and-restore, planned outage/restoration, normal operations |
| M15 | Acceptance tests mapping to §52 and a live-broker integration test |

# 43. Should Have

Historical timeline · risk trend · single-line animation · scenario progress ·
source tracking · CSV export · detection statistics · policy view.

# 44. Could Have

Isolation-Forest baseline on command sequences · `pandapower` AC power flow ·
IEEE 13-bus feeder · Modbus/SunSpec adapter for the PV inverter · MITRE ATT&CK
for ICS mapping · signed telemetry · role-based access.

# 45. Machine-Learning Extension

Not in the foundation. A later layer can learn normal switching sequences per
shift and flag deviations; Isolation Forest over (action, interval, context)
tuples is the natural first step.

# 46. Digital-Twin Extension

The algebraic model already predicts bus voltages and feeder current from tap,
loads and PV. **PHY-001** compares prediction with telemetry; `pandapower`
would sharpen the prediction without changing the rule.

# 47. Physics-Based Detection

```text
Residual = | V_predicted − V_reported |     or     | I_predicted − I_reported |
Residual > threshold for > 4 s  →  PHY-001
```

Breaker reported closed, loads present, current zero → the picture is wrong.

# 48. Advanced Architecture

Unchanged from revision 1: command analysis → risk engine → {state rules,
sequence rules, ML} → digital twin / physics → engineer advisory.

---

# 49. Functional Requirements

| | |
|---|---|
| FR-001 | Ingest feeder telemetry |
| FR-002 | Observe every command on the command topic |
| FR-003 | Maintain the latest network state, topology (supply paths) and protection state |
| FR-004 | Evaluate every command against that state |
| FR-005 | Keep a command history for sequence and timing rules |
| FR-006 | Apply switching-program and permit-to-work context |
| FR-007 | Validate telemetry freshness, sequence continuity, and command timestamps/ids |
| FR-008 | Compute an additive, explainable risk score |
| FR-009 | Raise advisories above the threshold; log every assessment |
| FR-010 | Every HIGH/CRITICAL advisory carries why, equipment, recommendation, confidence |
| FR-011 | The guard performs no protective action and has no write path to the plant |
| FR-012 | Dashboard shows state, single-line mimic, advisories, timeline |

# 50. Non-Functional Requirements

Detection latency < 1 s (asserted over the live broker) · dashboard survives
scenario failures · every advisory readable by a non-security engineer · no real
equipment · `./run.sh` or `docker compose up` reproduces the environment ·
all timestamps wall-clock.

# 51. Testing Strategy

**Unit:** physics (voltage drop, supply paths, fault/trip, parallel), each rule,
risk arithmetic, integrity tracking, confidence.
**Acceptance:** one test per criterion in §52.
**Integration:** simulator → MQTT → guard → status, latency assertion.
**Attack tests:** injection, close-onto-fault, drift, rapid switching, replay.
**False-positive tests:** planned switching, fault-and-restore, planned
outage/restoration, normal operations — quiet or LOW only.

# 52. Acceptance Criteria

| | |
|---|---|
| T1 | Normal operation, planned switching, fault-and-restore and planned outage/restoration produce no HIGH advisory |
| T2 | `cb_close` with a standing fault produces ≥ HIGH (STATE-001) with the fault named in the findings |
| T3 | `cb_open` on a healthy feeder with no alternate path produces ≥ HIGH (STATE-002) naming the hospital bus |
| T4 | `tie_close` creating a parallel outside a program produces ≥ MEDIUM (STATE-003) |
| T5 | A large `avc_target` step produces ≥ MEDIUM; a slow drift is flagged (ROC-002) **before** the statutory limit is crossed, with time-to-limit, and ≥ HIGH once crossed |
| T6 | The identical switching sequence inside an active switching program produces nothing above LOW; the same sequence outside one produces HIGH |
| T7 | Energising a section under permit-to-work produces ≥ HIGH even inside a switching program |
| T8 | Replayed telemetry is flagged (TEL-002) and escalates as it ages; a replayed command is flagged (CMD-001); advisories issued on untrusted state carry Confidence LOW and a plain uncertainty statement |
| T9 | Every advisory explains what, which equipment, why and what to verify |
| T10 | No automatic operation of any switchgear or setpoint ever occurs |

# 53. Development Roadmap (migration from revision 1)

| Phase | Work | Reuse |
|---|---|---|
| 1 | Feeder simulator: topology, supply paths, voltage drop, AVC/OLTC, PV, fault/protection, parallel, CML | telemetry/sequence/hold mechanics, real-time stepping |
| 2 | Telemetry and command models, catalogue of actions | bus, store, models |
| 3 | Rules: STATE-001…007, sequence patterns, ROC on `avc_target`/tap, envelope on voltage/current, context on switching program / permit, PHY-001 on V and I | engine, risk arithmetic, integrity, confidence, CMD-001, TEL-* unchanged |
| 4 | Narratives and rule catalogue | risk.py structure |
| 5 | Scenarios 1–9 | runner (remember/replay, threaded telemetry hold) |
| 6 | Dashboard: single-line mimic, KPI set, equipment row, trend series | app shell, views, advisory component, chart, theme |
| 7 | Tests: physics, rules, acceptance §52, integration | harness |
| 8 | Docs: QUICKSTART, demo script | — |

# 54. Team Structure

Power/process engineer (feeder model, unsafe operations, switching practice) ·
security engineer (rules, scenarios, integrity) · backend (MQTT, API, tests) ·
frontend/product (mimic, advisory UX, presentation).

# 55. What We Should Not Do

Full AC power flow before the detector works · DNP3/61850 stacks · SIEM,
packet capture, scanners · authentication systems · more than one feeder.

# 56. What Makes This Competitive

1. **Protocol-valid ≠ electrically safe** — the flagship close-onto-fault is the
   purest possible statement of the brief.
2. **Context-aware** — the identical switching sequence is an attack or a
   planned job depending on the program in force.
3. **Physics-informed** — voltages, currents, protection state and supply paths
   are first-class inputs.
4. **Explainable** — weighted findings a dispatcher can argue with.
5. **Honest about uncertainty** — confidence and a stated policy for when the
   picture cannot be trusted.
6. **Human-in-the-loop** — by construction, not by configuration.

# 57. The Winning Demo — Step 1: everything is normal

Busbar 11.04 kV, hospital bus 10.71 kV, 231 A, tap +3, AVC AUTO, PV 1.2 MW,
posture NORMAL.

# 58. Step 2: the fault and the close

A fault on S2. Protection trips; the hospital goes dark; nobody is surprised —
that is protection doing its job. Then a `cb_close` arrives. Don't say it's
malicious. Sentinel goes CRITICAL *before* the breaker moves; then the physics
confirms it: fault-current flash, re-trip, stress counter. Read the advisory:
the fault indicator still set, protection not reset, hospital downstream,
source not recognised.

# 59. Step 3: context

Run the planned switching program: tie close, sectionaliser open, breaker
open, permit issued. Same commands. Nothing above LOW — and the advisory panel
explains *why* it stayed quiet. Then cancel the program and send `sw_open`
again: HIGH.

# 60. Step 4: the frozen picture

Replay. Display shows 50.00 Hz, 11.0 kV, all supplied. Telemetry pill goes red,
sequence stuck, *Telemetry integrity: Untrusted*. Behind it the hospital bus is
dead. The replayed command lands as CRITICAL. Every advisory says *Confidence
Low* and what Sentinel could not verify. Then telemetry returns and the mimic
snaps to reality.

# 61. Narrative

> "An attacker doesn't need an invalid command. They need a valid one at the
> wrong moment. We built the layer that knows what the moment is."

# 62. Product Name

**Sentinel** — Critical Infrastructure Command Guard.
*Valid commands. Unsafe consequences. Detected in context.*

# 63. Final Product Definition

```text
      FEEDER SIMULATION ──► telemetry ──┐
                                        ├──► MQTT ──► COMMAND GUARD ──► ADVISORY ──► ENGINEER
      OPERATORS / ATTACKERS ─► commands ┘             state · sequence · context
                                                      integrity · physics · confidence
```

# 64. Priority Order

| Priority | Component |
|---|---|
| 1 | Feeder simulator with fault/protection and supply paths |
| 2 | MQTT plumbing (reuse) |
| 3 | STATE-001 close-onto-fault, STATE-002 open-under-load |
| 4 | Risk scoring and advisory narrative (reuse engine) |
| 5 | Single-line mimic |
| 6 | Switching-program / permit context |
| 7 | Scenarios 1, 2, 6 |
| 8 | Drift rule on `avc_target`, scenario 3 |
| 9 | Replay scenario (reuse integrity + CMD-001) |
| 10 | Remaining rules, scenarios, polish |
| 11 | `pandapower` — only if everything above is demo-stable |

**Do not start with power flow.** Get close-onto-fault, planned switching and
the frozen picture demo-stable first. Everything else is enhancement.
