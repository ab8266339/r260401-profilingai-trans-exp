#!/usr/bin/env bash
set -u

ROOT=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/cross_backend_validation
OUT=${OUT:-"$ROOT/results/flexric_rc_sa"}
RIC=/opt/oranos-stack/flexric/build/examples/ric/nearRT-RIC
RIC_CFG=/usr/local/etc/flexric/flexric.conf
SM_PATH=/usr/local/lib/flexric/
GNB_CFG="$ROOT/configs/srsran_zmq_gnb_srsue_fdd.yml"
GNB_NETNS_CFG="$ROOT/configs/srsran_zmq_gnb_netns_overlay.yml"
E2_CFG="$ROOT/configs/srsran_e2_rc_overlay.yml"
UE_CFG="$ROOT/configs/srsue_zmq_fdd_netns.conf"
XAPP=/tmp/profilingai-xapp-rc-slice
KPM_XAPP=/opt/oranos-stack/flexric/build/examples/xApp/c/monitor/xapp_kpm_moni
UE_NS=profilingai-ue
mkdir -p "$OUT"

ric_pid=""
gnb_pid=""
ue_pid=""
iperf_server_pid=""
iperf_client_pid=""
cleanup() {
  for pid in "$iperf_client_pid" "$iperf_server_pid" "$ue_pid" "$gnb_pid" "$ric_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -INT "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$iperf_client_pid" "$iperf_server_pid" "$ue_pid" "$gnb_pid" "$ric_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  ip netns del "$UE_NS" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run_flexric_rc_sa.sh must run as root to create the UE network namespace" >&2
  exit 2
fi

ip netns del "$UE_NS" 2>/dev/null || true
ip netns add "$UE_NS"
ip link add veth-srs-host type veth peer name veth-srs-ue
ip link set veth-srs-ue netns "$UE_NS"
ip addr add 192.168.250.1/24 dev veth-srs-host
ip link set veth-srs-host up
ip netns exec "$UE_NS" ip addr add 192.168.250.2/24 dev veth-srs-ue
ip netns exec "$UE_NS" ip link set veth-srs-ue up
ip netns exec "$UE_NS" ip link set lo up
ip netns exec "$UE_NS" ip route add default via 192.168.250.1

bash "$ROOT/build_flexric_rc_xapp.sh" "$XAPP" >"$OUT/build.log" 2>&1

stdbuf -oL -eL "$RIC" -c "$RIC_CFG" -p "$SM_PATH" >"$OUT/ric.log" 2>&1 &
ric_pid=$!
sleep 2

stdbuf -oL -eL /usr/local/bin/gnb -c "$GNB_CFG" -c "$GNB_NETNS_CFG" -c "$E2_CFG" >"$OUT/gnb.log" 2>&1 &
gnb_pid=$!
sleep 5

timeout --kill-after=2s --signal=INT 60s ip netns exec "$UE_NS" \
  stdbuf -oL -eL /usr/local/bin/srsue "$UE_CFG" \
  >"$OUT/srsue.log" 2>&1 &
ue_pid=$!
sleep 9

ue_ip=$(grep -aoE 'PDU Session Establishment successful\. IP: [0-9.]+' "$OUT/srsue.log" | tail -1 | awk '{print $NF}')
wan_gateway=$(ip route | awk '/default/{print $3; exit}')
if [[ -z "$ue_ip" ]]; then
  echo "No UE PDU-session address was found" >&2
  exit 1
fi

# Generate sustained uplink user-plane traffic inside the KPM observation
# window.  This is separate from the later bidirectional reachability check.
iperf3 -s -B 10.45.0.1 -p 5202 -1 >"$OUT/iperf_server.log" 2>&1 &
iperf_server_pid=$!
sleep 1
ip netns exec "$UE_NS" iperf3 -c 10.45.0.1 -p 5202 -t 10 \
  >"$OUT/iperf_uplink.log" 2>&1 &
iperf_client_pid=$!

# Subscribe while the UE is attached and carrying a live PDU session.  The
# monitor is intentionally time-bounded; timeout status 124 is a successful
# observation-window close, not an xApp failure.
timeout --kill-after=2s --signal=INT 8s stdbuf -oL -eL "$KPM_XAPP" \
  -c "$RIC_CFG" -p "$SM_PATH" >"$OUT/xapp_kpm_live.log" 2>&1
kpm_status=$?
wait "$iperf_client_pid"
iperf_status=$?
wait "$iperf_server_pid"
iperf_server_status=$?
iperf_client_pid=""
iperf_server_pid=""

timeout --kill-after=2s 12s stdbuf -oL -eL "$XAPP" 0 -c "$RIC_CFG" -p "$SM_PATH" \
  >"$OUT/xapp_rc_slice.log" 2>&1
xapp_status=$?

{
  printf 'ue_ip=%s\nwan_gateway=%s\n' "$ue_ip" "$wan_gateway"
  ip netns exec "$UE_NS" ip -brief addr show tun_srsue
  ip -brief addr show ogstun
  ip route get "$ue_ip"
  ip netns exec "$UE_NS" ip route get "$wan_gateway"
} >"$OUT/network_state.log" 2>&1

ip netns exec "$UE_NS" ping -I tun_srsue -c 4 -W 2 "$wan_gateway" >"$OUT/ping_uplink.log" 2>&1
ping_ul_status=$?
ping -I ogstun -c 4 -W 2 "$ue_ip" >"$OUT/ping_downlink.log" 2>&1
ping_dl_status=$?

cleanup
ue_pid=""
gnb_pid=""
ric_pid=""

printf 'kpm_status=%s\niperf_status=%s\niperf_server_status=%s\nxapp_status=%s\nue_ip=%s\nping_ul_status=%s\nping_dl_status=%s\n' \
  "$kpm_status" "$iperf_status" "$iperf_server_status" "$xapp_status" "$ue_ip" "$ping_ul_status" "$ping_dl_status"

if [[ "$kpm_status" -ne 0 && "$kpm_status" -ne 124 ]] || \
   [[ "$iperf_status" -ne 0 || "$iperf_server_status" -ne 0 || "$xapp_status" -ne 0 || \
      "$ping_ul_status" -ne 0 || "$ping_dl_status" -ne 0 ]]; then
  exit 1
fi
