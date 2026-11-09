#!/bin/bash
# Smoke test (tiny model, <=4 GB on one card, results NOT used): $1 = grid (smoke|smoke_offload), $2 = gpu
cd ~/exp/r260401
G=${2:-3}
if [ "$1" = smoke_offload ]; then OFF="--offload-gib 2"; else OFF=""; fi
CUDA_VISIBLE_DEVICES="" ~/venvs/vllm/bin/python kvbench.py --model-key x --model-path ~/models/Qwen/Qwen3-0.6B --grid smoke --gpus "" --prep-only
timeout 175 ~/venvs/vllm/bin/python kvbench.py --model-key qwen3_0p6b_SMOKE --model-path ~/models/Qwen/Qwen3-0.6B \
  --grid $1 --gpus $G --port 8190 --kv-dtype auto --gpu-util 0.12 --max-model-len 4096 --reps 2 --enforce-eager \
  --out-root ~/exp/r260401/smoke_results $OFF
echo "SMOKE_EXIT=$?"
