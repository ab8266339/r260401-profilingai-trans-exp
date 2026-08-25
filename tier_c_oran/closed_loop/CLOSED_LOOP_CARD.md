# Closed-loop gate demonstration: frozen experiment card

Status: design frozen before the confirmatory run on 2026-08-03.

## Question and evidence boundary

Does the bottleneck gate's decision cycle execute end to end on live Open RAN
interfaces: radio telemetry in over E2 (KPM), edge telemetry in over a
separate edge adapter, a gate decision every control tick, and an
acknowledged E2 control (RC Style 2, Action 6) out when the decision is an
action? This is software-radio protocol execution with an emulated edge
cluster. It is not an over-the-air run, it does not verify that the DU
scheduler applies the acknowledged quota, and it makes no performance claim.
The two earlier FlexRIC compatibility guards remain in force.

## Topology

- Open5GS core (systemd services, WSL Ubuntu-22.04).
- srsRAN gNB (ZMQ virtual radio) with the E2 overlay + srsue in a network
  namespace, one live PDU session; the stored cross-backend configs are
  reused unchanged.
- FlexRIC nearRT-RIC; xapp_kpm_moni runs continuously as the radio-telemetry
  stream (E2AP KPM indications, RRU.PrbTotUl).
- MEC GPU emulator (same timing model as `mec/mec_gpu_emulator.py`, rebuilt
  on the Python standard library; 4 GPUs x 8 slots) as the edge adapter,
  localhost REST. A load injector (30 concurrent /infer clients, 33B tier,
  2000/1000 tokens) saturates it on demand.
- Gate runner (Python, 1 s control tick): uplink pressure = latest PrbTotUl
  from the KPM log; compute margin = active/max from the MEC /status
  endpoint. Application-context bytes never touch the E2 path.

## Gate rule (fixed before the run)

- PrbTotUl > 60 percent and GPU utilization < 0.80 and quota at baseline:
  RAISE, send RC slice quota (min 10, max 90, dedicated 80).
- PrbTotUl > 60 percent and GPU utilization >= 0.80: DECLINE with a
  compute-hold reason code; no control message is sent.
- PrbTotUl < 30 percent and quota raised: RELEASE, send RC slice quota
  (min 10, max 50, dedicated 40).
- Otherwise: HOLD (no-op tick). The RC xApp is the parameterized variant
  `flexric_rc_slice_control_param.c`; every send must return the synchronous
  acknowledged response to count.

## Predeclared scenario (single run, phases annotate but never drive decisions)

- Phase 1 (about 0 to 35 s): uplink iperf3 flood AND injector on. Ground
  truth: DECLINE ticks, zero controls sent.
- Phase 2 (about 35 to 70 s): injector off, uplink flood continues. Ground
  truth: exactly one acknowledged RAISE, then hold-at-target ticks.
- Phase 3 (about 70 to 95 s): uplink flood off. Ground truth: exactly one
  acknowledged RELEASE after PrbTotUl falls below the low threshold.

## Pilot disclosure (2026-08-03, before the confirmatory run)

The first full run (preserved in `results_closed_loop_pilot_20260803/`)
exposed a scenario-construction flaw, not a gate flaw: the MEC /status
endpoint reported instantaneous slot occupancy, and the synchronous
injector's request gaps let that instantaneous value dip below the 0.80
ceiling while PrbTotUl was high, so the gate raised during phase 1 exactly
as its rule prescribes. Two changes follow, both disclosed here before the
confirmatory run: (a) the MEC stub now reports a 2-second time-integrated
window occupancy, the same windowed shape as the KPM PrbTotUl percentage;
(b) the injector uses 48 threads with 70B-tier 4000/2000-token requests so
the window occupancy stays saturated. The gate rule itself is unchanged.
Pilot numbers support no claim.

## Endpoints

1. Decision correctness: every tick's decision class matches the phase
   ground truth (stale-telemetry ticks are reported, not scored).
2. Both controls acknowledged (synchronous response true, exit 0).
3. Loop counts: KPM indications consumed, ticks executed, controls sent.
4. Latency diagnostics: telemetry-to-decision per tick; action-to-ack per
   control (includes xApp attach time, reported as such).

## Post-run audit notes (2026-08-05, cross-model audit)

Recorded deviations, not silent rewrites. (a) The summarizer scored the 60
ticks whose decisions fall in the oracle's classes; 22 further
fresh-telemetry no-op ticks were outside those classes, and the manuscript
now reports both numbers. (b) The scripted success check verified only the
two acknowledgments; decision correctness was checked in analysis rather
than in code. (c) `decide_ms` is controller processing time per tick, not
telemetry-to-decision time, and is relabeled accordingly. (d) The evaluated
rule covers the uplink-pressure and compute-margin branches only, and the
manuscript names it as such. (e) The plant loop is not closed through the
action: scheduler application of the acknowledged quota and an
action-induced telemetry response remain unverified.

## Success criterion

The run succeeds if endpoints 1 and 2 hold. A partial run (for example the
RAISE acknowledged but the RELEASE window missed) is reported as partial,
not silently rerun; reruns are recorded.
