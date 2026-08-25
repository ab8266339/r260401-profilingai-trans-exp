#!/bin/bash
cd /mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/ns3_ul_validation
bash build.sh || exit 1
echo "--- single test N=10 ---"
./mmas-ul-validation --nUes=10 --simTime=30 --seed=1 2>&1
