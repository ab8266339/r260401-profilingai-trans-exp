# Cross-stack test contract — 2026-07-18

This card freezes three common tasks before the next repair and rerun cycle.
Backends at different abstraction levels are not required to emit the same
metric, but a backend may only be called a pass for the stages it actually
executes.

## Contract R: radio-pressure intervention

- Carrier anchor: 20 MHz at 3.5 GHz with 30 kHz subcarrier spacing where the
  backend supports those controls.
- Baseline: four usable uplink slots in a 20-slot period.
- Intervention: eleven usable uplink slots in the same period.
- Within-backend controls: identical topology, workload, scheduler, seed and
  observation window for the paired arms.
- Common outputs: offered bytes, delivered/completed bytes, delivery/completion
  fraction, mean delay and p95 delay. A backend that lacks queues reports only
  its PHY endpoint and is not included in the system-delay comparison.
- System pass: both arms execute without zero-delivery artifacts caused by an
  invalid K2/DCI pattern, and every common endpoint is recorded.
- Link anchor: MCS 13, one layer, fixed transport-block definition and an
  explicitly named channel/receiver model. MATLAB and Sionna curves remain
  separate unless the channel and receiver assumptions are aligned.

## Contract S: 5G standalone session

- Core: the installed Open5GS 2.8.0 and MongoDB subscriber database.
- UE identity: one pre-provisioned test subscriber; each RAN/UE pair must use a
  non-conflicting virtual-RF or tunnel endpoint.
- Required stages: NG Setup, cell acquisition, RRC connection, authentication,
  registration accept, PDU-session establishment, assigned UE address and one
  bidirectional user-plane reachability check.
- Full pass: all stages including bidirectional user plane.
- Control-plane pass: through PDU-session establishment, with the missing data
  plane named explicitly.
- DORA-only or no-core execution is a RAN/PHY smoke test, not a 5G-SA pass.

## Contract E: O-RAN control path

- Required stages: SCTP association, E2 Setup request decode, E2 Setup response,
  KPM subscription response, at least one decoded KPM indication, one RC/control
  request and a positive acknowledgement at both RAN and controller when the
  implementation exposes both logs.
- Full pass: every required stage.
- Setup pass: association plus Setup response only.
- Model-only pass: an in-process abstract RIC may validate lifecycle/repository
  semantics but may not be counted as E2AP interoperability.
- No throughput or controller-gain claim is permitted when KPM values remain
  zero or when the control request is acknowledged but not tied to a measured
  data-plane change.

## Repair discipline

- Preserve the unmodified upstream result before applying any compatibility
  patch or deployment override.
- Record source commit, configuration, command, exit status and exact failed
  stage for every backend.
- A compatibility-patched run is labelled as such; it does not replace the
  upstream interoperability result.
- Protocol stacks use virtual RF or software channels only. Nothing in this
  campaign is over-the-air evidence.
- Lean artifacts remain exclusively in
  `E:\claude-lean-work\profilingai`; this test campaign creates no Lean files
  in the paper tree.
