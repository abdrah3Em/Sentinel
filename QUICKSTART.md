# Sentinel — Quickstart

Sentinel is a process-aware command guard for an 11 kV distribution feeder. It observes
commands and telemetry of the simulated feeder over Modbus TCP and MQTT and asks, for
every command, whether it makes physical sense for the network right now. It advises;
it never operates switchgear. Demo script: [docs/DEMO.md](docs/DEMO.md). Numbers:
[docs/RESULTS.md](docs/RESULTS.md). Trust boundary: [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## 1. Run it

### Option A — Docker

```bash
git clone https://github.com/abdrah3Em/Sentinel.git && cd Sentinel && docker compose up
```

Broker, feeder simulator (with its Modbus TCP RTU on 5020), guard and console come up
with healthchecks. Open **http://localhost:8080**. The operator token is in the
`dashboard-grid` container log (`docker compose logs dashboard-grid | grep token`).
`docker compose --profile pipeline up` adds the pump station on 8081.

### Option B — local Python (the demo laptop)

Requirements: Python 3.10+, `mosquitto` on the PATH.

```bash
pip install -r requirements.txt      # pinned, hash-checked
./run.sh                             # broker + feeder simulator + guard + console
./run.sh --profile pipeline          # the pipeline pump station instead (portability proof)
./run.sh --profile both              # both consoles, each sidebar links to the other
```

| Console | URL | Process |
|---|---|---|
| Grid simulation (the demo) | **http://localhost:8080** | 11 kV feeder: T1/OLTC, CB-101, SW-102, TS-201, PV-1, hospital bus B3 |
| Pipeline simulation | **http://localhost:8081** | tank farm T-101, mainline pump P-101, MOV-201, ESD-301, DRA skid |

`Ctrl-C` stops everything; logs are in `./logs` (one `plant-`, `guard-` and `api-` log per process).

### Option C — by hand (four terminals)

```bash
mosquitto -c mosquitto/mosquitto.conf
python -m sentinel.plant.run          # feeder physics, 2 Hz telemetry, Modbus TCP RTU on :5020
python -m sentinel.guard.run          # the command guard (observer only)
python -m sentinel.api.app            # console on :8080
```

Every Sentinel process reads `SENTINEL_PROCESS` (`grid`, the default, or `pipeline`).
The two processes share the broker under separate topic namespaces (`grid/...`, `pipeline/...`).

### Operator token

Every mutating console endpoint (`POST /api/command`, `/api/scenario/<id>`, `/api/sim`,
`/api/reset`) requires the operator token, sent as `X-Sentinel-Token` or `?token=`.
The token is generated on first run, stored in `data/console-token`, and printed by
`run.sh` and the console log as a ready-to-open link. Set `SENTINEL_CONSOLE_TOKEN`
to choose it yourself. Reads stay open: the console is an observer's screen.

### Modbus TCP

The feeder RTU listens on **5020** (pipeline station: 5021). Coil and register writes
become plant commands exactly as an attacker's would; input registers mirror telemetry.
The RTU's wire tap decodes function codes 1/2/3/4/5/6/15/16 in both directions and
publishes each frame to `grid/modbus/frames`; the console's Timeline shows them under the
*Modbus* filter. The same decoder runs as a transparent proxy in front of any RTU:
`python -m sentinel.plant.modbus --tap 5030 127.0.0.1:5020`.

```bash
python -m sentinel.attacks.modbus_inject coil 0 1            # cb_close, straight to the RTU
python -m sentinel.attacks.modbus_inject register 1 1180     # AVC target 11.80 kV
python -m sentinel.attacks.modbus_inject read                # mirror of live telemetry
```

Register map: `python -m sentinel.plant.modbus --map`.

## 2. Tests and numbers

```bash
make test            # the whole suite (integration tests need a broker on 1883)
make demo            # the four-minute demo, headless, every verdict asserted
make metrics         # regenerate docs/RESULTS.md and the README numbers from a real run
make check-metrics   # what CI runs: fail if the committed numbers drifted
```

`tests/test_grid_acceptance.py` maps one-to-one onto the PRD's acceptance criteria.
`tests/test_grid_integration.py` drives simulator → MQTT → guard on the live broker;
`tests/test_modbus.py` drives the RTU and the tap.

## 3. Architecture

```
  browser  ◄── SSE ──  sentinel.api.app  (Flask, SQLite history, scenario runner, director panel)
                              ▲
              guard/alert · guard/status · guard/assessment
                              │
                      sentinel.guard.run          ← observer only, no write path, SQLite state
                              ▲
        plant/telemetry · plant/command · modbus/frames
                              │
                   Mosquitto MQTT broker
                              ▲
                 ┌────────────┴────────────┐
          sentinel.plant.run        sentinel.attacks
          (power flow + Modbus RTU) (scripted scenarios, Modbus injector)
```

| Module | Role |
|--------|------|
| `sentinel/plant/grid.py` | Feeder physics: forward-backward sweep power flow over two feeders and the tie, per-section loads, fault current, protection, AVC, concurrent faults, permits per section. Accepts *any* well-formed command — like a real controller. |
| `sentinel/plant/modbus.py` | Modbus TCP RTU (FC 1/2/3/4/5/6/15/16) and the passive wire tap. |
| `sentinel/guard/state.py` | The guard's mirror of the feeder, telemetry-integrity tracking, command history, digital-twin expectation, learned baseline. |
| `sentinel/guard/grid_rules.py` | The detection layers for the feeder. Each rule is a pure function returning weighted, human-readable `Finding`s. |
| `sentinel/guard/risk.py` · `grid_risk.py` | Additive risk score, severity bands, narrative per dominant rule. |
| `sentinel/guard/engine.py` | Per-command evaluation, periodic process checks with escalation-aware de-duplication, confidence, persistence. |
| `sentinel/guard/signing.py` | Per-source HMAC envelopes, monotonic counters, nonce cache, freshness window. |
| `sentinel/attacks/grid_scenarios.py` | The nine scripted feeder scenarios. |
| `sentinel/api/` | REST + SSE backend and the vanilla HTML/CSS/JS console (no build step, no network). |
| `sentinel/config.py` | Every threshold and weight, in one place. |

MQTT topics, each prefixed with the process namespace (`grid/` or `pipeline/`):
`plant/telemetry`, `plant/command`, `plant/event`, `plant/mode`, `modbus/frames`,
`guard/alert`, `guard/assessment`, `guard/status` (retained), plus
`plant/sim` (simulator-only hooks: telemetry blackout, fault inject/clear) and
`sentinel/control` (demo reset).

A switching program names the equipment it covers — `switching_program_on` with value
`SP-0417:CB-101,SW-102,TS-201,S1` — and only those steps are excused. Permits-to-work
are per section (`ptw_issue S1`); energising a permitted section is HIGH regardless of program.

Process-specific code lives under `sentinel/domains/` (`grid.py`, `pipeline.py`): each
binds its simulator, rules, narratives, rule catalogue, scenarios and the dashboard
descriptor the console draws itself from. Engine, integrity tracking, risk arithmetic,
signing, API and console shell are shared.

REST: `GET /api/state · /api/status · /api/alerts · /api/events · /api/assessments ·
/api/trend · /api/stats · /api/scenarios · /api/rules · /api/baseline · /api/results ·
/api/export/alerts.csv · /api/stream (SSE)`, `POST /api/command · /api/scenario/<id> ·
/api/scenario/stop · /api/sim · /api/reset` (token required).

## 4. Detection layers and risk model

`Risk = Σ finding weights`, clamped to 0–100. Bands: **LOW · MEDIUM · HIGH · CRITICAL**
at the thresholds in `config.SEVERITY_BANDS`. Every point is traceable to a named
finding in the advisory. The weights live in `config.WEIGHTS` and are shown, with the
learned-versus-configured thresholds, on the console's *Rules* view.

| Rule | Layer | Fires when |
|------|-------|-----------|
| **STATE-001** | State | `cb_close` while a fault indicator is set on a section the close energises, or protection is tripped and not reset |
| STATE-002 | State | `cb_open` with load on the feeder and no alternate path |
| STATE-003 | State | a close that parallels F1 and F2 through TS-201 |
| STATE-004 | State | a close that energises a section under permit-to-work — never excused by a program |
| STATE-005 | State | `sw_open` that isolates the hospital with TS-201 open |
| STATE-006 | State | manual tap step with the busbar already outside statutory limits |
| STATE-007 | State | PV curtailed while the feeder is near its rating |
| ROC-001 | Rate of change | AVC target or tap step far beyond a normal trim |
| **ROC-002** | Rate of change | small target steps whose net movement heads for the statutory limit, with time-to-limit |
| ENV-001 / 002 | Envelope | target outside the statutory band; command with voltage or current already near a limit |
| SEQ-002 / 003 / 004 | Timing, sequence | rapid switching, breaker pumping, known unsafe patterns |
| CMD-001 | Integrity | replayed command: stale timestamp, duplicate id, non-monotonic sequence, bad signature |
| TEL-001 / 002 / 003 | Integrity | stale telemetry, replayed frames, injected frames |
| PHY-001 | Physics | busbar or current disagrees with what tap, source and loads imply |
| SRC-001 | Context | unsigned or unknown source; field host outside a program |
| BASE-001 | Baseline | cadence, value or command mix outside this source's learned history |
| CTX-001 / 002 / 003 | Context | program covers the step; restoration after a cleared fault; alternate path available (negative weights) |

## 5. What Sentinel does when it is unsure

Every advisory carries a **confidence** (HIGH / REDUCED / LOW) and a plain statement of
what could not be verified. When telemetry is stale or replayed, when instruments disagree
with the physics, or when there is no telemetry at all, Sentinel **still raises the
advisory** — integrity findings add to the score rather than suppress it — marks it
LOW/REDUCED confidence, says exactly why, and asks for the network state to be confirmed
by field indication. It never blocks: safe-direction commands (opening a faulted breaker,
cancelling a permit, AVC to AUTO) are never flagged alone, and the guard has no write path
to any switchgear or setpoint. The same policy is on the console's *Rules* view.

## 6. Design decisions worth knowing

See [docs/DECISIONS.md](docs/DECISIONS.md) for the log. The short list:

- **The guard has no write path.** It subscribes and publishes advisories;
  `tests/test_grid_acceptance.py::test_guard_never_operates_switchgear` pins this.
- **Deterministic before clever.** Additive weights an engineer can follow; the learned
  baseline only adds a small, explained contribution.
- **Controller restart ≠ replay.** A lower sequence number with a *newer* timestamp is a
  counter restart; only an older timestamp counts as replay.
- **Instrument lag is not an anomaly.** The physics-residual rule needs the mismatch to persist.
- **Escalation re-alerts immediately.** A persisting condition re-alerts every 30 s — unless
  its severity rises, in which case the engineer hears about it at once.
- **A real plant has no narrator.** Scenario narration is off on the operator timeline by
  default and lives in the presenter's director panel.
