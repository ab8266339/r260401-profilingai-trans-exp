#!/usr/bin/env bash
set -u

ROOT=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/cross_backend_validation
OAI=/home/hxu/ran-sims/openairinterface5g
BUILD="$OAI/cmake_targets/ran_build/build"
CONF="$OAI/ci-scripts/conf_files/gnb.band78.106prb.rfsim.phytest-dora.conf"
UICC="$OAI/ci-scripts/conf_files/nrue.uicc.conf"
OUT="$ROOT/results/oai_dora"
mkdir -p "$OUT"

gnb_pid=""
cleanup() {
  if [[ -n "$gnb_pid" ]] && kill -0 "$gnb_pid" 2>/dev/null; then
    kill -INT "$gnb_pid" 2>/dev/null || true
    wait "$gnb_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cd "$OUT"
export LD_LIBRARY_PATH="$BUILD:${LD_LIBRARY_PATH:-}"

"$BUILD/nr-softmodem" -O "$CONF" --do-ra --rfsim \
  --rfsimulator.[0].wait_timeout 20 --noS1 \
  --log_config.global_log_options level,nocolor,time \
  >"$OUT/gnb.log" 2>&1 &
gnb_pid=$!
sleep 5

timeout --signal=INT 20s "$BUILD/nr-uesoftmodem" -O "$UICC" \
  --do-ra --rfsim --noS1 \
  --reconfig-file "$OUT/reconfig.raw" \
  --rbconfig-file "$OUT/rbconfig.raw" \
  --rfsimulator.[0].serveraddr 127.0.0.1 \
  --log_config.global_log_options level,nocolor,time \
  >"$OUT/nrue.log" 2>&1
ue_status=$?
sleep 1
cleanup
gnb_pid=""

python3 "$ROOT/parse_oai_dora.py" \
  --gnb-log "$OUT/gnb.log" \
  --ue-log "$OUT/nrue.log" \
  --ue-exit-status "$ue_status" \
  --output "$OUT/summary.json"
