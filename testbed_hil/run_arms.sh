#!/bin/bash
# Two-arm soak for the hardware-in-the-loop testbed (run as root in WSL).
# Arm A: baseline TDD 0.20  -> UL 10   Mbit/s shared
# Arm B: adapted  TDD 0.55  -> UL 27.5 Mbit/s shared
# Same UE population and seed in both arms; only the shaped rate changes.
set -e
cd "$(dirname "$0")"
HOST=${1:-172.28.48.1}
DUR=${2:-720}

COMMON="--host $HOST --port 18090 --n-bg 200 --n-llm 8 --lam 0.2 \
        --lam-llm 0.05 --dur $DUR --seed 1 --model qwen3:1.7b \
        --num-predict 60"

echo "=== Arm A: TDD 0.20 (10 Mbit/s), ${DUR}s ==="
./shape.sh set 10 18090
python3 ue_client.py $COMMON --out arm_tdd020.jsonl

echo "=== Arm B: TDD 0.55 (27.5 Mbit/s), ${DUR}s ==="
./shape.sh set 27.5 18090
python3 ue_client.py $COMMON --out arm_tdd055.jsonl

./shape.sh clear
echo "=== both arms complete ==="
