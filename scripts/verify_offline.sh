#!/usr/bin/env bash
# V5: the console must load with no outbound network at all.
#  1. static assets reference no external origin;
#  2. the API sends a CSP that forbids external origins;
#  3. headless Chromium with every non-localhost name resolved to NOTFOUND renders the page
#     with the self-hosted fonts and no console errors.
set -u
cd "$(dirname "$0")/.."
URL="${1:-http://localhost:8080}"
fail=0
ext=$(grep -rnoE 'https?://[a-zA-Z0-9./_-]+' sentinel/api/static | grep -v 'www.w3.org/2000/svg' | grep -v 'localhost' || true)
if [ -n "$ext" ]; then echo "external references in static assets:"; echo "$ext"; fail=1; else echo "static assets: no external origins"; fi
csp=$(curl -s -D - -o /dev/null "$URL/" | grep -i '^content-security-policy' || true)
case "$csp" in *"default-src 'self'"*) echo "CSP: $csp" ;; *) echo "CSP missing on $URL"; fail=1 ;; esac
if command -v chromium >/dev/null 2>&1; then
  out=$(mktemp -d)
  timeout 60 chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
    --host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost" \
    --user-data-dir="$out/profile" --enable-logging=stderr --v=0 --window-size=1280,900 --timeout=8000 \
    --screenshot="$out/offline.png" "$URL/?token=x" 2>"$out/log" >/dev/null
  errs=$(grep -iE 'ERR_|Failed to load resource|Refused to' "$out/log" | grep -v 'ERR_NAME_NOT_RESOLVED.*gcm\|registration_request\|DEPRECATED_ENDPOINT' || true)
  if [ -s "$out/offline.png" ] && [ -z "$errs" ]; then echo "offline render: ok ($out/offline.png)"; else echo "offline render failed:"; echo "$errs"; fail=1; fi
else
  echo "chromium not installed: skipped the blocked-network render"
fi
exit $fail
