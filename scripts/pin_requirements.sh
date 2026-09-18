#!/usr/bin/env bash
# Regenerate requirements.txt (every PyPI file hash for each pinned version, so the same
# file installs on any CPython 3.10+/platform) and requirements.lock from the direct
# dependencies.  Needs network.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import json, urllib.request
direct = {"paho-mqtt": "2.1.0", "flask": "3.1.3", "pytest": "9.0.3"}
# transitive closure resolved once from the direct pins (see requirements.lock)
pins = dict(direct, **{"blinker": "1.9.0", "click": "8.5.0", "iniconfig": "2.3.0", "itsdangerous": "2.2.0",
                       "jinja2": "3.1.6", "markupsafe": "3.0.3", "packaging": "26.3", "pluggy": "1.6.0",
                       "pygments": "2.21.0", "werkzeug": "3.1.8"})
lines = ["# Pinned; every PyPI file hash for each version so the same artefacts install on any",
         "# supported CPython/platform.  Regenerate with scripts/pin_requirements.sh.",
         "# Install: pip install --require-hashes -r requirements.txt", ""]
for name, version in sorted(pins.items()):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    hashes = sorted({f["digests"]["sha256"] for f in files if not f.get("yanked")})
    if name not in direct:
        lines.append("# transitive")
    lines.append(f"{name}=={version} \\")
    lines.extend(f"    --hash=sha256:{h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(hashes))
open("requirements.txt", "w").write("\n".join(lines) + "\n")
open("requirements.lock", "w").write("\n".join(f"{n}=={v}" for n, v in sorted(pins.items())) + "\n")
print(f"pinned {len(pins)} packages")
PY
