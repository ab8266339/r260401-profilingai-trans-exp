# R260401 RD-R+ protocol, 2026-10-03

This package extends the archived RD-R0 `kvbench.py`. Freeze a SHA256-named copy of
`rplus.py` before queueing. Record the wrapper, dependency versions, legacy hash and
baseline hash. Do not replace a running file. No package installation or model
download is performed by this runner. The root runner may use a separate Python
environment for SciPy, while `kvbench.VLLM_BIN` still starts the existing vLLM.

## Common workload and measurements

- Reuse WORKLOAD_SEED=20261001, sorted RD-R0 word pool, and session_trace. Initial
  context is tau0 tokens; first stage also appends 50 instruction tokens. Thus the
  tau0=3400 initial prompt is 3450 tokens. Each later stage appends 50 and the real
  prior output IDs. The cache salt never adds prompt tokens.
- R changes salt each stage and asserts `cached_tokens == 0` for **every request**.
  A0 and the automatic-cache admission arms use a session salt. They are APC, with
  no guarantee of protected pinning. Cache reset must succeed before each cell.
- Ten paired repetitions; session random streams depend on repetition/session,
  not arm. Arms rotate by repetition. R4 additionally rotates default/uncapped
  server configurations, with a new server for each configuration/repetition.
- Record each stage's TTFT, completion-minus-TTFT, actual prompt/output counts,
  hashes, EOS reason, serialized UTF-8 context text and metadata bytes, KV block
  estimate, /metrics samples and four-card process/utilization samples.
- TTFT includes queue/network and is not a pure GPU prefill measurement. Natural
  EOS outputs are never padded. KV per-session bytes are block estimates, not
  observed individual allocation. APC may retain blocks after session completion;
  recorded KV holding duration is a lower bound until completion, not a pin lease
  or explicit release. The full cache is cleared between cells.
- Under natural capacity, startup available bytes / observed token capacity yields
  a rounded allocator estimate. Under a blocks override, unused available memory
  is not treated as allocated KV: tensor bytes/token derive from the model config
  and selected KV dtype. Sampled KV-pool peaks are clearly labelled estimates.
- Paired t 95% intervals and Wilcoxon are descriptive. A zero-containing interval
  means unresolved, not equivalent. No +/-3% test is used to assert mechanism
  identity. P-admit minus A0 is the native admission headline; P-admit minus R is
  secondary. Divergent output/prompt hashes invalidate an identical-trace claim.

## R+1 exclusive interference / timing reuse check

8B, tau0=3400, concurrency=4, depth=8, A0/R, ten repetitions. All four cards must
have no foreign compute process; non-selected cards must remain <=5% utilization.
The monitor invalidates a cell if that condition fails. The historical same-host
baseline has 71840 KV tokens at block size 16, so freeze `--token-capacity 71840`.
Use `rd_r0_baseline.json`: model/dtype, tau0, concurrency, depth and repetition
match. The old 32-stage traces were truncated at stage eight in this baseline.

The 90% paired relative interval inside +/-2%, with ten pairs, records support for
the predeclared timing reuse tolerance. It does not identify interference as the
cause of any change or establish mechanism equivalence. Differences in versions,
allocator budget, temperature, clocks and execution date remain in provenance.

```bash
python rplus_FROZEN.py --task r1 --model-key qwen3_8b \
  --model-path "$HOME/models/Qwen/Qwen3-8B" --gpus 0 --port 8180 \
  --out-root "$HOME/exp/r260401_20261003/results" --token-capacity 71840 \
  --parallel-reference "$HOME/exp/r260401_20261003/rd_r0_baseline.json"
```

## R+2 natural EOS

Same grid/arms/repetitions. Requests set max_tokens=128 and omit min_tokens and
ignore_eos. Store every stage's actual length. `natural_generation_report.json`
compares the A0-R mean sign against the same fixed-length baseline. A sign reversal
requires limiting conclusions to fixed-length generation. If every request still
reaches 128 tokens, do not claim robustness to variable EOS lengths.

```bash
python rplus_FROZEN.py --task r2 --model-key qwen3_8b \
  --model-path "$HOME/models/Qwen/Qwen3-8B" --gpus 0 --port 8180 \
  --out-root "$HOME/exp/r260401_20261003/results" \
  --parallel-reference "$HOME/exp/r260401_20261003/rd_r0_baseline.json"
```

## R+3 precision at equal capacity / backend

8B, tau0=900, concurrency=4, depth=32, A0/R, ten repetitions. Probe actual installed
`vllm serve --help=all` for attention-backend, blocks-override and block-size flags.
With token-capacity=auto, first start bf16/auto KV under TRITON_ATTN and read its
actual token budget. Freeze the integer count of 16-token blocks. Stop that server
and start FP8 KV under the same backend and block count. Reject any mismatch in
actual token capacity. Store backend log evidence and per-stage token hashes.
Unsupported flags/backend/FP8 generate an UNMEASURED/FAILED record, preserving any
partial measurements. Never use the unrelated automatic-backend budget as the
TRITON budget. No fallback to a different attention backend is attempted.

```bash
python rplus_FROZEN.py --task r3 --model-key qwen3_8b \
  --model-path "$HOME/models/Qwen/Qwen3-8B" --gpus 0 --port 8180 \
  --out-root "$HOME/exp/r260401_20261003/results" \
  --attention-backend TRITON_ATTN --token-capacity auto
```

## R+4 native low-pressure comparison

Grids (3400,4,8) and (900,16,8), arms R/A0/A-uncap/P-admit, ten repetitions. Query
the installed EngineArgs API-server configuration for actual default max_num_seqs.
A0 leaves that parameter at its version/hardware default. A-uncap sets it no lower
than that default or the largest grid concurrency. P-admit uses the same server
configuration and rejects before execution when the sum of block-rounded maximum
token paths exceeds 0.95 * observed KV token budget; the server remains APC/LRU.
Peak bytes derive from the measured allocator token budget and conservative token
path, not an asserted observed per-session allocation.

Default/uncapped configurations alternate order across repetitions. Each pair
requires two model-server starts, so this task includes twenty startup/warmup
cycles. This is deliberately a low-pressure baseline: expected capacities above
87000 tokens can admit all sessions in both grids. No admission difference or pin
superiority is promised; high-pressure discrimination is the separate M2 task.

```bash
python rplus_FROZEN.py --task r4 --model-key qwen3_8b \
  --model-path "$HOME/models/Qwen/Qwen3-8B" --gpus 0 --port 8180 \
  --out-root "$HOME/exp/r260401_20261003/results"
```

LMCache P-pin and SGLang S-radix are separate external tasks, through verified
`external_setup.sh`, `external_probe.py` and `external_rplus.py`. Native output
marks those arms unmeasured. LocalCPUBackend pin protects CPU KV chunks, not GPU
APC blocks. SGLang compares radix reuse against its own forced-miss/flush arm;
absolute latency is not compared against a different vLLM stack. The installed
version, location, pin/check_finish/lookup evidence and anti-eviction gate must be
recorded by the external task before calling a row measured.

## Outputs / restart rule

Each server launch writes protocol.json with source/arguments hashes, startup log,
server_info.json, complete raw cell JSONs, summary.json and DONE.json or FAILED.json.
Existing cells are skipped only under the same protocol hash. Protocol changes
require a new output root. R4 combines actual raw cells with their source hashes,
preserving the default/uncapped/repetition originals. Every task uses four-card
exclusivity by default; --allow-shared is an explicit opt-out and must be reported.
