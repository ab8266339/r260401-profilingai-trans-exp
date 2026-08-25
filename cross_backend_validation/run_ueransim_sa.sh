#!/usr/bin/env bash
set -u

ROOT=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/cross_backend_validation
OUT=${OUT:-"$ROOT/results/ueransim_sa"}
UERANSIM=/opt/oranos-stack/UERANSIM/build
GNB_CFG="$ROOT/configs/ueransim_open5gs_gnb.yaml"
UE_CFG="$ROOT/configs/ueransim_open5gs_ue.yaml"
UE_NS=profilingai-999700000000001-internet-psi1
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
  echo "run_ueransim_sa.sh must run as root to create the UE TUN namespace" >&2
  exit 2
fi

ip netns del "$UE_NS" 2>/dev/null || true
stdbuf -oL -eL "$UERANSIM/nr-gnb" -c "$GNB_CFG" >"$OUT/gnb.log" 2>&1 &
gnb_pid=$!
sleep 3

timeout --kill-after=2s --signal=INT 25s stdbuf -oL -eL "$UERANSIM/nr-ue" -c "$UE_CFG" \
  >"$OUT/ue.log" 2>&1 &
ue_pid=$!
sleep 8

ue_ip=$(grep -aoE 'TUN interface\[[^,]+, [0-9.]+' "$OUT/ue.log" | tail -1 | awk '{print $NF}')
UE_NS=$(grep -aoE 'namespace\[[^]]+\]' "$OUT/ue.log" | tail -1 | sed -e 's/^namespace\[//' -e 's/\]$//')
wan_gateway=$(ip route | awk '/default/{print $3; exit}')
{
  printf 'ue_ip=%s\nue_namespace=%s\nwan_gateway=%s\n' "$ue_ip" "$UE_NS" "$wan_gateway"
  ip netns exec "$UE_NS" ip -brief addr
  ip -brief addr show ogstun
  ip route get "$ue_ip"
  ip netns exec "$UE_NS" ip route get "$wan_gateway"
} >"$OUT/network_state.log" 2>&1

ip netns exec "$UE_NS" ping -c 4 -W 2 "$wan_gateway" >"$OUT/ping_uplink.log" 2>&1
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
