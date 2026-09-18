"""Signed command envelopes: who really issued this, and is it fresh?

Every source that is allowed to command the plant holds an HMAC key derived from
a master secret (``data/signing-master``, generated once).  A command carries a
per-source monotonic ``seq``, a random ``nonce`` and ``sig`` =
HMAC-SHA256(key, source|seq|ts|nonce|id|action|value).  The guard's Verifier
checks the signature, that ``seq`` only ever increases per source, that the
nonce has not been seen, and that ``ts`` is inside the freshness window.
Rewriting seq or ts to dodge replay detection breaks the MAC.

Residual risk (see docs/THREAT-MODEL.md): a compromised workstation holds a
valid key.  Its commands verify — which is why the physics and wrong-moment
rules are independent of everything in this file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Optional

from . import config

FIELDS = ("source", "seq", "ts", "nonce", "id", "action", "value")
NONCE_CACHE = 5000
_lock = threading.Lock()
_seq: dict[str, int] = {}
_master: Optional[bytes] = None


def keyed_sources() -> set[str]:
    return set(config.TRUSTED_SOURCES) | set(config.MAINTENANCE_SOURCES) | set(config.GRID_TRUSTED_SOURCES) \
        | set(config.GRID_PROGRAM_SOURCES) | set(config.EXTRA_SIGNING_SOURCES)


def master_secret() -> bytes:
    """Read or create the master secret every Sentinel process on this data dir shares."""
    global _master
    if _master is not None:
        return _master
    env = os.environ.get("SENTINEL_SIGNING_MASTER", "").strip()
    if env:
        _master = env.encode()
        return _master
    path = config.SIGNING_MASTER_PATH
    try:
        with open(path) as f:
            text = f.read().strip()
        if text:
            _master = text.encode()
            return _master
    except OSError:
        pass
    text = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text + "\n")
    except OSError:
        pass
    _master = text.encode()
    return _master


def key_for(source: str) -> Optional[bytes]:
    if source not in keyed_sources():
        return None
    return hmac.new(master_secret(), source.encode(), hashlib.sha256).digest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps([payload.get(f) for f in FIELDS], separators=(",", ":"), sort_keys=True).encode()


def _mac(key: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def sign(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a signed copy of a command dict, or the dict unchanged if the source holds no key."""
    key = key_for(str(payload.get("source", "")))
    if key is None:
        return dict(payload)
    with _lock:
        source = payload["source"]
        # Monotonic across processes that sign as the same source: time-based, never repeats locally.
        nxt = max(_seq.get(source, 0) + 1, int(time.time() * 1000))
        _seq[source] = nxt
    out = dict(payload, seq=nxt, nonce=secrets.token_hex(8))
    out["sig"] = _mac(key, out)
    return out


class Verifier:
    """Guard-side state: last sequence per source, nonces seen, and the verdict per command."""

    def __init__(self) -> None:
        self.last_seq: dict[str, int] = {}
        self.nonces: OrderedDict[str, int] = OrderedDict()
        self.history: Deque[dict[str, Any]] = deque(maxlen=200)

    def verify(self, payload: dict[str, Any], now: float) -> dict[str, Any]:
        source = str(payload.get("source", ""))
        result = {"source": source, "status": "ok", "detail": "", "keyed": source in keyed_sources()}
        key = key_for(source)
        if key is None:
            result.update(status="unkeyed", detail=f"'{source}' holds no signing key")
            return self._done(result)
        sig, seq, nonce = payload.get("sig"), payload.get("seq"), payload.get("nonce")
        if not sig or seq is None or not nonce:
            result.update(status="unsigned", detail=f"claims '{source}' but carries no signature")
            return self._done(result)
        if not hmac.compare_digest(str(sig), _mac(key, payload)):
            result.update(status="bad", detail=f"signature does not verify for '{source}' — forged or altered")
            return self._done(result)
        problems = []
        last = self.last_seq.get(source)
        if last is not None and int(seq) <= last:
            problems.append(f"sequence {seq} is not after {last} for '{source}'")
        if nonce in self.nonces:
            problems.append(f"nonce {nonce} already seen")
        age = now - float(payload.get("ts", 0)) / 1000.0
        if abs(age) > config.CMD_STALE_S:
            problems.append(f"timestamp {age:+.0f} s outside the {config.CMD_STALE_S:.0f} s freshness window")
        if problems:
            result.update(status="replay", detail="; ".join(problems))
            return self._done(result)
        self.last_seq[source] = int(seq)
        self.nonces[nonce] = int(now)
        while len(self.nonces) > NONCE_CACHE:
            self.nonces.popitem(last=False)
        return self._done(result)

    def _done(self, result: dict[str, Any]) -> dict[str, Any]:
        self.history.append(result)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"last_seq": dict(self.last_seq), "nonces": list(self.nonces.keys())[-NONCE_CACHE:]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Verifier":
        v = cls()
        v.last_seq.update({k: int(x) for k, x in (data.get("last_seq") or {}).items()})
        for n in data.get("nonces") or []:
            v.nonces[n] = 0
        return v
