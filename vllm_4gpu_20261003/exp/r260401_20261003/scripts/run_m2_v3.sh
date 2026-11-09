#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/host/exp/r260401_20261003
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH" PYTHONIOENCODING=utf-8 R260401_CACHE_DIR="$ROOT/cache" OPENBLAS_NUM_THREADS=1
GPU="${CUDA_VISIBLE_DEVICES%%,*}"
/home/host/venvs/vllm/bin/python - "$ROOT/M2_EXECUTION_PROTOCOL_v3.json" <<'VERIFY'
import hashlib,json,pathlib,sys
root=pathlib.Path('/home/host/exp/r260401_20261003')
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
for name,expected in p['dependencies'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected, 'Frozen dependency changed: '+name
assert (root/'results/preflight/qwen3_8b/auto_capacity/DONE').exists(), 'Preflight incomplete'
VERIFY
timeout --signal=TERM --kill-after=30 600 bash "$ROOT/scripts/setup_runner.sh"
PY=/home/host/venvs/r260401_runner_20261003/bin/python
exec "$PY" "$ROOT/scripts/m2_c034f74b.py" --phase pipeline --model-path /home/host/models/Qwen/Qwen3-8B --gpus "$GPU" --port 18231 --out "$ROOT/results/m2_pipeline" --legacy "$ROOT/scripts/kvbench.py" --prior-baseline "$ROOT/rd_r0_baseline.json" --calibration-reps 3 --pilot-reps 3 --uncap 2048
