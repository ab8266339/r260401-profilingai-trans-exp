#!/bin/bash
# R260401 TIMED run, exclusive (paperq GPUS=X). Four single-card models in parallel, one per card;
# on each card, in sequence: bf16(auto)-KV main grid -> FP8-KV grid -> KV offload (save/restore) grid.
# FP8 KV uses --attention-backend TRITON_ATTN (vLLM's auto choice for FP8 KV, FlashInfer, needs a JIT
# toolchain that 4卡机 lacks). Resumable: completed cells/configs are skipped on requeue.
# Exit 0 only if all 12 single-card configs reached DONE. Each card capped at 9 h wall.
cd ~/exp/r260401
PY=~/venvs/vllm/bin/python
M=~/models/Qwen
echo "$(date -u +%FT%TZ) run_main start host=$(hostname)"; nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader; uptime
card() {  # gpu key path
  g=$1; k=$2; p=$3; port=$((8100+g))
  $PY kvbench.py --model-key $k --model-path $p --grid main    --gpus $g --port $port --kv-dtype auto
  $PY kvbench.py --model-key $k --model-path $p --grid fp8     --gpus $g --port $port --kv-dtype fp8 -- --attention-backend TRITON_ATTN
  $PY kvbench.py --model-key $k --model-path $p --grid offload --gpus $g --port $port --kv-dtype auto --offload-gib 32
}
export -f card; export PY M
timeout -s TERM 9h bash -c "card 0 qwen3_4b      $M/Qwen3-4B"      > logs/card0.log 2>&1 &
timeout -s TERM 9h bash -c "card 1 qwen3_8b      $M/Qwen3-8B"      > logs/card1.log 2>&1 &
timeout -s TERM 9h bash -c "card 2 qwen3_14b_awq $M/Qwen3-14B-AWQ" > logs/card2.log 2>&1 &
timeout -s TERM 9h bash -c "card 3 qwen3_32b_awq $M/Qwen3-32B-AWQ" > logs/card3.log 2>&1 &
wait
~/venv/bin/python summarize.py ~/exp/r260401/results
n=$(ls results/*/*/DONE 2>/dev/null | grep -v qwen25_72b | wc -l)
echo "$(date -u +%FT%TZ) run_main end: $n/12 single-card configs DONE"
[ "$n" -ge 12 ]
