# Decisions

Each entry: what was decided, what was rejected, why. Newest last.

## D1 — Pipeline profile reuses the liquid-transfer engine
**Decided:** the crude oil pipeline pump station keeps the tank/pump/valve physics and the `tank_level`, `pump`, `outlet_valve`, `inlet_valve`, `setpoint` field names; only the domain module, narratives, labels and scenarios are re-skinned (tank farm level, mainline pump P-101, MOV-201, ESD-301, DRA rate).
**Rejected:** renaming every field and action (`mov_close`, `esd_trip`) through rules, tests and scenarios.
**Why:** the detector core must not change during hardening; the brief judges the detector, not the naming of a second process.

## D2 — DRA injection is a real actuator, not a relabelled setpoint
**Decided:** `dra_rate` (0–100 %) is a new command and telemetry field; it raises mainline throughput by up to 12 % (friction reduction). The level setpoint stays the level setpoint.
**Rejected:** relabelling the level setpoint as "DRA rate".
**Why:** the setpoint drives the level controller; calling it DRA would make the drift scenario physically meaningless.

## D3 — Process id is `pipeline`, selected by `--profile`
**Decided:** `SENTINEL_PROCESS=pipeline` and `./run.sh --profile pipeline`; grid is the default everywhere.
**Rejected:** keeping the interim id `oil`.
**Why:** the checklist names the flag; one word for the profile everywhere avoids a judge seeing two names.

## D4 — The passive Modbus tap is a transparent TCP proxy, plus an in-process decoder
**Decided:** `ModbusTap` decodes any Modbus/TCP byte stream (FC 1/2/3/4/5/6/15/16, both directions, resyncs on junk). The plant's RTU feeds it in-process and publishes every frame on `<ns>/modbus/frames`; `python -m sentinel.plant.modbus --tap` runs the same decoder as a transparent proxy in front of any RTU, forwarding bytes untouched.
**Rejected:** a raw-socket/pcap sniffer.
**Why:** raw sockets need root and libpcap; a proxy tap is passive with respect to the frames, works in a container, and the decoder is the same code either way.

## D5 — Base images pinned by digest, dependencies by hash
**Decided:** `python@sha256:78387bc3…` and `eclipse-mosquitto@sha256:38c0da4f…` (the manifest-list digests fetched from Docker Hub on 2026-09-18), `pip install --require-hashes` with `scripts/pin_requirements.sh` regenerating the file.
**Rejected:** floating tags.
**Why:** a judge's `docker compose up` must build what we tested.

## D6 — Fault current depends on where the fault is
**Decided:** the source contributes `E/√3 / |Z_source + Z_path|`, with Z_path the sections from the breaker to the middle of the faulted section, so a close onto S2 draws ~3.2 kA and a bolted busbar fault 6.5 kA. Narratives say "kilo-amps"; the advisory and the mimic show the computed value.
**Rejected:** a constant 6.5 kA everywhere.
**Why:** the power-flow model exists; showing the same number for every location would contradict it.

## D7 — The pipeline profile is a compose profile, not a default service
**Decided:** `docker compose up` brings up the feeder only; `docker compose --profile pipeline up` adds the pump station. Locally `./run.sh --profile pipeline|both`.
**Rejected:** two consoles by default.
**Why:** one flagship (A9); the second process is a twenty-second portability proof.

## D8 — Docker could not be executed on the build machine
**Decided:** compose and Dockerfile were validated by parsing and by the digests/hashes they pin; the fresh-clone `docker compose up` run (V3) is recorded as not proven here. The same services run from `./run.sh`, the healthcheck commands were executed by hand against the running services.
**Rejected:** claiming V3 passed.
**Why:** no Docker or Podman on the machine and no privilege to install one.
