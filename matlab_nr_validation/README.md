# MATLAB R2026a paired TDD NR validation

This directory contains the radio-side system experiment for the mMAS
study. It is independent of the manuscript source and does not by itself
constitute over-the-air, RIC/E2, or Open5GS validation.

## Model pinned by `umi-static-shadow-v2`

- MATLAB R2026a Update 3 with 5G Toolbox and Wireless Network Toolbox.
- One `nrGNB` and static `nrUE` nodes at 3.5 GHz, 20 MHz, 30 kHz SCS,
  51 RB, SISO, 16 HARQ processes, and proportional-fair scheduling.
- Paired TDD arms with 4 or 11 complete UL slots per 20-slot/10 ms pattern.
- Public `nrPathLoss` implementation of TR 38.901 UMi path loss. A UMi
  street-canyon LOS/NLOS draw and a Gaussian shadowing draw are generated
  once per UE, written to CSV, and reused unchanged by both TDD arms.
- Abstract PHY, adaptive MCS, RLC/MAC/PHY statistics, and a version-pinned
  `ScheduledResources` grant observer for grant- and PRB-weighted UL MCS
  histograms. The event is hidden in R2026a, so every shard records source
  SHA-256 values and MCS totals are cross-checked against MAC grant and RB
  counters before publication.
- Common random numbers across each pair: packet sizes, arrival intervals,
  UE positions, link states, and top-level MATLAB seed are identical.

The NR traffic API invokes every source at time zero. To avoid an artificial
synchronized multi-megabyte request burst, each UE emits one 1-byte priming
SDU at time zero and waits one exponential interval before its first real
request. Priming markers are excluded from all measurement cohorts and are
checked explicitly by the verifier.

Each request has
`U(0,50000) + 4*min(LogNormal(5,1.5),2048)` bytes. Because the RLC API accepts
at most 9000 bytes per SDU, a request is emitted as a burst of SDUs no larger
than 9000 bytes. Reported latency is therefore SDU latency, not HTTP request
completion time.

## Finite-window endpoint

The experiment is deliberately a paired finite-window experiment, not a
steady-state estimate:

1. warm up;
2. admit the measurement-generated SDU cohort;
3. stop all new application traffic;
4. drain for a fixed duration.

The primary endpoint reports accepted cohort size, completion fraction,
unfinished-or-lost SDUs/bytes, and mean/p95 latency conditional on cohort
completion by the finite drain deadline. Completed bytes are also normalized
by the generation-window duration; this quantity is not called instantaneous
drain goodput. Receiver-window conditional latency is retained only as a
secondary metric. Attempted and accepted offered rates, receiver-window
goodput, TX-minus-RX counter imbalance, application overflow, RLC reassembly
PDU-drop fraction, BLER, HARQ, RB use, and MCS distribution are reported
together so overload cannot be hidden by survivor latency.

| Profile | UEs | Rates (background/inference) | Warm + generation + drain | Seeds |
|---|---:|---:|---:|---:|
| `smoke` | 2+1 | 5 / 2 Hz | 0.1 + 0.4 + 0.5 s | 1 |
| `quick` | 8+2 | 2 / 1 Hz | 0.5 + 2 + 2 s | 1 |
| `medium` | 48+4 | 0.5 / 0.2 Hz | 2 + 8 + 10 s | 1 |
| `formal` | 200+8 and 230+8 | 0.20 / 0.05 Hz | 3 + 8 + 10 s | 1--5 |

## Run independent shards

The machine's default `matlab` still points to R2025a. Always use the wrapper,
which discovers and verifies R2026a:

```powershell
cd E:\ml-work\R260401-profilingai-trans\matlab_nr_validation
powershell -ExecutionPolicy Bypass -File .\run_r2026a.ps1 -Profile smoke
powershell -ExecutionPolicy Bypass -File .\run_r2026a.ps1 -Profile formal -BackgroundUEs 200 -Seeds 1
```

For the formal design, run the ten `(background load, seed)` cases as
independent processes. Each process writes only its own atomic case shard:

```powershell
foreach ($load in 200,230) {
  foreach ($seed in 1..5) {
    powershell -ExecutionPolicy Bypass -File .\run_r2026a.ps1 `
      -Profile formal -BackgroundUEs $load -Seeds $seed
  }
}
```

Those commands are shown serially for clarity. Parallel execution is safe
only when each process owns a distinct case. A sibling lock rejects duplicate
concurrent runs of the same case. A completed shard is reused only after its
manifest, schema, metrics, source hash, and configuration hash pass again.

Do not point parallel jobs at a shared aggregate output. After every expected
shard exists, perform one explicit merge process:

```powershell
powershell -ExecutionPolicy Bypass -File .\merge_r2026a.ps1 -Profile formal
```

The complete merge expects exactly `2 loads × 5 seeds × 2 arms`. It rejects
missing or extra cases, mixed experiment versions, changed invariant config,
changed source, unreliable p95/MCS data, or a reduced paired-seed count.

For the frozen `umi-static-shadow-v2` formal shards completed on 2026-07-14,
the original merger verified all ten shards but MATLAB R2026a rejected its
final vector-valued `assert` condition before atomic publication. The twelve
fingerprinted source files and every shard were left unchanged. The formal
aggregate was therefore published with the separately hashed compatibility
post-processor:

```powershell
powershell -ExecutionPolicy Bypass -File .\merge_r2026a_compat.ps1 -Profile formal
```

It changes only that final check to reduce over all numeric table cells with
`all(...,"all")`. `aggregate_manifest.json` keeps the frozen shard bundle
fingerprint and records separate SHA-256 values for the original merger, the
unchanged aggregation function, the compatibility tool, and its wrapper.

## Outputs

One case is published under:

```text
results/shards/umi-static-shadow-v2/<profile>/bgNNNN_seedNNNN/
```

It contains:

- `summary.csv/json` and `results.mat`;
- `paired_differences.csv` and single-case CI schema;
- `case_manifest.json` with config and source SHA-256 fingerprints;
- `link_states/link_state.csv` with every LOS/NLOS and shadowing realization;
- `arm_audits/sdu_latencies_<arm>.csv` with exact per-SDU generation,
  reception, length, latency, and cohort flags;
- `arm_audits/ul_mcs_histogram_<arm>.csv` with 28 MCS bins and grant/PRB,
  new-transmission, and retransmission counts.

The explicit merge writes `paired_ci95.csv` using seed-level paired
Student-t intervals. Five formal seeds give four degrees of freedom. A
single smoke shard intentionally has no estimable confidence interval.

## Verified execution and runtime calibration (2026-07-14)

Final v2 smoke, seed 1:

| Arm | Completed-cohort mean / p95 | Completion | Normalized delivered rate | BLER | Grant/PRB-weighted mean MCS | Wall |
|---|---:|---:|---:|---:|---:|---:|
| 4/20 UL slots | 22.363 / 35.776 ms | 1.000 | 2.083 Mbps | 0 | 13.87 / 13.87 | 7.8 s |
| 11/20 UL slots | 12.571 / 18.301 ms | 1.000 | 2.083 Mbps | 0 | 13.75 / 13.77 | 5.6 s |

An earlier v1 runtime-only medium case (48+4 UEs, 10 simulated seconds)
took 620.5 s for 4/20 and 495.4 s for 11/20. Linear UE-time projection of
the v2 21-second formal window is roughly 2.6--3.0 hours per paired case, or
about 28 hours serial for ten cases, before allowing for exact-event logging
overhead and nonlinear scaling. This is a planning estimate, not a result.

## Claim boundary

The channel realization is static large-scale UMi LOS/NLOS plus independent
static shadowing. It does not include time-correlated shadowing, CDL/TDL
small-scale fading, mobility, inter-cell interference, multi-cell operation,
live TDD reconfiguration, RIC/E2 control, core-network signaling, or RF
hardware. The separate `pusch_link_validation/` experiment is an independent
link-level cross-check; it is not silently substituted for this system
model. No manuscript number should be replaced until all formal shards,
paired confidence intervals, censoring/completion results, BLER/HARQ, and MCS
distributions are jointly reviewed.
