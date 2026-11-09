#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/host/exp/r260401_20261003
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH" PYTHONIOENCODING=utf-8 R260401_CACHE_DIR="$ROOT/cache"
GPU="${CUDA_VISIBLE_DEVICES%%,*}"
/home/host/venvs/vllm/bin/python - "$ROOT/R12_PROTOCOL.json" <<'VERIFY'
import hashlib,json,pathlib,sys
root=pathlib.Path('/home/host/exp/r260401_20261003')
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
for name,expected in p['dependencies'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected, 'Frozen dependency changed: '+name
assert (root/'results/preflight/qwen3_8b/auto_capacity/DONE').exists(), 'Preflight incomplete'
VERIFY
exec /home/host/venvs/vllm/bin/python "$ROOT/scripts/rplus_r12_14a3fccf.py" --task r1 --model-key qwen3_8b --model-path /home/host/models/Qwen/Qwen3-8B --gpus "$GPU" --port 18211 --out-root "$ROOT/results/rplus_r12" --kvbench-path "$ROOT/scripts/kvbench.py" --parallel-reference "$ROOT/rd_r0_baseline.json" --token-capacity 71840 --reps 10 --tau0 3400 --depth 8 --concurrency 4
