# Threat model

## What Sentinel is

An advisory command guard for a distribution feeder's control system. It observes every
command and every telemetry frame — over Modbus TCP (the RTU's native protocol, through a
passive wire tap) and MQTT — reconstructs the physical state of the network, and tells the
control engineer when a protocol-valid command does not make physical sense right now.

**It has no write path.** The guard subscribes and publishes advisories. It cannot open or
close a breaker, move a tap, or change a setpoint. `tests/test_grid_acceptance.py::test_guard_never_operates_switchgear`
pins this. The human holds the decision because, in this domain, blocking a genuine safety
command (opening a faulted breaker) can cause the outage the guard was meant to prevent.

## Trust boundary

| Inside (trusted to be what it says) | Outside (assumed hostile or compromised) |
|---|---|
| The guard process and its SQLite state | Anything that can reach the broker or the RTU: engineering laptops, field hosts, jump servers, the "attacker" in every scenario |
| The signing master secret (`data/signing-master`, mode 600) and the keys derived from it | Any command that arrives unsigned, badly signed, out of sequence, with a reused nonce, or outside the freshness window |
| The physics model and the rule weights in `config.py` | The telemetry path: frames can be delayed, frozen, replayed or injected |
| The console's operator token (`data/console-token`) | The console's network: every mutating endpoint needs the token; reads are open by design (it is an observer's screen) |
| The broker's process boundary on the demo host | The broker's clients: a client that can publish on `plant/command` is an attacker for the purposes of this model |
| The RTU's wire tap (in-process, or the transparent proxy) | The Modbus TCP client: any host that can reach port 5020 can write coils and registers, exactly as the brief's controllers allow; those writes arrive unsigned with source `modbus:<ip>` |

## What each layer defends against, and what defeats it

| Threat | Defence | Defeats it | Residual risk stated honestly |
|---|---|---|---|
| A command that nobody in the control room issued | Signed envelopes (per-source HMAC key, `sentinel/signing.py`); SRC-001 charges a keyed source that cannot prove its identity | Holding a valid key | **A compromised engineering workstation holds a valid key.** Its commands verify. That is why the physics and wrong-moment rules never consult the signature: STATE-001 fires on a perfectly signed `cb_close` onto a fault. |
| Replay of recorded traffic | Per-source monotonic sequence, nonce cache, freshness window, MAC over seq+ts so rewriting them breaks it; CMD-001 also catches duplicate ids and stale timestamps independently | Nothing short of the key; a rewritten envelope fails the MAC, a verbatim one fails seq/nonce/time | Guard restart within the freshness window: counters and nonces are persisted in SQLite so a restart does not open a window. |
| Replayed or frozen telemetry | TEL-002 (sequence not advancing), TEL-003 (sequence regression with an older timestamp), TEL-001 (age), PHY-001 (frame disagrees with the physics); every advisory raised meanwhile is marked **confidence LOW** and says what could not be verified | An attacker who forges *consistent* telemetry that also satisfies the physics model | Telemetry frames are not signed in this build. The guard therefore never trusts telemetry as proof; it asks for field indication and keeps raising advisories at LOW confidence. |
| Valid command at the wrong moment (the brief's hardest case) | State rules over the reconstructed network: fault indicators, protection latch, supply paths, permits, programs, tap and voltage limits | A wrong picture of the network (see above) | Confidence is downgraded whenever the picture is untrusted; the advisory still goes out. |
| Slow setpoint drift | ROC-002 over the drift window with time-to-limit; learned baseline ranges (BASE-001) | Drift slower than the window, or a baseline poisoned during learning | Windows and thresholds are in `config.py`; the console shows learned versus configured so an operator can see a poisoned baseline. |
| Alert fatigue used as cover | Escalation-aware de-duplication: a persisting condition re-alerts every 30 s, immediately if it worsens | — | A flood of distinct low-value advisories is still possible; severity bands keep them out of the dispatcher. |
| Tampering with the console | Operator token on every mutating endpoint; CSP forbids every external origin; no inline script | Token theft from the URL bar or a shared screen | The token is a shared secret for a demo host, not an identity system. Do not expose the console beyond the control-room network. |

## What Sentinel does not defend against

- **Physical compromise of the RTU or its firmware.** The guard believes the RTU's telemetry unless the physics disagrees.
- **A wholly consistent forged world.** If an attacker can forge telemetry that satisfies the power-flow model *and* sign commands with a valid key, the guard sees a legitimate operation. Mitigation is outside this system: telemetry signing at the RTU and key custody.
- **Denial of service on the broker or the RTU.** Sentinel raises TEL-001 when frames stop; it cannot restore them.
- **Insider misuse with a valid key inside a valid program.** A control engineer who declares a switching program that names CB-101 and then opens it has done something Sentinel calls expected. Programs are named per equipment to keep that window small; energising a section under permit is HIGH regardless.
- **Attacks on the demo host itself.** The console, the guard and the simulator share a machine; local privilege is total.

## Why the weights are what they are

A missing signature scores 30, a replayed envelope 40, a wrong-moment close onto a fault 40+20+15. The identity and freshness evidence is deliberately not enough on its own to reach CRITICAL: the point of the guard is the physical consequence, and the score should say so. Every point is listed on the advisory.
