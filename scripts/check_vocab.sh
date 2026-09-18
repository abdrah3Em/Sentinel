#!/usr/bin/env bash
# V9: the legacy process vocabulary must not appear anywhere outside git history.
# The pattern is spelled with brackets so this script is not itself a hit.
set -u
cd "$(dirname "$0")/.."
pattern='w[a]ter|chlor[i]ne|\bW[T]P\b|pot[a]ble|reserv[o]ir|clarif[i]er'
hits=$(grep -rniE "$pattern" . 2>/dev/null \
  | grep -v '^./\.git/' | grep -v '\.png:' | grep -v '\.db' | grep -v '__pycache__' \
  | grep -v '\.pytest_cache' | grep -v '^./\.venv/' | grep -v '^./htmlcov/' | grep -v '^./logs/' || true)
if [ -n "$hits" ]; then
  echo "legacy vocabulary found:"; echo "$hits"; exit 1
fi
echo "vocabulary check: clean"
