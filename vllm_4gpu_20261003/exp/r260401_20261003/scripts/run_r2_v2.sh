#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/host/exp/r260401_20261003
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH" PYTHONIOENCODING=utf-8 R260401_CACHE_DIR="$ROOT/cache" OPENBLAS_NUM_THREADS=1
GPU="${CUDA_VISIBLE_DEVICES%%,*}"
/home/host/venvs/vllm/bin/python - "$ROOT/EXECUTION_PROTOCOL_v2.json" <<'VERIFY'
import hashlib,json,pathlib,sys
root=pathlib.Path('/home/host/exp/r260401_20261003')
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
for name,expected in p['dependencies'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected, 'Frozen dependency changed: '+name
assert (root/'results/preflight/qwen3_8b/auto_capacity/DONE').exists(), 'Preflight incomplete'
VERIFY
timeout --signal=TERM --kill-after=30 600 bash "$ROOT/scripts/setup_runner.sh"
PY=/home/host/venvs/r260401_runner_20261003/bin/python
exec "$PY" "$ROOT/scripts/rplus_95d81299.py" --task r2 --model-key qwen3_8b --model-path /home/host/models/Qwen/Qwen3-8B --gpus "$GPU" --port 18212 --out-root "$ROOT/results/rplus_v2" --kvbench-path "$ROOT/scripts/kvbench.py" --parallel-reference "$ROOT/rd_r0_baseline.json" --reps 10 --token-capacity 71840 --tau0 3400 --depth 8 --concurrency 4
