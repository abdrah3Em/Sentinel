"""Which simulated process Sentinel is guarding.

Everything process-specific — physics, rules, narratives, scenarios and the
dashboard descriptor — lives in one module per process under ``sentinel.domains``.
The engine, integrity tracking, risk arithmetic, MQTT plumbing, API and dashboard
shell are shared and only ever ask ``domain()`` for what they need.
"""
from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Optional

from . import config

KNOWN = ("grid", "pipeline")
_active: Optional[str] = None


def active_id() -> str:
    pid = _active or config.PROCESS
    return pid if pid in KNOWN else "grid"


def use(process_id: str) -> None:
    """Select the process at runtime (tests, tooling).  Services use SENTINEL_PROCESS."""
    if process_id not in KNOWN:
        raise ValueError(f"unknown process '{process_id}', expected one of {KNOWN}")
    global _active
    _active = process_id


def domain() -> ModuleType:
    return import_module(f"sentinel.domains.{active_id()}")


CONSOLE_LABELS = {"grid": "Grid simulation", "pipeline": "Oil pipeline simulation"}


def consoles() -> list[dict]:
    """The consoles a dashboard can switch between — one per simulated process."""
    return [{"id": pid, "label": CONSOLE_LABELS[pid], "port": config.CONSOLE_PORTS[pid],
             "active": pid == active_id()} for pid in KNOWN]
