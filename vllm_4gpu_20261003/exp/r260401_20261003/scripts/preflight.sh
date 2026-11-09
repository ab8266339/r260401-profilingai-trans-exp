#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/host/exp/r260401_20261003
cd "$ROOT"
export R260401_CACHE_DIR="$ROOT/cache"
export PYTHONIOENCODING=utf-8
mkdir -p results/preflight logs
printf '%s preflight host=%s assigned_gpus=%s\n' "$(date -u +%FT%TZ)" "$(hostname)" "$CUDA_VISIBLE_DEVICES"
/home/host/venvs/vllm/bin/python scripts/kvbench.py --model-key qwen3_8b --model-path /home/host/models/Qwen/Qwen3-8B --grid capacity --gpus "$CUDA_VISIBLE_DEVICES" --port 18201 --kv-dtype auto --out-root "$ROOT/results/preflight" --ready-timeout 1200
