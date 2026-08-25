#!/bin/bash
# MD1 regression + MG1 random-payload sweep (review fix, 2026-07-04)
cd /mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/ns3_ul_validation
bash build.sh || exit 1
echo "=== regression MD1 (must match published Table) ==="
for N in 10 30 50 100 150 175 200; do
    ./mmas-ul-validation --nUes=$N --simTime=120 --seed=1 2>/dev/null | grep RESULT || echo "N=$N ERROR"
done
echo "=== random payload MG1 (model mixture, seeds 1-5) ==="
for N in 10 30 50 100 150 175 200; do
    for S in 1 2 3 4 5; do
        ./mmas-ul-validation --nUes=$N --simTime=120 --seed=$S --randomPayload=1 2>/dev/null | grep RESULT || echo "N=$N S=$S ERROR"
    done
done
echo DONE
