# Software-stack health ledger — 2026-07-18

This ledger records what was actually executed on this host. `PASS` means that
the named layer and endpoint passed; it does not mean that every optional
feature of the package was tested. All RAN paths use an analytical channel,
ZMQ, RFsimulator, a software tunnel, or an in-process model. Nothing here is an
over-the-air measurement.

| Stack | Executed test | Result | Scope and remaining boundary |
| --- | --- | --- | --- |
| CPython 3.13.3 / SimPy 4.1.1 | Rechecked the frozen all-integer 180--260 population summary, five paired seeds per integer and arm | **PASS** — all 81 points complete; no interval-resolved improvement under the compute-bound profile | Abstract queues and simulated GPU service only; not NR or E2AP |
| MATLAB R2026a, 5G/Communications/Wireless Network toolboxes | PUSCH link smoke plus formal result-shard verifier | **PASS** — AWGN sanity and all assertions passed; formal 208/238-UE shards verified | Static UMi/abstract PHY system result; no explicit K2/DCI feasibility |
| 5G-LENA nr v5.0 / ns-3-dev | Rebuilt/reran one anchor and reparsed all 15 logs (three TDD patterns, five seeds) | **PASS** — spread 11 versus spread 4 improves delivery and mean delay; p95 interval crosses zero | Detailed NR timing, but six CBR flows rather than the finite mMAS cohort; contiguous-4 remains a negative timing diagnostic |
| Sionna 1.2.2 / TensorFlow 2.21 | Aligned MCS-13/14088-bit-TB link sweep and CPU/GPU 1000-TB reproducibility microbenchmark | **PASS** — CPU/GPU block errors both 126; one-bit difference in 14,088,000 bits; RTX 5090 D is 3.05x faster for the 1000-TB batch | TensorFlow uses PTX JIT because it has no native `sm_120` image; cold/small GPU runs can be slower than CPU; MATLAB/Sionna absolute SNR differs by about 6 dB |
| Open5GS 2.8.0 / MongoDB 6.0.29 | AMF/SMF/UPF-backed SA sessions from three independent RAN/UE stacks | **PASS** — registration, PDU-session address assignment, and bidirectional user plane repeated across srsRAN, UERANSIM, and OAI | Software core in WSL NAT; one UE per counted run |
| srsRAN 25.10.0 + srsUE + Open5GS | ZMQ SA session, UE `10.45.0.14`, 4/4 ping each direction, and loaded UL iperf3 | **PASS** — full Contract S; 65.5 Mbit/s sender rate in the E2 session | Installed srsUE cannot select the separate n78/30-kHz profile; this role limitation is covered by OAI and is not generalized to the gNB |
| UERANSIM 3.3.0 + Open5GS | NG/RRC/NAS registration, PDU session, UE `10.45.0.11`, 4/4 ping each direction | **PASS** — full Contract S | Fast protocol emulator; no waveform, beamforming, scheduler, or K2 |
| OpenAirInterface develop `70508eb` + RFsimulator + Open5GS | n78/30-kHz gNB/nrUE SA session, UE `10.45.0.12`, 4/4 ping each direction | **PASS** — full Contract S in the target TDD/RFsim family | Highest startup/resource cost; software channel, not OTA |
| FlexRIC + srsRAN DU + Open5GS | One loaded SA session: Setup, KPM subscription, six indications, RC request and ACK | **PASS with two labelled compatibility guards** — `RRU.PrbTotUl=97%`; RC Style 2/Action 6 acknowledged at DU and xApp | DRB-throughput KPM remained unavailable; compatibility-patched, one UE, no closed-loop gain/stability claim |
| O-RAN SC release M + official ns-O-RAN | kind platform readiness and five simultaneous E2 Setup exchanges | **PASS with two labelled deployment repairs** — AppMgr/SubMgr/E2Mgr/RTMgr/E2Term Ready; 5/5 SCTP and successful Setup responses; simulator exit 0 | RTMgr service-port and E2Term RMR-route corrections are required; Setup only, no KPM/control/user plane |
| NORI on ns-3.42 + O-RAN SC M | `nori-sample` Setup plus bounded 10-s medium/saturation `nori-simple-rl-sched` smokes to `172.19.0.2:32222` | **PASS with explicit compatibility/operability profile** — Setup Response decoded; functions 200/300 accepted; saturation smoke delivered 35,184/35,200 packets and exited 0 without log flooding | Repaired Setup/lifecycle/user-plane smoke only; no KPM subscription, RC action, comparative gain, repeated trial, or paper performance evidence |
| NIST ns3-oran on ns-3.42 | Data-repository and keep-alive examples | **PASS** — 2/2 commands exited zero and exercised node/location/UE-cell lifecycle records | In-process abstract RIC model; no SCTP/ASN.1 E2AP, NR, 5GC, or OTA |
| Lean / mathlib | `lake build` and `check.ps1` in `E:\claude-lean-work\profilingai` | **PASS** — 8560 build jobs; no `sorry`, `admit`, custom axiom placeholder, or `sorryAx` | Deterministic algebra only. The entire Lean project, toolchain, dependencies, and cache remain on E: |

## Cross-stack consistency decision

- Full 5G-SA feasibility is independently consistent across srsRAN+srsUE,
  UERANSIM, and OAI: all close the same NG/RRC/registration/PDU-session/user-
  plane contract against Open5GS.
- Radio-bound direction is consistent between MATLAB and 5G-LENA for delivery
  and mean delay. The very large MATLAB p95 reduction is **not** reproduced by
  5G-LENA, so it remains a configuration-specific effect rather than a
  universal number.
- FlexRIC closes the loaded Setup→KPM→indication→RC→ACK path. O-RAN SC M is an
  independent platform-level Setup replication; NORI and NIST are secondary
  compatibility/model controls.
- Sionna and MATLAB agree on the MCS/TB/channel-family trend but not the
  absolute SNR scale. Their curves remain separate.

Machine-readable evidence is in `results/protocol_contract_summary.json`,
`results/backend_evidence_matrix.json`,
`results/sionna/cpu_gpu_consistency.json`, and
`results/nori_ns3_oransc_m/summary.json`.
