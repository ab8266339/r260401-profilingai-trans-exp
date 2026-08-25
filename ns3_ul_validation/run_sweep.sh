#!/bin/bash
cd /mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/ns3_ul_validation
bash build.sh || exit 1
echo "=== mMAS UL Queuing Validation — ns-3.35 ==="
echo "N    sim_ms   theory_ms  rho     nflows pkts"
for N in 10 20 30 50 75 100 125 150 175 200; do
    result=$(./mmas-ul-validation --nUes=$N --simTime=120 --seed=1 2>/dev/null)
    echo "$result" | grep RESULT || echo "N=$N ERROR"
done
