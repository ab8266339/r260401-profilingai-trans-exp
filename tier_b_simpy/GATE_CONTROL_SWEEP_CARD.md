# Four-arm gate-control integer sweep: frozen experiment card

Status: design frozen before the confirmatory run on 2026-08-01.
Extends `INTEGER_SWEEP_CARD.md` (2026-07-17) by adding the two control arms
and widening the integer grid downward to cover the congestion transition.

## Question and evidence boundary

Does the bottleneck gate change outcomes relative to (a) no radio action and
(b) a naive uplink-triggered reallocation that ignores compute margins, as the
agent population moves through the accelerator-congestion transition? What
mechanism produces the full-arm tail increase recorded in the 2026-07-17
baseline/full sweep? This is a SimPy discrete-event model result under the
capacity-corrected GPU model (2026-07-14). It is not an NR PHY, E2AP,
Open5GS, over-the-air, or production-RIC measurement.

## Pilot disclosure

A single-seed (seed 0) pilot at N = 60 to 260 step 20 located the congestion
transition and confirmed the arms differ. Pilot results calibrate nothing and
support no claim; all reported statistics come from the frozen run below.
The 2026-07-04 four-arm ablation (`results_ablation_20260704/`) predates the
capacity-corrected GPU model and is superseded; it must not be mixed with
current-model results.

## Frozen design

- Total agent count: every integer from 100 through 260, inclusive (161
  predeclared points; no post-run point selection).
- Arms, all four at every point:
  - `baseline`  — no control loops, no persistence
  - `tdd`       — uplink-triggered dynamic TDD only (gate disabled)
  - `tdd_joint` — joint radio-compute gate + advisory admission
  - `full`      — gate + advisory admission + AFS-C context persistence
- Pairing: common seed within each agent count across all four arms.
- Seeds: 0, 1, 2, 3, and 4.
- Simulated time: 10 s warm-up followed by 60 s observation.
- Model code: `oran_mmas_sim.py` at its 2026-07-14 capacity-corrected state,
  no parameter changes. Determinism verified by bit-exact reproduction of the
  stored N=220 seed-0 baseline row before this run.
- Raw output directory: `results_gate_control_100_260/`.
- Software: CPython 3.13.3, SimPy 4.1.1, NumPy 2.3.4.

## Frozen endpoints

Primary paired endpoints, per agent count, paired Student-t 95% interval over
the five seeds:

1. tdd-minus-baseline mean and p95 completed-response latency (ms);
2. tdd_joint-minus-baseline mean and p95 completed-response latency (ms);
3. full-minus-tdd_joint mean and p95 completed-response latency (ms);
4. full-minus-baseline mean and p95 completed-response latency (ms).

Secondary diagnostics: completed-response count, mean uplink delay, mean
accelerator-queue delay, mean downlink delay, final TDD uplink ratio.

Grid-level reporting is fixed to the median point estimate over predeclared
regions plus the count of integer points whose interval indicates
improvement, degradation, or an unresolved sign. Regions are predeclared as
the transition region (100 to 179) and the saturated region (180 to 260),
matching the 2026-07-17 grid boundary. No single agent count becomes a
headline merely because it has the largest effect.

## Interpretation rule

The gate helps only if, in the saturated region, the naive arm shows a real
cost (latency, delivered downlink capacity, or wasted uplink ratio) that the
gated arm avoids, or if, in the transition region, the gated arm matches the
naive arm's gain. Identical tdd and tdd_joint traces in the transition region
mean the gate correctly permits; identical tdd_joint and baseline traces in
the saturated region mean the gate correctly holds. The full-minus-tdd_joint
contrast isolates the persistence mechanism behind the 2026-07-17 tail
increase. Results from this model remain separate from MATLAB, 5G-LENA,
Sionna, srsRAN/Open5GS, and O-RAN SC protocol evidence.
