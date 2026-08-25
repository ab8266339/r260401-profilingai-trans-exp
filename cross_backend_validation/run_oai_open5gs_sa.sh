#!/usr/bin/env bash
set -u

ROOT=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/cross_backend_validation
OUT=${OUT:-"$ROOT/results/oai_open5gs_sa"}
OAI=/home/hxu/ran-sims/openairinterface5g
BUILD="$OAI/cmake_targets/ran_build/build"
GNB_CFG="$OAI/ci-scripts/conf_files/gnb.sa.band78.106prb.rfsim.yaml"
UE_CFG="$OAI/ci-scripts/conf_files/nrue.uicc.yaml"
UE_NS=profilingai-oai-ue
mkdir -p "$OUT"

gnb_pid=""
ue_pid=""
cleanup() {
  for pid in "$ue_pid" "$gnb_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -INT "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$ue_pid" "$gnb_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  ip netns del "$UE_NS" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run_oai_open5gs_sa.sh must run as root to create the UE network namespace" >&2
  exit 2
fi

ip netns del "$UE_NS" 2>/dev/null || true
ip netns add "$UE_NS"
ip link add veth-oai-host type veth peer name veth-oai-ue
ip link set veth-oai-ue netns "$UE_NS"
ip addr add 192.168.251.1/24 dev veth-oai-host
ip link set veth-oai-host up
ip netns exec "$UE_NS" ip addr add 192.168.251.2/24 dev veth-oai-ue
ip netns exec "$UE_NS" ip link set veth-oai-ue up
ip netns exec "$UE_NS" ip link set lo up
ip netns exec "$UE_NS" ip route add default via 192.168.251.1

export LD_LIBRARY_PATH="$BUILD:${LD_LIBRARY_PATH:-}"
cd "$OUT"

stdbuf -oL -eL "$BUILD/nr-softmodem" -O "$GNB_CFG" --rfsim \
  --gNBs.[0].plmn_list.[0].mcc 999 \
  --gNBs.[0].plmn_list.[0].mnc 70 \
  --gNBs.[0].amf_ip_address.[0].ipv4 127.0.0.5 \
  --gNBs.[0].NETWORK_INTERFACES.GNB_IPV4_ADDRESS_FOR_NG_AMF 127.0.0.1 \
  --gNBs.[0].NETWORK_INTERFACES.GNB_IPV4_ADDRESS_FOR_NGU 127.0.0.1 \
  --log_config.global_log_options level,nocolor,time \
  >"$OUT/gnb.log" 2>&1 &
gnb_pid=$!
sleep 6

timeout --kill-after=3s --signal=INT 45s ip netns exec "$UE_NS" \
  stdbuf -oL -eL "$BUILD/nr-uesoftmodem" -O "$UE_CFG" --rfsim \
  -r 106 --numerology 1 -C 3319680000 \
  --rfsimulator.[0].serveraddr 192.168.251.1 \
  --uicc0.imsi 999700000000001 \
  --uicc0.key 465B5CE8B199B49FAA5F0A2EE238A6BC \
  --uicc0.opc E8ED289DEBA952E4283B54E88E6183CA \
  --uicc0.pdu_sessions.[0].dnn internet \
  --log_config.global_log_options level,nocolor,time \
  >"$OUT/nrue.log" 2>&1 &
ue_pid=$!

ue_ready=0
for _ in $(seq 1 30); do
  if ip netns exec "$UE_NS" ip link show oaitun_ue1 >/dev/null 2>&1; then
    ue_ready=1
    break
  fi
  sleep 1
done

if [[ "$ue_ready" -ne 1 ]]; then
  echo "OAI UE TUN did not become ready" >&2
  cleanup
  exit 1
fi

ue_ip=$(ip netns exec "$UE_NS" ip -4 -o addr show oaitun_ue1 | awk '{print $4}' | cut -d/ -f1)
wan_gateway=$(ip route | awk '/default/{print $3; exit}')
{
  printf 'ue_ip=%s\nue_namespace=%s\nwan_gateway=%s\n' "$ue_ip" "$UE_NS" "$wan_gateway"
  ip netns exec "$UE_NS" ip -brief addr
  ip -brief addr show ogstun
  ip route get "$ue_ip"
  ip netns exec "$UE_NS" ip route get "$wan_gateway"
} >"$OUT/network_state.log" 2>&1

ip netns exec "$UE_NS" ping -I oaitun_ue1 -c 4 -W 2 "$wan_gateway" >"$OUT/ping_uplink.log" 2>&1
ping_ul_status=$?
ping -I ogstun -c 4 -W 2 "$ue_ip" >"$OUT/ping_downlink.log" 2>&1
ping_dl_status=$?

cleanup
ue_pid=""
gnb_pid=""

printf 'ue_ip=%s\nping_ul_status=%s\nping_dl_status=%s\n' \
  "$ue_ip" "$ping_ul_status" "$ping_dl_status"

if [[ "$ping_ul_status" -ne 0 || "$ping_dl_status" -ne 0 ]]; then
  exit 1
fi
