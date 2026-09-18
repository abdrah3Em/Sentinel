# Sentinel — Critical Infrastructure Command Guard

> **Valid commands. Unsafe consequences. Detected in context.**

[![Release](https://img.shields.io/badge/release-v1.0.0--icsc-orange.svg)](https://github.com/abdrah3Em/Sentinel/releases/tag/v1.0.0-icsc)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](requirements.txt)
[![Track](https://img.shields.io/badge/Track%20E-Critical%20Infrastructure%20%26%20Energy-orange.svg)](docs/)
[![Protocols](https://img.shields.io/badge/OT%2FICS-Modbus%20TCP%20%2B%20MQTT-purple.svg)](sentinel/plant/modbus.py)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Sentinel** is a process-aware operational technology (OT/ICS) command guard for an **11 kV electrical distribution feeder**. It watches every switching command and setpoint sent to the feeder's RTU — over **Modbus TCP** (the controller's native protocol, through a passive wire tap) and MQTT — and answers the question traditional security tools never ask:

> Traditional controls ask: **"Is this command authorised and well-formed?"**
> Sentinel asks: **"Does this command make physical sense for the network right now?"**

A `cb_close` is issued fifty times a week. The one sent while a fault is still on the section drives kilo-amps into it. Sentinel tells the control engineer *what* was commanded, *which equipment* it touches, *why* it is dangerous, *how sure* it is, and *what to verify* — and never operates the network itself.

Built for **ICSC 2026 · Track E (Critical Infrastructure & Energy) · Challenge E1 — Catching Unsafe Commands in Your Own Control System**.

![Grid console during the close-onto-fault attack](docs/img/ui-grid-overview.png)

---

## ⚡ Quickstart

### 1. One line (what the judges type)

```bash
git clone https://github.com/abdrah3Em/Sentinel.git && cd Sentinel && docker compose up
```

Open **http://localhost:8080**. The operator token is in the console log (`docker compose logs dashboard-grid | grep token`).

### 2. Local Python (the demo laptop)

Requires Python 3.10+ and Mosquitto (`sudo apt install mosquitto` / `brew install mosquitto`):

```bash
pip install --require-hashes -r requirements.txt
./run.sh                     # broker + feeder simulator + Modbus RTU + guard + console
```

`run.sh` prints a ready-to-open `http://localhost:8080/?token=…` link. `Ctrl-C` stops everything.

### 3. Tests, demo, numbers

```bash
make test            # the whole suite (integration tests need a broker on 1883)
make demo            # the four-minute demo, headless, every verdict asserted
make demo-live       # the same sequence driven through the running console
make metrics         # regenerate docs/RESULTS.md and the numbers below from a real run
```

---

## 🔌 The Feeder

One 11 kV radial feeder **F1**: a 33/11 kV transformer with an on-load tap changer under automatic voltage control, feeder breaker **CB-101**, sectionaliser **SW-102**, normally-open tie **TS-201** to feeder **F2** (its own transformer and breaker **CB-201**), a 2 MWp PV plant, and load groups including **hospital bus B3**. The physics is a forward-backward sweep power flow: per-section voltage drop, path-dependent fault current, protection trips, source paralleling with circulating current, and a tap changer that obediently walks the busbar out of statutory limits if you ask it to.

**Assets:** T1/OLTC · BB-101 · CB-101 · S1–S3 · SW-102 · PV-1 · TS-201 · CB-201 · S4–S5 · buses B1–B5
**Physics modelled:** close-onto-fault (fault current, re-trip, switchgear stress), open under load (customers off, customer-minutes-lost), uncontrolled parallel, per-section permits-to-work, concurrent faults, AVC drift out of the ±6 % band.
**Full PRD:** [`docs/PRD-v2-distribution-grid.md`](docs/PRD-v2-distribution-grid.md).

---

## 🎯 Why This Matters

* **Closing onto a fault destroys switchgear and can kill a crew.** The command is protocol-valid, correctly formed and sent from a host that holds a key. Only the *moment* is wrong.
* **Cascading grid collapses** follow uncoordinated or spoofed switching commands sent to substation breakers; a hospital on the wrong bus goes dark first.
* **The OT constraint:** **Sentinel never acts on the network.** Automated tripping causes the outage it was meant to prevent. Sentinel provides **human-interpretable advisories with forensic evidence**, keeping the decision with the qualified engineer. Trust boundary and residual risks: [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md).

---

## 🛡️ The 7-Layer Detection Engine

Every command is evaluated across seven deterministic layers plus a learned baseline:

| Layer | Rules | Evaluates | Example violation |
|---|---|---|---|
| **1. State** | `STATE-001…007` | Physical state of the network | `cb_close` while FI-2 is set and protection is tripped (close onto fault) |
| **2. Sequence** | `SEQ-004` | Multi-step command order | `protection_reset` → `cb_close` with the fault still present |
| **3. Timing** | `SEQ-002` / `SEQ-003` | Rapid switching, breaker pumping | five switching commands in four seconds |
| **4. Rate of change** | `ROC-001` / `ROC-002` | Target steps and slow drift | AVC target walked 0.1 kV at a time toward 11.66 kV, with time-to-limit |
| **5. Operating envelope** | `ENV-001` / `ENV-002` | Statutory band, feeder rating | a target that puts the busbar outside 10.34–11.66 kV |
| **6. Context** | `CTX-001…003` / `SRC-001` | Switching programs, permits, restoration, signed sources | the same sequence is quiet under a program naming its equipment; energising a permitted section is HIGH regardless |
| **7. Integrity** | `TEL-001…003` / `CMD-001` / `PHY-001` | Stale or replayed telemetry, replayed commands, physics mismatch | sequence counter frozen (`seq 41 ×12`), a replayed envelope, a busbar that disagrees with the tap |
| **Baseline** | `BASE-001` | Learned cadence, ranges and command mix per source | a first-ever `cb_open` from a source that only ever trims |

### Explainable Risk Scoring

$$\text{Risk Score} = \min\left(100, \max\left(0, \sum \text{Finding Weights}\right)\right)$$

Bands: **LOW · MEDIUM · HIGH · CRITICAL** (`config.SEVERITY_BANDS`). Every advisory lists its exact contributions (e.g. `+40 FI-2 set on S2`, `+20 protection tripped, not reset`, `+15 hospital downstream`, `−30 program covers CB-101`).

### When it is unsure

Stale, replayed or physically inconsistent telemetry never silences an advisory: it is raised at **LOW** or **REDUCED** confidence with a plain statement of what could not be verified. **It never blocks** — the guard has no write path to any switchgear.

### Signed commands, defeated replay

Every keyed source signs `seq | ts | nonce | id | action | value` with a per-source HMAC key. The guard verifies the MAC, a per-source monotonic sequence, a nonce cache and a freshness window before any rule runs; rewriting `seq` or `ts` breaks the MAC, a verbatim replay fails sequence, nonce and clock. A compromised workstation with a valid key still produces a valid signature — which is why the physics and wrong-moment rules never consult it.

---

## 📱 Field Engineer Mobile Alerts

Engineers are on call, not watching a desk HMI. HIGH and CRITICAL advisories go out through the asynchronous **Alert Dispatcher** ([`sentinel/guard/dispatcher.py`](sentinel/guard/dispatcher.py)):

* **Telegram:** set `SENTINEL_TELEGRAM_BOT_TOKEN` and `SENTINEL_TELEGRAM_CHAT_ID`.
* **Webhook:** set `SENTINEL_WEBHOOK_URL` (Slack, Discord, Teams, or an SMS gateway).
* **Live format:**
  ```text
  🚨 CRITICAL · STATE-001 · 95/100

  Breaker close onto an uncleared fault

  Equipment
  CB-101 / faulted section / Hospital bus B3

  Command
  cb_close from engineering-laptop

  Why it matters
  Protection tripped for a fault that is still on the section; closing now drives the
  source fault current into it.

  Do this
  Confirm the crew has cleared the fault, reset protection, then close under a switching
  instruction.

  Confidence HIGH · Sentinel advises only, human decides.
  ```

---

## 🎬 4-Minute Hackathon Demo Script

Run scenarios from the **Scenarios** view, the CLI, or straight at the RTU over Modbus. Full timed script and the 30-second fallback: [`docs/DEMO.md`](docs/DEMO.md). `make demo` asserts every verdict below.

```bash
python3 -m sentinel.attacks.run close_onto_fault              # over MQTT
python3 -m sentinel.attacks.modbus_inject coil 0 1            # cb_close, straight to the RTU on :5020
```

| Scenario | Attack type | What happens on screen | Verdict |
|---|---|---|---|
| **1. Close onto a standing fault (Flagship)** | Wrong-moment command | A fault on S2 trips CB-101 and darkens the hospital bus. A valid `cb_close` arrives from an engineering laptop before the crew has cleared it. | **CRITICAL · STATE-001**. Fault-current flash on the mimic, re-trip, stress counter. Then crew clears, protection reset, operator closes: quiet. |
| **2. Open a healthy feeder breaker** | Command injection | `cb_open` with ~200 A on the feeder and the tie open. | **CRITICAL · STATE-002** — three buses dead, customers-off and CML climb. |
| **3. Slow AVC-target drift** | Stealth trajectory | The voltage target rises 0.1 kV at a time; the tap changer walks the busbar out of limits. | **MEDIUM · ROC-002** (time-to-limit) → **HIGH/CRITICAL** past 11.66 kV. |
| **4. Breaker pumping** | Rapid switching | Open/close/open/close/tie-close inside four seconds. | **SEQ-002 / SEQ-003**, then **STATE-003** parallel. |
| **5. Telemetry & command replay** | Sensor spoofing | A healthy frame is replayed while SW-102 is opened behind it; a captured command is replayed verbatim. | **TEL-002**, **CMD-001**, every advisory **Confidence LOW**. |
| **6. Planned switching program** | Context awareness | The same tie close, sectionaliser open, breaker open — under a declared program naming its equipment, with a permit. | **Nothing above LOW · CTX-001**; energising the permitted section is still **HIGH · STATE-004**. |
| **7. Fault, locate, clear, restore** | False-positive check | Protection trips, crew clears, `protection_reset`, `cb_close`. | **Quiet · CTX-002** — the expected restoration step. |
| **8. Planned outage and restoration** | False-positive check | Transfer through the tie, open the feeder, restore, under a program. | **Nothing above LOW**. |
| **9. Normal operations** | False-positive check | AVC trims, a routine tap change, a PV curtailment. | **Quiet**. |

---

## 📊 Results

<!-- metrics:start -->
- **133 offline tests pass**; 5 more run end to end over a live broker.
- **11/11 attack scenarios** detected at their required level; **0 false positives** above LOW across 8 legitimate scenarios.
- Flagship close-onto-fault: **CRITICAL · score 95**, raised the moment the command arrives (evaluation p95 **0.84 ms**).
- Full tables: [docs/RESULTS.md](docs/RESULTS.md).
<!-- metrics:end -->

---

## 🔁 Second Process, Same Engine

The detector core is process-agnostic. A **crude oil pipeline pump station** — tank farm T-101, mainline pump P-101, sectionalising valve MOV-201, emergency shutdown valve ESD-301, DRA injection — runs on the same engine behind one flag as a portability proof, never as a parallel story:

```bash
./run.sh --profile pipeline                 # http://localhost:8081
docker compose --profile pipeline up        # the same, in compose
```

---

## 🏗️ Repository Structure

```
Sentinel/
├── run.sh                        # one-command demo: broker, feeder, RTU, guard, console
├── Makefile                      # test · demo · demo-live · metrics · check-metrics · screenshots
├── requirements.txt              # pinned, hash-checked (scripts/pin_requirements.sh)
├── Dockerfile · docker-compose.yml   # digest-pinned images, healthchecks, shared data volume
├── sentinel/
│   ├── config.py                 # every threshold and weight, in one place
│   ├── signing.py                # HMAC command envelopes, verifier (sequence, nonce, freshness)
│   ├── demo.py · evaluate.py · metrics.py · harness.py   # the demo as a test, offline replay, generated numbers
│   ├── plant/
│   │   ├── grid.py               # feeder physics: sweep power flow, protection, AVC, faults, permits
│   │   ├── modbus.py             # Modbus TCP RTU (FC 1/2/3/4/5/6/15/16), wire tap, proxy tap
│   │   └── run.py                # plant service: telemetry, commands, RTU, Modbus frames
│   ├── guard/
│   │   ├── grid_rules.py         # the seven layers for the feeder (rules.py: pump station)
│   │   ├── risk.py · grid_risk.py    # additive scoring, severity bands, narratives
│   │   ├── baseline.py           # learned cadence, ranges, command mix
│   │   ├── engine.py · state.py  # evaluation, confidence, integrity tracking
│   │   ├── store.py · run.py     # SQLite WAL persistence, the guard service
│   │   └── dispatcher.py         # Telegram / webhook alerts
│   ├── transport/                # MQTT and Modbus behind one envelope schema
│   ├── domains/                  # grid.py (flagship) · pipeline.py (portability proof)
│   ├── attacks/                  # scenario scripts, Modbus injector
│   └── api/                      # Flask + SSE console (no build step, no network)
├── tests/                        # unit, acceptance (one per PRD criterion), signing, persistence, Modbus, integration
├── scripts/                      # vocabulary, README, offline-render checks; requirement pinning
└── docs/                         # DEMO.md · RESULTS.md · THREAT-MODEL.md · DECISIONS.md · TASKS.md · PRD · img/
```

More detail: [`QUICKSTART.md`](QUICKSTART.md).

---

## 📜 License

Developed for ICSC 2026. Open source under the [MIT License](LICENSE).
