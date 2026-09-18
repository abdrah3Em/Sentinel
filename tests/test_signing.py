"""Signed command envelopes: forged identities and replayed traffic are caught before any physics."""
from sentinel import config, signing
from sentinel.models import Command
from tests.helpers import Rig


def rules_of(alert):
    return {f["rule"] for f in alert.findings} if alert else set()


def details_of(alert):
    return " ".join(f["detail"] for f in alert.findings) if alert else ""


def test_keyed_sources_sign_and_unkeyed_sources_do_not():
    signed = Command(action="pump_stop", source="operator-hmi").to_dict()
    assert {"seq", "nonce", "sig"} <= set(signed)
    plain = Command(action="pump_stop", source="unknown-host").to_dict()
    assert "sig" not in plain


def test_verifier_accepts_fresh_signed_and_rejects_altered():
    v = signing.Verifier()
    env = Command(action="setpoint", source="operator-hmi", value=60).to_dict()
    now = env["ts"] / 1000.0
    assert v.verify(env, now)["status"] == "ok"
    tampered = dict(env, value=95)                       # change the payload, keep the MAC
    assert v.verify(tampered, now)["status"] == "bad"
    rewritten = dict(env, seq=env["seq"] + 1, ts=env["ts"] + 5000)   # rewrite seq and ts to look fresh
    assert v.verify(rewritten, now + 5)["status"] == "bad"


def test_replayed_valid_command_is_rejected_by_sequence_nonce_and_freshness():
    v = signing.Verifier()
    env = Command(action="cb_close", source="operator-hmi").to_dict()
    now = env["ts"] / 1000.0
    assert v.verify(env, now)["status"] == "ok"
    again = v.verify(dict(env), now + 2)                 # verbatim replay two seconds later
    assert again["status"] == "replay" and "sequence" in again["detail"] and "nonce" in again["detail"]
    later = Command(action="cb_close", source="operator-hmi").to_dict()
    assert v.verify(dict(later), now + 120)["status"] == "replay"      # fresh envelope, stale by the clock


def test_replayed_valid_command_scores_high_through_the_guard():
    rig = Rig()
    rig.send("outlet_open", settle=2)
    captured = rig.last_command                          # a perfectly valid, signed operator command
    alert = rig.replay_command(captured)                 # byte-for-byte, two seconds later
    assert alert is not None and "CMD-001" in rules_of(alert)
    assert "sequence" in details_of(alert) and "nonce" in details_of(alert)
    assert config.SEVERITY_ORDER[alert.level] >= config.SEVERITY_ORDER["HIGH"]
    rig.advance(2)
    rewritten = rig.replay_command(captured, age_s=300)  # timestamp rewritten as well: the MAC breaks
    assert rewritten is not None and "does not verify" in details_of(rewritten)
    assert config.SEVERITY_ORDER[rewritten.level] >= config.SEVERITY_ORDER["HIGH"]


def test_spoofed_trusted_source_without_the_key_is_rejected():
    rig = Rig()
    forged = Command(action="outlet_open", source="operator-hmi").to_unsigned_dict()
    forged["ts"] = int(rig.now * 1000)
    alert = rig.guard.observe_command(forged, now=rig.now)
    assert alert is not None and "SRC-001" in rules_of(alert)
    assert "no signature" in details_of(alert)
    bad = Command(action="outlet_open", source="operator-hmi").to_dict()
    bad["sig"] = "00" * 32
    bad["id"] = "forged-2"
    alert = rig.guard.observe_command(bad, now=rig.now)
    assert alert is not None and "does not verify" in details_of(alert)


def test_genuine_signed_operator_command_carries_no_signature_finding():
    rig = Rig()
    alert = rig.send("inlet_open")
    assert alert is None or not {"SRC-001"} & rules_of(alert)
    assessment = rig.guard.assessments[-1]
    assert assessment["signature"] == "ok"


def test_verifier_state_survives_a_snapshot():
    v = signing.Verifier()
    env = Command(action="tie_open", source="operator-hmi").to_dict()
    now = env["ts"] / 1000.0
    v.verify(env, now)
    restored = signing.Verifier.from_dict(v.to_dict())
    assert restored.verify(dict(env), now + 1)["status"] == "replay"
