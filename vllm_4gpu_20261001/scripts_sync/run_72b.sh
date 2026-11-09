#!/bin/bash
# R260401 TIMED run, exclusive (paperq GPUS=X): 70B-class model (Qwen2.5-72B-Instruct-AWQ), tensor parallel 4.
# Resumable; capped at 9 h wall.
cd ~/exp/r260401
PY=~/venvs/vllm/bin/python
P=~/models/Qwen/Qwen2.5-72B-Instruct-AWQ
echo "$(date -u +%FT%TZ) run_72b start host=$(hostname)"; nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader; uptime
timeout -s TERM 9h bash -c "
$PY kvbench.py --model-key qwen25_72b_awq --model-path $P --grid tp4     --gpus 0,1,2,3 --tp 4 --port 8200 --kv-dtype auto
$PY kvbench.py --model-key qwen25_72b_awq --model-path $P --grid tp4_fp8 --gpus 0,1,2,3 --tp 4 --port 8200 --kv-dtype fp8 -- --attention-backend TRITON_ATTN
" > logs/tp4.log 2>&1
~/venv/bin/python summarize.py ~/exp/r260401/results
n=$(ls results/qwen25_72b_awq/*/DONE 2>/dev/null | wc -l)
echo "$(date -u +%FT%TZ) run_72b end: $n/2 configs DONE"
[ "$n" -ge 2 ]
