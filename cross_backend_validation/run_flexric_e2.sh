#!/usr/bin/env bash
set -u

ROOT=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/cross_backend_validation
OUT=${OUT:-"$ROOT/results/flexric_e2"}
RIC=/opt/oranos-stack/flexric/build/examples/ric/nearRT-RIC
XAPP=${XAPP:-/opt/oranos-stack/flexric/build/examples/xApp/c/monitor/xapp_kpm_moni}
XAPP_LOG=${XAPP_LOG:-xapp_kpm.log}
XAPP_TIMEOUT=${XAPP_TIMEOUT:-8}
RIC_CFG=/usr/local/etc/flexric/flexric.conf
SM_PATH=/usr/local/lib/flexric/
GNB_CFG="$ROOT/configs/srsran_zmq_gnb.yml"
E2_CFG=${E2_CFG:-"$ROOT/configs/srsran_e2_overlay.yml"}
mkdir -p "$OUT"

ric_pid=""
gnb_pid=""
cleanup() {
  if [[ -n "$gnb_pid" ]] && kill -0 "$gnb_pid" 2>/dev/null; then
    kill -INT "$gnb_pid" 2>/dev/null || true
    wait "$gnb_pid" 2>/dev/null || true
  fi
  if [[ -n "$ric_pid" ]] && kill -0 "$ric_pid" 2>/dev/null; then
    kill -INT "$ric_pid" 2>/dev/null || true
    wait "$ric_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

stdbuf -oL -eL "$RIC" -c "$RIC_CFG" -p "$SM_PATH" >"$OUT/ric.log" 2>&1 &
ric_pid=$!
sleep 2

stdbuf -oL -eL /usr/local/bin/gnb -c "$GNB_CFG" -c "$E2_CFG" >"$OUT/gnb.log" 2>&1 &
gnb_pid=$!
sleep 8

if [[ "$XAPP" == *flexric-asan* ]]; then
  timeout --kill-after=2s --signal=INT "${XAPP_TIMEOUT}s" "$XAPP" -c "$RIC_CFG" -p "$SM_PATH" \
    >"$OUT/$XAPP_LOG" 2>&1
else
  timeout --kill-after=2s --signal=INT "${XAPP_TIMEOUT}s" stdbuf -oL -eL "$XAPP" -c "$RIC_CFG" -p "$SM_PATH" \
    >"$OUT/$XAPP_LOG" 2>&1
fi
xapp_status=$?
sleep 1
cleanup
gnb_pid=""
ric_pid=""

python3 "$ROOT/parse_flexric_e2.py" \
  --ric-log "$OUT/ric.log" \
  --gnb-log "$OUT/gnb.log" \
  --xapp-log "$OUT/$XAPP_LOG" \
  --xapp-exit-status "$xapp_status" \
  --output "$OUT/summary.json"
