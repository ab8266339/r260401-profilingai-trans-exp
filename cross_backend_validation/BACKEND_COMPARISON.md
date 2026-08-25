# Cross-stack consistency, disagreement, and paper-use decision

The common contracts now pass at the layers each backend actually implements.
The backends agree on protocol feasibility and on the direction of a
radio-pressure intervention, but they do **not** agree on a universal tail
latency magnitude.  The disagreement is informative and must not be averaged
away.

## Result matrix

| Contract / backend | Counted result | Consistency contribution | Principal strength | Principal limitation |
| --- | --- | --- | --- | --- |
| R — MATLAB R2026a | At 208/238 total UEs, 4-to-11 UL slots raised completion by 35.7/42.6 points and reduced completed-cohort p95 by 94.1/96.9% | Strong positive direction at the target mMAS workload | Exact paired burst/cohort experiment and detailed censoring diagnostics | Static large-scale UMi plus abstract PHY; no explicit K2/DCI feasibility |
| R — 5G-LENA nr v5.0 | Five pairs: delivered-byte fraction +0.295 (95% CI 0.185–0.406), mean delay −44.85% (22.47–67.22%), p95 −12.34% (−13.60–38.28%) | Replicates delivery and mean-delay direction; does not resolve the tail effect | NR EESM, explicit TDD/K2/control timing, three-sector geometry | Six CBR flows rather than the mMAS finite cohort; first anchor disables fading/shadowing |
| R boundary — SimPy integer sweep | Every integer 180–260, five paired seeds; median full-minus-baseline mean/p95 +180.5/+523.8 ms, with ≈94% GPU-queue share | Shows the radio action stops helping once compute is the bottleneck | Dense predeclared population sweep and component-level queue attribution | Abstract radio/E2 and simulated GPU service; not a 5G implementation |
| R link sensitivity — Sionna 1.2.2 | MCS 13/14088-bit TB: BLER 0.105 at 9 dB; MATLAB BLER 0.135 at 15 dB. On MATLAB’s exact 12–17 dB grid, Sionna had 0/1200 TB errors. CPU/GPU block errors were both 126 over 1000 TB; GPU was 3.05x faster | Same TB, MCS, channel family, monotone waterfall; CPU/GPU result is reproducible; absolute MATLAB/Sionna SNR scale disagrees by about 6 dB | Fast independent coded-PUSCH/TDL receiver sweep; RTX 5090 D works for larger batches | Frequency-domain normalized channel, LS/LMMSE, no HARQ/queues; SNR axes cannot be merged; TensorFlow PTX JIT makes cold small GPU runs slower |
| S — srsRAN+srsUE+Open5GS | NG/RRC/registration/PDU session, UE `10.45.0.14`, and 4/4 bidirectional pings; 65.5 Mbit/s UL iperf3 in the E2 run | Full SA pass | Real protocol implementation, ZMQ RF, concurrent E2 control | One UE, virtual RF; installed srsUE cannot exercise n78/30 kHz TDD |
| S — UERANSIM+Open5GS | Full SA stages, UE `10.45.0.11`, and 4/4 bidirectional pings | Independently reproduces every Contract-S stage | Fastest startup and native per-session namespace | Abstract RAN/UE; no waveform, scheduler, beamforming, or K2 |
| S — OAI RFsim+Open5GS | n78/30 kHz full SA stages, UE `10.45.0.12`, and 4/4 bidirectional pings | Independently reproduces every Contract-S stage in the target TDD/RFsim family | Deep RRC/NAS/PDCP/RLC/MAC/PHY visibility | Highest startup/resource complexity; software channel, not OTA |
| E — srsRAN DU+FlexRIC | E2 Setup, KPM subscription, six loaded indications (`RRU.PrbTotUl=97%`), RC Style 2/Action 6 request and ACK in the same live SA session | Full Contract-E pass | Executable KPM/RC path tied to nonzero user-plane load | Two labelled FlexRIC compatibility guards; DRB throughput KPM remained unavailable |
| E — official ns-O-RAN+O-RAN SC M | 5/5 SCTP, Setup Requests, Setup Responses, and decoded successful outcomes; process exit 0 | Independent E2 Setup/platform replication | Real O-RAN SC AppMgr/SubMgr/E2Mgr/RTMgr/E2Term deployment | Setup-level only and two explicit deployment routing corrections |
| E controls — NORI / NIST ns3-oran | Compatibility-profiled NORI ns-3 receives a successful O-RAN SC M Setup response accepting functions 200/300; its repaired 10-s saturation RL smoke delivers 35,184/35,200 packets and exits 0; NIST repository and keep-alive examples 2/2 exit 0 | Secondary protocol/model controls | Cheap isolation of codec, platform, lifecycle, and basic user-plane failures | NORI has no KPM/control or repeated comparison here; NIST is in-process and has no networked E2AP |

All protocol/RAN executions use virtual RF, RFsimulator, software tunnels, or
analytical channels.  None is over-the-air evidence.

## What is genuinely consistent

1. **The 4-to-11 intervention has a positive radio-bound direction.** MATLAB
   and 5G-LENA both improve delivery/completion and mean delay.  This survives
   different workloads, topologies, and PHY abstractions.
2. **Slot count alone is not a sufficient treatment definition.** In 5G-LENA,
   four spread UL slots deliver traffic, while the four-contiguous diagnostic
   produces zero delivery because the complete flexible/control/K2 pattern is
   unusable.  The paper must freeze the full pattern.
3. **The 5G-SA protocol path is reproducible.** srsRAN, UERANSIM, and OAI all
   reach an Open5GS PDU session and pass bidirectional user-plane traffic.
4. **The E2 path is executable, not merely diagrammatic.** FlexRIC+srsRAN
   closes Setup→KPM→loaded indication→RC request→ACK.  The O-RAN SC M platform
   independently closes five Setup exchanges.
5. **Bottleneck awareness is necessary.** The SimPy integer sweep shows that
   the same control has no resolved benefit when GPU queueing dominates.

## Disagreements that constrain the claim

- MATLAB's 96.9% p95 reduction is not reproduced by 5G-LENA: the latter's p95
  interval crosses zero.  The defensible cross-stack statement is directional
  relief of a radio-bound path, not a universal 96.9% tail reduction.
- Sionna and MATLAB share MCS 13, 51 RB, 30 kHz, TDL-C, 300 ns, 30 Hz, and the
  exact 14088-bit TB, yet their comparable-BLER points are about 6 dB apart.
  Receiver processing, channel realization, waveform timing, noise scaling,
  and HARQ differ.  Their curves stay separate.
- srsRAN's installed srsUE rejects 30 kHz SCS, whereas OAI RFsim completes n78
  SA.  This is a frontend compatibility difference, not evidence that one
  target TDD pattern is universally invalid.
- FlexRIC's advertised RC support did not initially match the srsRAN DU
  profile.  The final run uses the DU-advertised Style 2/Action 6 slice-quota
  action and records both compatibility guards.  It is a patched
  interoperability result, not an upstream out-of-box claim.
- The newer NORI E2Sim archive reached O-RAN SC M over SCTP but did not produce
  a usable RAN inventory identity at E2Mgr. Binding NORI to the E2Sim variant
  already validated by official ns-O-RAN, plus the same three-byte PLMN
  profile, closes Setup. Three additional operability guards bound scheduler
  logging, restore the requested simulator stop time, and install the missing
  slice-to-RNTI map. The short RL smokes now deliver packets and exit cleanly,
  but remain repaired single-run health checks rather than comparative
  performance evidence.

## TCOM paper-use decision

- Main numerical evidence: MATLAB target workload + 5G-LENA independent NR
  direction check + SimPy compute-bottleneck boundary.
- Short model-sensitivity evidence: Sionna, explicitly separated from the
  system curves.
- Protocol feasibility evidence: the three Contract-S full passes, the loaded
  FlexRIC Contract-E full pass, and the O-RAN SC M Setup pass.  Do not convert
  their setup times, ping RTTs, or iperf rate into the paper's inference-latency
  endpoint.
- Internal controls: NORI and NIST remain in the evidence matrix; they do not
  strengthen the quantitative performance claim.

The generated machine-readable sources are
`results/protocol_contract_summary.json` and
`results/backend_evidence_matrix.{csv,json}`.
