"""Unit tests for the notification dispatcher."""
from sentinel.guard import dispatcher
from sentinel.models import Alert, Command


def test_format_alert_text():
    alert = Alert(
        level="CRITICAL",
        rule="SEQ-001",
        score=85,
        summary="Outlet valve close conflicts with running pump",
        equipment="Outlet valve V-102",
        why="Dead-head condition",
        recommendation="Verify pump status",
        command=Command("outlet_close", source="maintenance-laptop").to_dict(),
        confidence="HIGH",
    )
    text = dispatcher.format_alert_text(alert)
    assert "CRITICAL" in text
    assert "SEQ-001" in text
    assert "Outlet valve V-102" in text
    assert "maintenance-laptop" in text
    assert "Sentinel advises only" in text


def test_dispatch_threshold_respects_level():
    # Should not raise any exceptions even if no webhook is configured
    alert_low = Alert(
        level="LOW",
        rule="INFO-001",
        score=10,
        summary="Low severity event",
        equipment="Sensor",
        why="",
        recommendation="",
    )
    dispatcher.dispatch_alert(alert_low)

    alert_crit = Alert(
        level="CRITICAL",
        rule="SEQ-001",
        score=85,
        summary="Critical event",
        equipment="Pump",
        why="Deadhead",
        recommendation="Stop pump",
    )
    dispatcher.dispatch_alert(alert_crit)
