# Cross-backend validation

This directory compares simulation backends without treating unlike abstraction
levels as interchangeable measurements.  The shared question is whether a more
uplink-capable TDD configuration improves delivery under radio pressure, and
which mechanisms change the magnitude or even the feasibility of that action.

## Backend roles

| Backend | Role in this study | Truth label | Directly comparable outputs |
| --- | --- | --- | --- |
| MATLAB R2026a Wireless Network Toolbox | Existing single-cell paired system result with mMAS burst traffic | simulated, abstract PHY | accepted-cohort completion and latency |
| 5G-LENA nr v5.0 | Independent NR MAC/PHY, K2/control timing, 3-sector UMi topology and PF scheduler | simulated, system-level NR | delivered-byte fraction, user-perceived throughput and packet delay |
| Sionna 1.2.2 | Independent waveform/channel sensitivity check | simulated, link-level PHY | BLER/BER versus SNR; no device-population claim |
| srsRAN/OAI/UERANSIM + Open5GS | Independent Contract-S executions | emulated protocol stack | NG/RRC/registration, PDU session and bidirectional user plane |
| FlexRIC and O-RAN SC M | Contract-E execution at DU and platform levels | protocol-level O-RAN | E2 Setup, loaded KPM indication, RC acknowledgement, and platform Setup response |

The numerical values are compared only after checking endpoint definitions.
MATLAB's finite-drain cohort completion is not silently equated with a
5G-LENA receiver-window statistic, and neither is called over-the-air evidence.

## First 5G-LENA anchor

The official `cttc-nr-3gpp-calibration-user` program is used with 5G-LENA,
UMi, 20 MHz, 30 kHz SCS, UL direction, PF scheduling, SISO antennas, IR EESM
table 2, and fixed-beam/no-fading/no-shadowing for the first abstraction-aligned
anchor.  One site contains three sectors; `uesPerGnb=2` therefore creates six
UE flows.  Traffic scenario 3 stresses each cell.

Three patterns are deliberately separated:

- `ul4_spread`: four complete UL slots distributed through a 20-slot period;
- `ul11_spread`: eleven complete UL slots distributed through the same period;
- `ul4_contiguous`: four complete UL slots placed at the period tail, retained
  as a control-timing diagnostic rather than a performance baseline.

The diagnostic matters because a slot count alone does not freeze K2/DCI/SRS
opportunities.  A pattern that is legal in a high-level abstraction can be
unusable in a detailed NR implementation.

Run and aggregate:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_5glena.ps1
C:\Python313\python.exe .\parse_5glena_runs.py
```

Raw logs and parsed tables are written below `results/5glena/`.

## Additional anchors completed

- Sionna: MCS 13 uses the same 51-RB/30-kHz/TDL-C/14088-bit-TB anchor as
  MATLAB. Five seeds produce the waterfall at 8--10 dB; a second run covers
  MATLAB's exact 12--17 dB grid. The approximately 6 dB shift is reported as a
  receiver/noise-model disagreement, not hidden by merging curves. TensorFlow
  2.21 now detects the RTX 5090 D through the isolated Sionna environment. A
  1000-TB CPU/GPU check gives identical block-error counts and a 3.05x GPU
  speedup; PTX JIT makes small cold GPU runs slower.
- srsRAN+srsUE, UERANSIM, and OAI each complete Contract S against the same
  Open5GS core, including isolated UE networking and 4/4 bidirectional pings.
- The srsRAN/FlexRIC combined run adds sustained UL iperf3 traffic inside the
  KPM window, six nonzero PRB-load indications, and an acknowledged RC Style
  2/Action 6 request in the same SA session.
- The O-RAN SC M deployment has AppMgr, SubMgr, E2Mgr, RTMgr, and E2Term ready.
  After two recorded compatibility corrections, official ns-O-RAN closes five
  of five E2 Setup exchanges.
- NIST ns3-oran repository/keep-alive examples and the narrow NORI Setup path
  provide secondary model/protocol controls. NORI ns-3 now closes Setup against
  O-RAN SC M after an explicit E2Sim/PLMN compatibility profile. Scheduler-log
  and stop-time guards make its 10-s RL smoke bounded and cleanly exiting; a
  missing slice-to-RNTI map was also restored, after which the saturation smoke
  delivered 35,184/35,200 packets. It remains a repaired single-run health
  check outside the comparative performance evidence.

Run or check the anchors with `run_sionna_pusch.py`, `run_5glena.ps1`,
`run_ueransim_sa.sh`, `run_oai_open5gs_sa.sh`, and `run_flexric_rc_sa.sh`.
Rebuild the machine-checked protocol summary and compact evidence matrix with:

```powershell
C:\Python313\python.exe .\compare_backends.py
C:\Python313\python.exe .\summarize_protocol_contracts.py
C:\Python313\python.exe .\summarize_backends.py
```

The interpretation and TCOM paper-use decision are in
`BACKEND_COMPARISON.md`; machine-readable rows are in
`results/backend_evidence_matrix.csv` and `.json`.
The per-stack pass/fail and boundary ledger is `STACK_HEALTH_20260718.md`.
