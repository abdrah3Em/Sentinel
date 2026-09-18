"""Every scripted scenario, both processes, replayed offline: attacks must reach their
expected level; legitimate operations must produce nothing above LOW."""
import pytest

from sentinel import evaluate, process


@pytest.mark.parametrize("process_id", list(process.KNOWN))
def test_all_scenarios_meet_their_expected_verdict(process_id):
    rows = evaluate.evaluate(process_id)
    assert rows, "no scenarios"
    failures = [(r["id"], r["expect"], r["worst_level"], r["worst_rule"], r["above_low"]) for r in rows if not r["passed"]]
    assert not failures, failures


def test_replay_scenarios_mark_confidence_low():
    for process_id in process.KNOWN:
        rows = {r["id"]: r for r in evaluate.evaluate(process_id)}
        replay = rows["telemetry_replay"]
        assert replay["low_confidence"] >= 1, replay
