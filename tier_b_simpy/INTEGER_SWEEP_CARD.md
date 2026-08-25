# Integer population sweep: frozen experiment card

Status: design frozen before the confirmatory run on 2026-07-17.

## Question and evidence boundary

This experiment asks whether the existing abstract joint radio--compute control
loop improves completed-response latency or service-level attainment as the
device population changes continuously through the congestion transition.  It
is a SimPy discrete-event model result.  It is not an NR PHY, E2AP, Open5GS,
over-the-air, or production-RIC measurement.

## Frozen design

- Total device count: every integer from 180 through 260, inclusive (81
  predeclared points; no post-run point selection).
- Arms: `baseline` and `full`, using the existing model without parameter
  changes.
- Pairing: common seed within each device count and arm.
- Seeds: 0, 1, 2, 3, and 4.
- Simulated time: 10 s warm-up followed by 60 s observation.
- Raw output directory: `results_integer_180_260/`.
- Software: CPython 3.13.3, SimPy 4.1.1, NumPy 2.3.4.

## Frozen endpoints

For each device count, the primary paired endpoints are:

1. full-minus-baseline p95 completed-response latency in milliseconds;
2. full-minus-baseline mean completed-response latency in milliseconds;
3. full-minus-baseline service-level attainment in percentage points.

Secondary diagnostics are completed-response count, uplink-delay share,
GPU-queue-delay share, the final TDD uplink ratio, xApp decisions, and rApp
decisions.  A paired Student-t 95% interval over the five seeds is reported for
each primary endpoint.  Grid-level reporting is fixed to the median point
estimate and the number of integer points whose interval indicates improvement,
degradation, or an unresolved sign.  No single device count becomes the
headline merely because it has the largest effect.

## Interpretation rule

Negative latency differences and positive service-level differences favor the
full arm.  If the final TDD ratio remains unchanged while GPU queueing dominates
latency, the correct conclusion is a compute-bottleneck boundary, not a radio
control gain.  Results from this model must remain separate from MATLAB,
5G-LENA, Sionna, srsRAN/Open5GS, and O-RAN SC protocol evidence.

