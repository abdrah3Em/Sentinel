#!/usr/bin/env bash
# V10: the README opens as an energy guard for both processes — feeder and oil pipeline —
# with the feeder as the live demo.
set -u
cd "$(dirname "$0")/.."
head -40 readme.md | grep -qiE 'feeder|11 kV' || { echo "README head does not name the feeder"; exit 1; }
head -40 readme.md | grep -qiE 'oil pipeline' || { echo "README head does not name the oil pipeline"; exit 1; }
head -40 readme.md | grep -qiE 'energy' || { echo "README head does not say energy"; exit 1; }
echo "readme check: energy head, feeder and oil pipeline named"
