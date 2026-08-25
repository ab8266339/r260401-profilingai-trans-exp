#!/usr/bin/env bash
set -euo pipefail

SIM=/mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation
FLEXRIC=/opt/oranos-stack/flexric
OUT=${1:-/tmp/profilingai-xapp-rc-quota}

/usr/bin/gcc-12 \
  -DASN -DE2AP_V2 -DKPM_V2_03 -DSQLITE3_XAPP \
  -DNR_RRC_VERSION=17.3.0 -std=gnu11 -g -O0 -Wall -Wextra \
  -I"$FLEXRIC/src" \
  -I"$FLEXRIC/build/examples/xApp/c/monitor/RRC_MESSAGES" \
  -I"$FLEXRIC/src/sm/rc_sm/ie/asn" \
  "$SIM/tier_c_oran/closed_loop/flexric_rc_slice_control_param.c" \
  "$FLEXRIC/build/src/xApp/libe42_xapp.a" \
  "$FLEXRIC/build/examples/xApp/c/monitor/RRC_MESSAGES/libasn1_nr_rrc.a" \
  -pthread -lsctp -ldl -o "$OUT"

echo "$OUT"
