#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/hxu/exp/r260401_brjd_20261003
PY=/home/hxu/venvs/r260401_brjd_20261003/bin/python
MODEL=/mnt/e/CodeBase-708a/061-working-paper/R260306-fractal-moe/.runtime-qwen-scale/8B
GPU="${CUDA_VISIBLE_DEVICES:-0}"
if [[ "$GPU" == *,* ]]; then
  printf '%s\n' 'R260401 M2 requires one exclusive BRJD GPU, not a multi-GPU list.' >&2
  exit 64
fi
export R260401_CACHE_DIR="$ROOT/cache"
export R260401_VLLM_BIN=/home/hxu/venvs/r260401_brjd_20261003/bin/vllm
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
export TOKENIZERS_PARALLELISM=false
export PATH="/home/hxu/venvs/r260401_brjd_20261003/bin:$PATH"
cd "$ROOT"
"$PY" - "$ROOT/scripts/m2_brjd_ea00369a.py" ea00369a2e24df43268d802930f8f09c69470ceee03a5634fd3cdf47b6fe00d1 <<'PY_VERIFY'
import hashlib,pathlib,sys
p=pathlib.Path(sys.argv[1]); actual=hashlib.sha256(p.read_bytes()).hexdigest()
if actual != sys.argv[2]: raise SystemExit('R260401 M2 frozen code SHA mismatch')
PY_VERIFY
# The root supervisor wraps this entire command in common_guard.py, which checks
# the fresh Windows guard and kills only this owned process group on contention.
exec "$PY" "$ROOT/scripts/m2_brjd_ea00369a.py" --phase pipeline \
  --legacy "$ROOT/scripts/kvbench.py" --model-path "$MODEL" --gpus "$GPU" \
  --port 8241 --out "$ROOT/results/m2_pipeline" --calibration-reps 3 --pilot-reps 3
