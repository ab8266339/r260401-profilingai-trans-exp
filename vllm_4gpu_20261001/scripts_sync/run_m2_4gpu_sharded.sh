#!/usr/bin/env bash
# R260401 RD-M2 on 4卡机 across several GPUs (2026-10-05). Same frozen-dependency check and runner setup as
# run_m2_v3.sh, then scripts/m2_shard_20261005.py <stage> [K]. Started by the NAS dispatch daemon; GPU stages get the
# GPU the daemon claimed in CUDA_VISIBLE_DEVICES. A 30 s host-load log (load average, every GPU's use) is written
# next to the results as a covariate: this run shares the machine with other jobs (no whole-machine exclusivity).
set -euo pipefail
ROOT=/home/host/exp/r260401_20261003
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH" PYTHONIOENCODING=utf-8 R260401_CACHE_DIR="$ROOT/cache" OPENBLAS_NUM_THREADS=1
STAGE=$1
K=${2:-}
GPU="${CUDA_VISIBLE_DEVICES:-}"
GPU="${GPU%%,*}"
case "$STAGE" in
  cal|pilot|main) [ -n "$GPU" ] || { echo "stage $STAGE needs a GPU (CUDA_VISIBLE_DEVICES is empty)"; exit 2; } ;;
esac
/home/host/venvs/vllm/bin/python - "$ROOT/M2_EXECUTION_PROTOCOL_v3.json" <<'VERIFY'
import hashlib,json,pathlib,sys
root=pathlib.Path('/home/host/exp/r260401_20261003')
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
for name,expected in p['dependencies'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected, 'Frozen dependency changed: '+name
assert (root/'results/preflight/qwen3_8b/auto_capacity/DONE').exists(), 'Preflight incomplete'
VERIFY
timeout --signal=TERM --kill-after=30 600 bash "$ROOT/scripts/setup_runner.sh"
mkdir -p "$ROOT/results/m2_pipeline/sharding" "$ROOT/locks"
LOAD="$ROOT/results/m2_pipeline/sharding/host_load_${STAGE}${K}.log"
( while :; do
    echo "$(date -u +%FT%TZ) load=$(cut -d' ' -f1-3 /proc/loadavg) gpu=$(nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | tr '\n' ';')"
    sleep 30
  done ) >> "$LOAD" 2>&1 &
SIDE=$!
trap 'kill $SIDE 2>/dev/null || true' EXIT
echo "== m2 sharded stage=$STAGE k=${K:--} gpu=${GPU:-none} start $(date '+%F %T')"
rc=0
flock -n -E 75 "$ROOT/locks/m2_sharded_${STAGE}${K}.lock" \
  /home/host/venvs/r260401_runner_20261003/bin/python -u "$ROOT/scripts/m2_shard_20261005.py" "$STAGE" ${K:+"$K"} --gpus "$GPU" || rc=$?
echo "== m2 sharded stage=$STAGE k=${K:--} end rc=$rc $(date '+%F %T')"
exit $rc
