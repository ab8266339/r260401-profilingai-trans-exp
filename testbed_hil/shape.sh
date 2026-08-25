#!/bin/bash
# tc uplink shaping for the hardware-in-the-loop testbed (run as root in WSL).
# Shapes ONLY traffic to the relay port; everything else is untouched.
#
#   ./shape.sh set <rate_mbit> [port]   e.g. ./shape.sh set 10 18090
#   ./shape.sh clear
#   ./shape.sh show
set -e
DEV=eth0
PORT=${3:-18090}

case "$1" in
  set)
    RATE=$2
    tc qdisc del dev $DEV root 2>/dev/null || true
    tc qdisc add dev $DEV root handle 1: htb default 20
    tc class add dev $DEV parent 1: classid 1:10 htb rate ${RATE}mbit ceil ${RATE}mbit burst 15k
    tc class add dev $DEV parent 1: classid 1:20 htb rate 10gbit
    tc qdisc add dev $DEV parent 1:10 handle 10: bfifo limit 5mb
    tc filter add dev $DEV protocol ip parent 1: prio 1 u32 \
        match ip dport $PORT 0xffff flowid 1:10
    echo "UL to port $PORT shaped at ${RATE} Mbit/s (queue 5 MB)"
    ;;
  clear)
    tc qdisc del dev $DEV root 2>/dev/null || true
    echo "shaping cleared"
    ;;
  show)
    tc -s qdisc show dev $DEV
    tc -s class show dev $DEV
    ;;
  *)
    echo "usage: $0 {set <rate_mbit> [port]|clear|show}"; exit 1
    ;;
esac
