#!/usr/bin/env bash
# V10: the first 60 lines of the README are about the feeder only.
set -u
cd "$(dirname "$0")/.."
hits=$(head -60 readme.md | grep -niE 'pipeline|pump station|tank farm|MOV-201|ESD-301|DRA' || true)
if [ -n "$hits" ]; then echo "README first 60 lines mention the second process:"; echo "$hits"; exit 1; fi
head -60 readme.md | grep -qiE 'feeder|11 kV|CB-101' || { echo "README first 60 lines do not name the feeder"; exit 1; }
echo "readme check: grid-only head"
