#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/hxu/exp/r260401_brjd_20261003
MODEL=/mnt/e/CodeBase-708a/061-working-paper/R260306-fractal-moe/.runtime-qwen-scale/8B
cd "$ROOT"
test -f "$ROOT/results/preflight/BRJD_PREFLIGHT_DONE.json"
exec bash "$ROOT/scripts/external_run.sh" 0 "$ROOT/results/external" "$MODEL" 1800 10
