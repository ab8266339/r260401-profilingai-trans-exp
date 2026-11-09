#!/bin/bash
# R260401: download models to shared cache ~/models/<Org>/<Name> from ModelScope (fastest in 2026-10-01 test).
# Lock file ~/models/.lock_<Name> while downloading; marker ~/models/<Org>/<Name>/.complete_r260401 when done.
set -u
PY=~/venvs/vllm/bin/python
for m in Qwen/Qwen3-4B Qwen/Qwen3-8B Qwen/Qwen3-14B-AWQ Qwen/Qwen3-32B-AWQ Qwen/Qwen2.5-72B-Instruct-AWQ; do
  name=${m#*/}; dir=~/models/$m; lock=~/models/.lock_$name
  if [ -f "$dir/.complete_r260401" ] || [ -f "$dir/.complete" ]; then echo "$(date -u +%FT%TZ) SKIP $m (complete)"; continue; fi
  if [ -e "$lock" ] && ! grep -q "r260401" "$lock"; then echo "$(date -u +%FT%TZ) WAIT other agent lock $lock"; while [ -e "$lock" ]; do sleep 60; done; fi
  if [ -f "$dir/.complete" ]; then continue; fi
  echo "r260401 $(date -u +%FT%TZ) pid=$$" > "$lock"
  mkdir -p "$dir"
  t0=$(date +%s)
  for try in 1 2 3; do
    ~/venvs/vllm/bin/modelscope download --model "$m" --local_dir "$dir" --max-workers 8 && ok=1 && break
    ok=0; echo "retry $try for $m"; sleep 30
  done
  t1=$(date +%s); sz=$(du -sb "$dir" | cut -f1)
  if [ "$ok" = 1 ]; then echo "$(date -u +%FT%TZ) source=modelscope bytes=$sz seconds=$((t1-t0))" > "$dir/.complete_r260401"; fi
  echo "$(date -u +%FT%TZ) DONE $m ok=$ok bytes=$sz secs=$((t1-t0))"
  rm -f "$lock"
done
echo ALL_DOWNLOADS_FINISHED
