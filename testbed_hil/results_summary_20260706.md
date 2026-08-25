# Hardware-in-the-loop soak results

## CLEAN RE-RUN (2026-07-11) — canonical dataset, supersedes the 07-06 run below

GPU unloaded before runs (user-authorized): killed run_locality_exp.py
--cell L1 --steps 25000 + second python job; unloaded ollama-resident model.
ROOT CAUSE of the earlier slow inference + qwen3:4b CUDA assert: Ollama had
loaded models with num_ctx=40960 (81 GB alloc, 81% CPU spill). Fix: relay pins
"options":{"num_ctx":4096}. qwen3:4b then loads fine, warm 0.448 s / 60 tk.
GameViewerServer (5.4 GB) deliberately NOT killed (possible active remote
session). Model: qwen3:4b (matches measured-tier midpoint), same protocol/
seeds/durations as 07-06.

| arm file | UL share (rate) | UEs | rho | sense sojourn mean/p50/p95/max (ms) | P-K (ms) | agent n | e2e mean/p95 (ms) | infer (ms) | UL share |
|---|---|---|---|---|---|---|---|---|---|
| arm2_tdd020.jsonl     | 0.20 (10)   | 200+8 | 0.828 | 174.1 / 120.5 / 514.6 / 1199 | 87.9  | 168 | 624 / 1125  | 446 | 28.5% |
| arm2_sat_tdd020.jsonl | 0.20 (10)   | 230+8 | 0.906 | 396.3 / 324.7 / 991.5 / 1912 | 154.8 | 168 | 864 / 1697  | 469 | 45.8% |
| arm2_sat_tdd055.jsonl | 0.55 (27.5) | 230+8 | 0.356 | 13.0 / 11.5 / 31.1 / 85      | 10.5  | 168 | 513 / 1214  | 500 | 2.5%  |

Headlines: (1) UL-side physics reproduces the 07-06 run to ~1% (network
independent of GPU state) — instrument is stable. (2) With a right-sized
model, UL queueing is 45.8% of agent e2e at saturation; reconfiguration cuts
e2e by 41% (864->513 ms) in one radio action. (3) Pressure shift QUANTIFIED
on dedicated GPU: infer 469->500 ms (+6.6%) at equal offered load when UL is
relieved (arrival pacing -> serving-side queueing). (4) P-K optimism 2.0x
(rho .83) / 2.6x (rho .91); within 24% at rho .36. Zero request errors.

## Superseded first run (2026-07-06, qwen3:1.7b, contaminated GPU)

Topology: WSL2 Ubuntu 22.04 client population -> tc/HTB token-bucket on eth0
(filtered to relay port, bfifo 5 MB) -> Windows relay :18090 -> Ollama
qwen3:1.7b on RTX 5090 (GPU shared with ~16 GB background workload, identical
across arms). Payload mixture per UE = U(0,50KB) sensing + 4 B/tk *
truncLogNormal(5,1.5,2048) prompt (ns-3 M/G/1 mode identical). lam_sense=0.2 Hz,
lam_agent=0.05 Hz, num_predict=60, seed=1, 540 s per arm, 60 s warmup discarded.
qwen3:4b unusable: ggml CUDA assert (cpy.cu ggml_nbytes <= INT_MAX) on current
Ollama build.

| arm file | UL share (rate) | UEs | realized rho | sense sojourn mean/p50/p95/max (ms) | P-K @realized moments (ms) | agent n | agent e2e mean/p95 (ms) | infer mean (ms) | UL+net mean (ms) | UL share of e2e |
|---|---|---|---|---|---|---|---|---|---|---|
| arm_tdd020.jsonl     | 0.20 (10 Mbit)   | 200+8 | 0.828 | 173.2 / 118.4 / 516.2 / 1179 | 87.7  | 156 | 1859 / 2343 | 1685 | 174 | 9.4% |
| arm_sat_tdd020.jsonl | 0.20 (10 Mbit)   | 230+8 | 0.906 | 399.1 / 329.5 / 990.1 / 1700 | 154.1 | 155 | 2064 / 2552 | 1688 | 377 | 18.2% |
| arm_sat_tdd055.jsonl | 0.55 (27.5 Mbit) | 230+8 | 0.356 | 13.4 / 11.9 / 31.6 / 100     | 10.5  | 157 | 1943 / 2460 | 1929 | 14  | 0.7% |

Errors: 0 in all arms (3 malformed log lines per arm from interleaved writes,
excluded). Rate calibration: 2 MB upload in 1.754 s = 9.57 Mbps at 10 Mbit
setting.

Decision bound: dynamic TDD reconfiguration (UL symbol share 0.20 -> 0.55)
relieves the UL bottleneck with a real serving stack in the loop.
Outcome: mean UL sojourn 399 -> 13 ms (30x), UL share of agent e2e
18.2% -> 0.7%, inference component ~constant (1.69 -> 1.93 s; +14%,
consistent with faster arrivals pressuring the shared GPU; not quantified
due to background GPU load).

Abstraction check: measured sojourn exceeds P-K (realized moments) by
2.0x at rho=0.83 and 2.6x at rho=0.91 (TCP handshake/slow-start inside the
shared queue); agreement within 28% at rho=0.36. Consistent with the paper's
restriction of quantitative M/G/1 claims to rho <= 0.75.

Repro: shape.sh set {10|27.5} 18090; relay_win.py 18090 (Windows);
ue_client.py --host 172.28.48.1 --n-bg {200|230} --n-llm 8 --lam 0.2
--lam-llm 0.05 --dur 540 --seed 1 --model qwen3:1.7b --num-predict 60;
analyze.py {10|27.5} 60 <file>. Shaping cleared after runs.
