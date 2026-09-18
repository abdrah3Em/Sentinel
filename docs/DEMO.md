# The four-minute demo

Grid console, one screen, no slides. Everything below is replayed and asserted by `make demo`
(headless) and `make demo-live` (against the running console), so the demo is a test, not a hope.

**A real plant has no narrator.** Scenario narration is off on the operator timeline in judge mode;
the presenter says what is happening. The director panel (top bar → *Director*) shows the script
privately if needed.

## Before you start (T−5:00)

```bash
./run.sh                      # grid console on http://localhost:8080; the operator token is printed
make demo-live                # rehearsal: drives the console through the sequence and asserts verdicts
```
Open the console with the printed `?token=…` link. Overview view, dark theme, Reset demo.

## Script

| Time | Say | Do | Watch |
|---|---|---|---|
| **T+0:00** | "This is feeder F1: 33/11 kV transformer, breaker CB-101, sectionaliser, a tie to F2, PV, and a hospital on bus B3. Sentinel sees every command and every telemetry frame — Modbus TCP and MQTT — and has no write path to any of it." | Point at posture NORMAL, busbar 11.0 kV, 180 A. | Nothing happens. That is the point. |
| **T+0:30** | "Normal traffic first." | Scenarios → **9 Normal operations** → Run. | AVC trim, PV curtail: quiet. |
| **T+1:00** | "Now the one that matters. A fault on section S2." | Scenarios → **1 Close onto a standing fault** → Run. Back to Overview. | Protection trips CB-101. Hospital bus dead. FI-2 lit on the mimic. |
| **T+1:20** | "An engineering laptop sends `cb_close`. Same command an operator issues fifty times a week. Valid. Authorised by the protocol." | — | Posture → **CRITICAL 95**. Advisory: *Breaker close onto an uncleared fault*. |
| **T+1:40** | "Read it: FI-2 set, protection tripped 8 s ago and not reset, hospital downstream, no switching program, source not in the control room. Do this: confirm the crew, reset protection, close under an instruction." | Open the advisory. | Orange flash on CB-101: 6.5 kA for 150 ms, re-trip, stress counter. |
| **T+2:20** | "Same command, different moment." | Wait; the scenario clears the fault, resets protection, closes. | Quiet. All buses back. |
| **T+2:50** | "Now the attacker hides the picture." | Scenarios → **5 Telemetry & command replay** → Run. | Sequence counter stops. Telemetry tag **replay suspected**. Confidence **LOW** on every advisory. |
| **T+3:20** | "Behind the frozen frame the sectionaliser opens. The display says the hospital is supplied. Sentinel says it cannot verify that, raises the advisory anyway, and asks for local confirmation." | — | STATE-005 at LOW confidence; CMD-001 on the replayed command. |
| **T+3:50** | "Advisory only. The engineer decides. Same engine, second process, one flag." | Optional: sidebar → *Pipeline simulation*. | 20 seconds, no more. |

## Fallback (30 seconds, no console)

If the laptop misbehaves:

```bash
make demo
```

prints the same sequence as a timestamped PASS/FAIL table from the offline harness — the
commands, the physics and the verdicts are identical to the live run. Read the CRITICAL line aloud.
If Python itself is gone, [RESULTS.md](RESULTS.md) has the same table committed from the last run.

## Reset between runs

Top bar → **Reset demo** (simulator, guard and timeline back to nominal), or `POST /api/reset` with the token.
