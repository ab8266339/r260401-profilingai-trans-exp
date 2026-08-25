# Joint four-margin gate and radio-dominant persistence A/B: frozen card

Status: design frozen before the confirmatory runs on 2026-08-05. Same
simulator and truth type as the earlier cards (abstract SimPy model).
Motivated by the cross-model audit findings 1 and 10: implement the gate of
Section IV as one controller, and decide the end-to-end persistence question
in a regime where the radio, not the accelerator, dominates.

## Code changes and pilot disclosure

A `joint` mode evaluates, at every uplink-pressure tick, the projected
downlink margin, the compute margin, and the state margin together, with
per-margin hold counters logged. The state margin is operationalized as
headroom exhaustion: one or more session-state reservation failures within
the last two seconds. A seed-0 pilot showed the earlier occupancy-threshold
form cannot bind (lumpy reservations keep occupancy below any near-full
threshold while requests are being denied), so the reservation-failure form
was adopted before this card froze; that is the disclosed pilot-driven
change. Counter attribution follows the fixed order compute, downlink,
state when several margins bind at once. `persist_only` runs AFS-C
persistence with no control loops at all. After the changes, the stored
2026-08-01 arms reproduce bit exactly at spot checks, and `joint` reproduces
stored `tdd_joint` bit exactly at (140,2), (220,0), (100,4).

## Run R1: reduction identity on the default grid

- Every integer N 100 through 260, seeds 0 through 4, arm `joint`.
- Endpoint: the count of (N, seed) rows bit-identical to the stored
  `tdd_joint` rows on count, mean, p95, and final ratio. The claim
  "the joint controller reduces to the tested branch behavior wherever the
  extra margins never bind" requires 405 of 405.

## Run R2: reduction identity under dl_heavy

- Every integer N 100 through 200, seeds 0 through 4, arm `joint`,
  scenario dl_heavy; identity against the stored dl_heavy `tdd_joint` rows.
  Requires 505 of 505.

## Run R3: state-margin branch holds the radio target

- Every integer N 70 through 100, seeds 0 through 4, store 500 MB,
  arms `joint_full` (state guard on) and `full` (state guard off).
- Endpoints: fraction of points where `joint_full` logs at least one
  state-hold; paired final-ratio difference (joint_full minus full, expected
  nonpositive); raises per run in both arms. This realizes the rule that
  exhausted state headroom leaves the radio target unchanged.

## Run P: persistence A/B in the radio-dominant regime

- Every integer N 60 through 130, seeds 0 through 4, arms `baseline` and
  `persist_only` (persistence isolated; no controller in either arm).
- Primary endpoint: paired pipeline-agent mean completed-response latency,
  per point, Student-t 95 percent intervals, sign counts, and region
  medians. Regions predeclared: radio-dominant 60 through 100 and mixed 101
  through 130, split where the seed-0 pilot showed the accelerator queue
  overtaking the uplink delay.
- Secondary endpoints, reported regardless of sign: pipeline p95, pipeline
  service-level attainment, overall mean latency, completed pipeline count.
  The pilot showed the low-load pipeline p95 can move against the mean;
  whatever the confirmatory sign, it is reported.
- Interpretation: the state-path recommendation for deep pipelines is
  supported end to end only if the primary endpoint improves with resolved
  intervals in the radio-dominant region and the secondary endpoints do not
  resolve against it there.

## Seed extension E1 (declared 2026-08-05, after the primary Run P readout)

The primary five-seed Run P returned unresolved intervals at every point in
both regions (radio-dominant medians favorable at -28.7 ms pipeline mean,
mixed-region medians unfavorable). Per this note, Run P is extended with
seeds 5 through 24 (20 fresh seeds, 2,840 additional runs), same grid, same
arms, same endpoints, to tighten the intervals. The five-seed result
remains the card's primary readout and is reported either way; the pooled
25-seed result is reported as the labeled extension. No other change.

E1 outcome (recorded after the run): the pooled 25-seed contrasts stay
unresolved at every point and endpoint except one mixed-region
service-level point resolving as degradation. Radio-dominant pooled medians
shrink to +2.0 ms pipeline mean (the five-seed -28.7 ms median was noise).
Reading per the interpretation rule: the end-to-end persistence effect in
the radio-dominant regime is a wash in both directions, so the state-path
recommendation rests on the payload saving, the absence of a resolved
end-to-end price outside accelerator saturation, and the margin screen,
not on a latency win.

## Outputs

`results_joint_persistence_20260805/` with raw JSON and a summary from
`summarize_joint_persistence.py`.
