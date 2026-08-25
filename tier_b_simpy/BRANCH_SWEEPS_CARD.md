# Enforced-admission, downlink-pressure, and state-headroom sweeps: frozen card

Status: design frozen before the confirmatory runs on 2026-08-03.
Extends `GATE_CONTROL_SWEEP_CARD.md`. Same simulator, same truth type
(abstract SimPy discrete-event model; not NR PHY, E2AP, or over-the-air).

## Code-change discipline

Three mechanisms are added to `oran_mmas_sim.py`, all inactive by default:
downlink utilization tracking plus a downlink-margin projection in the gate,
admission enforcement on the request path, and a finite application-state
store with stateless fallback for AFS-C. Before any confirmatory run, the
four arms of the 2026-08-01 gate-control sweep must reproduce the stored
N=220 seed-0 rows bit exactly under the changed code. If they do not, the
change is fixed or the affected stored results are re-run in full; mixing is
forbidden.

## Sweep A: enforced admission (saturated region)

- Question: when the accelerator is saturated and the gate holds the radio
  target, does enforcing the admission output improve the served requests,
  and at what blocking cost?
- Grid: every integer agent population 180 through 260 (81 points).
- Arms: `tdd_joint` (advisory admission, existing behavior) and
  `tdd_joint_enforced` (identical except requests from agents excluded by the
  latest admission evaluation are blocked before uplink).
- Pairing and seeds: common seeds 0 through 4; 10 s warm-up + 60 s window.
- Endpoints, paired Student-t 95 percent over five seeds: served mean and p95
  completed-response latency; service-level attainment counting blocked
  requests as misses; blocked fraction (diagnostic).
- Interpretation: enforcement helps only if served latency or all-request
  attainment improves with a resolved interval. Blocking that improves served
  latency while collapsing attainment is a trade-off, reported as such.

## Pilot disclosure (2026-08-03, before the confirmatory runs)

Seed-0 pilots checked the three mechanisms. Two design changes came out of
the pilot, both recorded here before any confirmatory run: (a) a raise-time
one-step downlink projection alone cannot catch downlink congestion that
builds after an early uplink grant, so the gate gains a downlink-pressure
step-down (measured downlink utilization above the 0.85 ceiling returns the
ratio one step toward baseline, checked every control tick); (b) the
state-store reservation is the session's peak retained context, taken at
session start. Pilot numbers support no claim. After both changes, the four
stored 2026-08-01 gate-control arms again reproduce bit exactly at spot
checks (N, seed) = (220,0), (140,2), (100,4).

## Sweep B: downlink pressure (dl_heavy scenario)

- Question: when responses carry artifact payloads and the downlink is close
  to its capacity, does the unguarded uplink reallocation relocate congestion
  to the downlink, and does the downlink-margin projection decline it?
- Scenario `dl_heavy`: agents with index mod 10 < 3 attach an artifact of
  Uniform(0.5, 2.0) MB to every response; all other traffic unchanged.
- Grid: every integer agent population 100 through 200 (101 points).
- Arms: `baseline`, `tdd` (no margin checks), `tdd_joint` (gate with
  accelerator and downlink projections).
- Pairing and seeds: common seeds 0 through 4; 10 s warm-up + 60 s window.
- Endpoints as in the gate-control card (paired mean and p95, attainment),
  plus mean downlink delay and final uplink ratio as diagnostics.
- Interpretation: the branch is demonstrated only if the unguarded arm shows
  interval-resolved degradation attributable to downlink queueing at points
  where the gated arm does not.

## Sweep C: state headroom (finite state store)

- Question: how do AFS-C eligibility and the uplink saving fall as the
  application-state store shrinks, realizing the fourth coordinate of the
  screen?
- Fixed population N=140, mode `full`, seeds 0 through 4.
- State-store capacity sweep: 0.25, 0.5, 1, 2, 4, 8, 16, 32, and 64 GB,
  plus the unlimited reference.
- Mechanism: a pipeline session reserves its accumulated context state
  (gamma MB per token) at each stage; if the reservation fails at session
  start, that session runs stateless for the whole cycle and is counted as a
  fallback.
- Endpoints: AFS-C session hit rate, total pipeline uplink bytes, mean
  completed-response latency (diagnostic).
- Interpretation: a monotone hit-rate and uplink-byte curve versus capacity
  demonstrates the state coordinate governing AFS-C eligibility. No latency
  claim is made from this sweep.

## Post-run audit notes (2026-08-05, cross-model audit)

- Sweep A's `sla_rate_all` denominator (completed + blocked) omitted
  accepted-but-unfinished requests. A generated-request counter was added
  (bit-exact dynamics verified) and both arms rerun as
  `run_audit_fixes.py` Sweep A2: on the generation denominator, enforced
  minus advisory attainment is +1.8 pp median, 36 resolved improvements and
  none resolved worse. The earlier N=257 resolved degradation was an
  artifact of the flawed denominator. Warm-up boundary mismatch between
  generation and completion counting is under one percent.
- Sweep B reused seed 0 after the pilot that motivated the step-down rule.
  Fresh seeds 5 through 9 (Sweep B2, rule frozen) confirm the pattern: 50 of
  101 unguarded resolved degradations against 11 gated; median downlink
  delay 128/388/139 ms for baseline/unguarded/gated.
- Sweep C's total-window uplink payload endpoint is confounded by
  closed-loop completion counts and is recorded as unresolved. Per completed
  pipeline, payload falls monotonically from 31.4 to 26.9 KB as capacity
  grows (one 0.3 KB inversion at the two smallest capacities). The state
  coefficient is per-token serving-tier pricing, an assumed parameter now
  stated in the manuscript.

## Outputs

`results_branch_sweeps_20260803/` with one JSON per sweep and a combined
summary produced by `summarize_branch_sweeps.py`.
