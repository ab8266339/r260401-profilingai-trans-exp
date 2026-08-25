#!/usr/bin/env bash
# Closed-loop gate demonstration (CLOSED_LOOP_CARD.md). Run as root in WSL.
set -u

SIM=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation
CBV="$SIM/cross_backend_validation"
CL="$SIM/tier_c_oran/closed_loop"
OUT=${OUT:-"$CL/results_closed_loop_20260803"}
RIC=/opt/oranos-stack/flexric/build/examples/ric/nearRT-RIC
RIC_CFG=/usr/local/etc/flexric/flexric.conf
SM_PATH=/usr/local/lib/flexric/
GNB_CFG="$CBV/configs/srsran_zmq_gnb_srsue_fdd.yml"
GNB_NETNS_CFG="$CBV/configs/srsran_zmq_gnb_netns_overlay.yml"
E2_CFG="$CBV/configs/srsran_e2_rc_overlay.yml"
UE_CFG="$CBV/configs/srsue_zmq_fdd_netns.conf"
KPM_XAPP=/opt/oranos-stack/flexric/build/examples/xApp/c/monitor/xapp_kpm_moni
XAPP=/tmp/profilingai-xapp-rc-quota
UE_NS=profilingai-ue
PHASE=/tmp/profilingai_phase
INJECT_FLAG=/tmp/profilingai_inject_on
mkdir -p "$OUT"

pids=()
cleanup() {
  rm -f "$INJECT_FLAG"
  for pid in "${pids[@]:-}"; do
    kill -INT "$pid" 2>/dev/null || true
  done
  sleep 2
  for pid in "${pids[@]:-}"; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  ip netns del "$UE_NS" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$(id -u)" -ne 0 ]]; then
  echo "must run as root (UE netns)" >&2
  exit 2
fi

echo "== build parameterized RC xApp"
bash "$CL/build_param_xapp.sh" "$XAPP" >"$OUT/build.log" 2>&1 || { echo BUILD-FAIL; exit 3; }

echo "== netns"
ip netns del "$UE_NS" 2>/dev/null || true
ip netns add "$UE_NS"
ip link add veth-srs-host type veth peer name veth-srs-ue 2>/dev/null || true
ip link set veth-srs-ue netns "$UE_NS"
ip addr add 192.168.250.1/24 dev veth-srs-host 2>/dev/null || true
ip link set veth-srs-host up
ip netns exec "$UE_NS" ip addr add 192.168.250.2/24 dev veth-srs-ue
ip netns exec "$UE_NS" ip link set veth-srs-ue up
ip netns exec "$UE_NS" ip link set lo up
ip netns exec "$UE_NS" ip route add default via 192.168.250.1

echo "== RIC"
stdbuf -oL -eL "$RIC" -c "$RIC_CFG" -p "$SM_PATH" >"$OUT/ric.log" 2>&1 &
pids+=($!)
sleep 2

echo "== gNB"
stdbuf -oL -eL /usr/local/bin/gnb -c "$GNB_CFG" -c "$GNB_NETNS_CFG" -c "$E2_CFG" >"$OUT/gnb.log" 2>&1 &
pids+=($!)
sleep 5

echo "== UE"
ip netns exec "$UE_NS" stdbuf -oL -eL /usr/local/bin/srsue "$UE_CFG" >"$OUT/srsue.log" 2>&1 &
pids+=($!)
sleep 9
ue_ip=$(grep -aoE 'PDU Session Establishment successful\. IP: [0-9.]+' "$OUT/srsue.log" | tail -1 | awk '{print $NF}')
if [[ -z "$ue_ip" ]]; then
  echo "NO-PDU-SESSION" >&2
  exit 4
fi
echo "ue_ip=$ue_ip" | tee "$OUT/session.txt"

echo "== MEC stub"
MEC_PORT=8080 NUM_GPUS=4 MAX_CONCURRENT=8 stdbuf -oL python3 "$CL/mec_stub.py" >"$OUT/mec.log" 2>&1 &
pids+=($!)
sleep 1

echo "== KPM monitor (continuous)"
stdbuf -oL -eL "$KPM_XAPP" -c "$RIC_CFG" -p "$SM_PATH" >"$OUT/kpm_live.log" 2>&1 &
pids+=($!)
sleep 2

echo "== iperf server"
iperf3 -s -B 10.45.0.1 -p 5202 >"$OUT/iperf_server.log" 2>&1 &
pids+=($!)
sleep 1

echo "== scenario start"
echo P1 >"$PHASE"
touch "$INJECT_FLAG"
stdbuf -oL python3 "$CL/injector.py" "$INJECT_FLAG" >"$OUT/injector.log" 2>&1 &
pids+=($!)
ip netns exec "$UE_NS" iperf3 -c 10.45.0.1 -p 5202 -t 68 >"$OUT/iperf_uplink.log" 2>&1 &
pids+=($!)

stdbuf -oL python3 "$CL/gate_runner.py" \
  --kpm-log "$OUT/kpm_live.log" --mec http://127.0.0.1:8080 \
  --xapp "$XAPP" --ric-conf "$RIC_CFG" --sm-path "$SM_PATH" \
  --phase-file "$PHASE" --out "$OUT/gate_ticks.jsonl" --duration 100 \
  >"$OUT/gate_runner.log" 2>&1 &
runner_pid=$!
pids+=($runner_pid)

sleep 35
echo "== phase 2 (injector off)"
rm -f "$INJECT_FLAG"
echo P2 >"$PHASE"
sleep 35
echo "== phase 3 (uplink off)"
echo P3 >"$PHASE"
sleep 32

wait "$runner_pid" 2>/dev/null || true
echo "== teardown"
cleanup
trap - EXIT INT TERM
grep -ac "E2 SETUP" "$OUT/gnb.log" >"$OUT/e2_setup_count.txt" 2>/dev/null || true
echo DONE
