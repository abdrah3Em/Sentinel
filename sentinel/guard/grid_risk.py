"""Advisory narratives for the distribution feeder (PRD revision 2, section 26).

What happened, which equipment, why it matters, what to verify — in the words
of a control-room engineer.  Integrity narratives (CMD-001, TEL-00x) are shared
with the pipeline pump station in risk.py.
"""
from __future__ import annotations

NARRATIVES: dict[str, dict[str, str]] = {
    "STATE-001": {
        "summary": "Breaker close onto an uncleared fault",
        "equipment": "CB-101 / faulted section / Hospital bus B3",
        "why": "Protection tripped for a fault that is still on the section; closing now drives "
               "{fault_ka:.1f} kA into it — switchgear damage, a re-trip, and a hazard to anyone on the section.",
        "recommendation": "Confirm the crew has cleared the fault, reset protection, then close under a "
                          "switching instruction; if nobody issued this close, treat the source as compromised.",
    },
    "STATE-002": {
        "summary": "Feeder breaker open with load and no alternate supply",
        "equipment": "CB-101 / all downstream buses",
        "why": "CB-101 carries the whole feeder and TS-201 is open: every bus, including the hospital, goes dark.",
        "recommendation": "Transfer load through TS-201 first if the feeder must come off; otherwise treat "
                          "the source as compromised.",
    },
    "STATE-003": {
        "summary": "Uncontrolled parallel between feeders F1 and F2",
        "equipment": "TS-201 / CB-101 / SW-102",
        "why": "With all three closed the feeders form a loop: circulating current and protection that no longer grades.",
        "recommendation": "Confirm a switching program covers the transfer and break the parallel within a minute.",
    },
    "STATE-004": {
        "summary": "Energising a section under permit-to-work",
        "equipment": "Permitted section / crew",
        "why": "A crew is declared on the conductors; energising is a live-line hazard regardless of any program.",
        "recommendation": "Do not close; confirm the crew is clear and the permit cancelled first.",
    },
    "STATE-005": {
        "summary": "Sectionaliser open isolates the hospital",
        "equipment": "SW-102 / Hospital bus B3",
        "why": "With TS-201 open the hospital is fed only through SW-102; opening it drops B2 and B3.",
        "recommendation": "Close TS-201 to feed B2 and B3 from F2 before opening SW-102.",
    },
    "STATE-006": {
        "summary": "Manual tap step with the busbar already outside statutory limits",
        "equipment": "T1 OLTC / 11 kV busbar BB-101",
        "why": "The busbar is already outside ±6 %; another tap the same way pushes every customer further out.",
        "recommendation": "Return the AVC to AUTO with a target inside {vmin:.2f}–{vmax:.2f} kV and trace the tap commands.",
    },
    "STATE-007": {
        "summary": "PV curtailed while the feeder is near its rating",
        "equipment": "PV-1 / CB-101",
        "why": "PV-1 is carrying part of the load; curtailing it near 400 A pushes the feeder toward an overload trip.",
        "recommendation": "Confirm the constraint; otherwise restore the inverter setpoint.",
    },
    "ROC-001": {
        "summary": "Unusually large voltage-target change",
        "equipment": "AVC / T1 OLTC",
        "why": "One large step drives the tap changer through several positions and can leave statutory limits.",
        "recommendation": "Confirm the target; if required, ramp it in steps and watch the bus voltages.",
    },
    "ROC-002": {
        "summary": "Voltage target is being walked toward the statutory limit",
        "equipment": "AVC / 11 kV busbar BB-101",
        "why": "Each trim looks routine, but the AVC is tapping the busbar toward the statutory limit — "
               "how an attacker takes a network out of limits without one command looking wrong.",
        "recommendation": "Compare with the shift log; if nobody owns the trajectory, restore the logged "
                          "target and trace the source.",
    },
    "ENV-001": {
        "summary": "Requested target is outside the statutory voltage band",
        "equipment": "11 kV busbar BB-101",
        "why": "The value puts the busbar outside {vmin:.2f}–{vmax:.2f} kV for every customer on the feeder.",
        "recommendation": "Reject or correct the target and verify who issued it.",
    },
    "ENV-002": {
        "summary": "Command issued while the feeder is already near a limit",
        "equipment": "11 kV busbar BB-101 / CB-101",
        "why": "Voltage or current is already near its limit; this command removes the remaining margin.",
        "recommendation": "Bring the feeder back inside limits before making further changes.",
    },
    "SEQ-002": {
        "summary": "Abnormally rapid switching sequence",
        "equipment": "Control room / switchgear",
        "why": "Switching commands are arriving faster than a control room issues them — the signature of a script.",
        "recommendation": "Identify the source of the burst before allowing further switching.",
    },
    "SEQ-003": {
        "summary": "Breaker pumping — the same device opened and closed within seconds",
        "equipment": "Switchgear",
        "why": "Repeated open/close cycles wear the mechanism and can defeat protection.",
        "recommendation": "Check for a fighting automation, a stuck HMI or an external source repeating commands.",
    },
    "SEQ-004": {
        "summary": "Known unsafe switching pattern observed",
        "equipment": "Feeder F1",
        "why": "The recent command sequence matches a known multi-step path into an unsafe configuration.",
        "recommendation": "Review the full sequence and its source before allowing further switching.",
    },
    "PHY-001": {
        "summary": "Reported feeder values do not match the physics",
        "equipment": "Feeder instrumentation",
        "why": "Tap, source and load say the busbar and current should read differently: a failed "
               "instrument or manipulated values.",
        "recommendation": "Cross-check the busbar voltage against tap position and the ammeter against load.",
    },
    "SRC-001": {
        "summary": "Command from an unexpected source",
        "equipment": "Command source",
        "why": "Not a recognised control-room source, and no switching program authorises field hosts now.",
        "recommendation": "Confirm which workstation issued the command and whether that access is expected.",
    },
    "CTX-001": {
        "summary": "Planned switching — expected in context",
        "equipment": "Switching program",
        "why": "A switching program covering this equipment is in force; the step is normal work.",
        "recommendation": "Confirm the program covers this step.",
    },
    "CTX-002": {
        "summary": "Restoration after a cleared fault — expected",
        "equipment": "CB-101",
        "why": "The fault was cleared and protection reset; this close is the restoration step.",
        "recommendation": "Confirm the field crew reported clear before the close.",
    },
    "CTX-003": {
        "summary": "Load transferred — alternate path available",
        "equipment": "TS-201",
        "why": "TS-201 is closed, so opening CB-101 transfers load to F2 rather than dropping it.",
        "recommendation": "Confirm the transfer is planned and break the parallel afterwards.",
    },
}
