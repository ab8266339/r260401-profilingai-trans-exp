#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/hxu/exp/r260401_brjd_20261003
VENV=/home/hxu/venvs/r260401_brjd_20261003
MODEL=/mnt/e/CodeBase-708a/061-working-paper/R260306-fractal-moe/.runtime-qwen-scale/8B
export R260401_CACHE_DIR="$ROOT/cache"
export R260401_VLLM_BIN="$VENV/bin/vllm"
export CUDA_VISIBLE_DEVICES=0
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p "$ROOT/results" "$ROOT/logs"
test -x "$VENV/bin/python"
test -x "$VENV/bin/vllm"
test -f "$MODEL/config.json"
cd "$ROOT"
exec timeout --signal=TERM --kill-after=90s 21600s "$VENV/bin/python" scripts/rplus_brjd.py \
  --task r3 --model-key 8B --model-path "$MODEL" --gpus 0 --tp 1 \
  --out-root "$ROOT/results" --kvbench-path "$ROOT/scripts/kvbench.py" \
  --port 8180 --gpu-util 0.90 --max-model-len 12288 --block-size 16 \
  --reps 10 --token-capacity auto --attention-backend TRITON_ATTN
