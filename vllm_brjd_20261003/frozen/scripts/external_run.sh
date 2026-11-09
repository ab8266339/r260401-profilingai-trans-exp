#!/usr/bin/env bash
# One queue slot, sequential installation -> semantics gate -> benchmark per engine.
# Usage: bash external_run.sh GPU OUTPUT_DIR [MODEL_PATH] [INSTALL_LIMIT_S] [REPS]
set -u
export PATH="$HOME/.local/bin:$PATH"
GPU="${1:?Pass the one exclusively allocated GPU}"
OUT="${2:?Pass external results directory}"
MODEL="${3:-/mnt/e/CodeBase-708a/061-working-paper/R260306-fractal-moe/.runtime-qwen-scale/8B}"
INSTALL_LIMIT="${4:-1200}"
REPS="${5:-10}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="/home/hxu/exp/r260401_brjd_20261003"
export R260401_CACHE_DIR="$ROOT/cache" PYTHONIOENCODING=utf-8
BASE_PY="/home/hxu/venvs/r260401_brjd_20261003/bin/python"
mkdir -p "$OUT"
if [ ! -x "$BASE_PY" ] || ! command -v timeout >/dev/null || ! command -v bash >/dev/null; then
  for ENGINE in lmcache sglang; do
    mkdir -p "$OUT/$ENGINE"
    printf '{"engine":"%s","status":"UNMEASURED","phase":"queue_preflight","note":"Required existing base Python, timeout, or bash command absent; no external arm was measured."}\n' "$ENGINE" > "$OUT/$ENGINE/summary.json"
  done
  printf '{"status":"EXTERNAL_TASK_FINISHED","engines":{"lmcache":{"status":"UNMEASURED","phase":"queue_preflight"},"sglang":{"status":"UNMEASURED","phase":"queue_preflight"}},"note":"Queue completion is not evidence of measurements."}\n' > "$OUT/external_task_status.json"
  exit 0
fi
bash "$HERE/external_setup.sh" all "$OUT/setup" "$INSTALL_LIMIT" > "$OUT/setup_task.log" 2>&1
for ENGINE in lmcache sglang; do
  ENV_PATH="/home/hxu/venvs/r260401_${ENGINE}_brjd_20261003"
  ENGINE_OUT="$OUT/$ENGINE"
  mkdir -p "$ENGINE_OUT"
  if ! "$BASE_PY" - "$OUT/setup/$ENGINE/install_status.json" <<'PY'
import json,pathlib,socket,sys
try: status=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
except Exception: raise SystemExit(1)
raise SystemExit(0 if status.get('status')=='INSTALLED_IMPORT_CHECKED' else 1)
PY
  then
    printf '{"engine":"%s","status":"UNMEASURED","phase":"installation","note":"See setup engine install status/log. No fallback arm was substituted."}\n' "$ENGINE" > "$ENGINE_OUT/summary.json"
    continue
  fi
  timeout --signal=TERM --kill-after=30 1200 "$ENV_PATH/bin/python" "$HERE/external_probe.py" \
    --engine "$ENGINE" --gpu "$GPU" --model "$MODEL" --out "$ENGINE_OUT/gate.json" \
    > "$ENGINE_OUT/gate_task.log" 2>&1
  GATE_RC=$?
  if [ "$GATE_RC" -ne 0 ]; then
    printf '{"engine":"%s","status":"UNMEASURED","phase":"compatibility_and_semantics_gate","exit_code":%s,"note":"See gate.json and launch logs; no performance comparison was made."}\n' "$ENGINE" "$GATE_RC" > "$ENGINE_OUT/summary.json"
    continue
  fi
  timeout --signal=TERM --kill-after=30 14400 "$ENV_PATH/bin/python" "$HERE/external_rplus.py" \
    --engine "$ENGINE" --gpu "$GPU" --model "$MODEL" --out "$ENGINE_OUT/summary.json" \
    --gate "$ENGINE_OUT/gate.json" --reps "$REPS" --cpu-cache-gb 8 \
    > "$ENGINE_OUT/benchmark_task.log" 2>&1
  BENCH_RC=$?
  if [ "$BENCH_RC" -ne 0 ]; then
    "$BASE_PY" - "$ENGINE_OUT/summary.json" "$ENGINE" "$BENCH_RC" <<'PY'
import json,pathlib,socket,sys
p,name,rc=sys.argv[1:]; p=pathlib.Path(p)
try: status=json.loads(p.read_text(encoding='utf-8'))
except Exception: status={'engine':name}
status.update(status='UNMEASURED',phase='benchmark',exit_code=int(rc),note='Completed raw cells are retained; incomplete engine runs cannot support the paired headline.')
p.write_text(json.dumps(status,indent=2),encoding='utf-8')
PY
  fi
done
"$BASE_PY" - "$OUT" <<'PY'
import json,pathlib,socket,sys
out=pathlib.Path(sys.argv[1]); engines={}
for name in ('lmcache','sglang'):
    try: engines[name]=json.loads((out/name/'summary.json').read_text(encoding='utf-8'))
    except Exception as exc: engines[name]={'status':'UNMEASURED','error':repr(exc)}
result={'machine':socket.gethostname(),'machine_label':'DESKTOP-BRJD313','task_root':'/home/hxu/exp/r260401_brjd_20261003','status':'EXTERNAL_TASK_FINISHED','engines':{k:{'status':v.get('status'),'error':v.get('error'),'phase':v.get('phase')} for k,v in engines.items()},'note':'Per-engine MEASURED/UNMEASURED is authoritative. Queue completion is not evidence of measurements.'}
(out/'external_task_status.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
PY
