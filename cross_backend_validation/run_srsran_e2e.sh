#!/usr/bin/env bash
set -u

ROOT=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/cross_backend_validation
GNB_CFG="${1:-$ROOT/configs/srsran_zmq_gnb.yml}"
UE_CFG="${2:-$ROOT/configs/srsue_zmq.conf}"
OUT="${3:-$ROOT/results/srsran_e2e}"
mkdir -p "$OUT"

gnb_pid=""
cleanup() {
  if [[ -n "$gnb_pid" ]] && kill -0 "$gnb_pid" 2>/dev/null; then
    kill -INT "$gnb_pid" 2>/dev/null || true
    wait "$gnb_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

/usr/local/bin/gnb -c "$GNB_CFG" >"$OUT/gnb.log" 2>&1 &
gnb_pid=$!
sleep 5

timeout --signal=INT 20s /usr/local/bin/srsue "$UE_CFG" >"$OUT/srsue.log" 2>&1
ue_status=$?
sleep 1
cleanup
gnb_pid=""

python3 "$ROOT/parse_srsran_e2e.py" \
  --gnb-log "$OUT/gnb.log" \
  --ue-log "$OUT/srsue.log" \
  --ue-exit-status "$ue_status" \
  --output "$OUT/summary.json"
