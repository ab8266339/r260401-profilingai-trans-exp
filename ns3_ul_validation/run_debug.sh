#!/bin/bash
cd /mnt/d/CodeBase-708a/061-working-paper/R260401-profilingai-trans/simulation/ns3_ul_validation
./mmas-ul-validation --nUes=5 --simTime=60 --seed=1 2>&1 | head -50
