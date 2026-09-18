"""Refresh docs/img from a running console: light and dark, desktop and phone.

    python -m sentinel.screenshots [--url http://localhost:8080] [--token ...]

Needs chromium on the PATH.  Runs the flagship scenario first so the shots show
the console doing its job, then captures every view in both themes and the
overview at phone width.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "docs", "img")
SHOTS = [
    ("ui-grid-overview.png", "#overview", "dark", (1440, 1750)),
    ("ui-grid-overview-light.png", "#overview", "light", (1440, 1750)),
    ("ui-grid-overview-phone.png", "#overview", "dark", (400, 2200)),
    ("ui-grid-overview-phone-light.png", "#overview", "light", (400, 2200)),
    ("ui-advisories.png", "#advisories", "dark", (1440, 1300)),
    ("ui-timeline.png", "#timeline", "dark", (1440, 1100)),
    ("ui-scenarios.png", "#scenarios", "dark", (1440, 1200)),
    ("ui-rules.png", "#rules", "dark", (1440, 1600)),
]


def post(base: str, path: str, token: str) -> None:
    req = urllib.request.Request(base + path, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Sentinel-Token": token})
    urllib.request.urlopen(req, timeout=5).read()


def shoot(base: str, token: str, name: str, view: str, theme: str, size: tuple[int, int]) -> str:
    out = os.path.join(IMG, name)
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(["chromium", "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}", "--timeout=8000",
                        f"--screenshot={out}", f"{base}/?token={token}&theme={theme}{view}"],
                       capture_output=True, timeout=90)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    token = args.token or open(os.path.join(ROOT, "data", "console-token")).read().strip()
    os.makedirs(IMG, exist_ok=True)
    post(args.url, "/api/reset", token)
    time.sleep(3)
    post(args.url, "/api/scenario/close_onto_fault", token)
    time.sleep(10)
    for name, view, theme, size in SHOTS:
        out = shoot(args.url, token, name, view, theme, size)
        ok = os.path.exists(out) and os.path.getsize(out) > 20000
        print(f"{'ok  ' if ok else 'FAIL'} {name} {theme} {size[0]}x{size[1]}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
