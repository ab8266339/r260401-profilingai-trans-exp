# MATLAB R2026a PUSCH link-level cross-check

This directory is an independent, small-scale cross-check of the radio link.
It does not modify or replace the existing abstract-PHY network simulation.

## Scope

- MATLAB R2026a Update 3 and 5G Toolbox low-level APIs.
- 20 MHz nominal channel, 30 kHz SCS, 51 RB, normal CP, one layer, SISO.
- Full-slot PUSCH allocation with mapping type A and two DM-RS symbols.
- UL-SCH coding, PUSCH modulation, practical DM-RS timing/channel estimation,
  MMSE equalization, LDPC decoding, and HARQ RV sequence `[0 2 3 1]`.
- 3GPP TDL-C with 300 ns RMS delay spread and 30 Hz maximum Doppler.
- MCS table 1 representatives: MCS 4 (QPSK, R=308/1024), MCS 13
  (16QAM, R=490/1024), and MCS 18 (64QAM, R=466/1024).
- SNR is defined per resource element and receive antenna, following the
  MathWorks NR PUSCH throughput example.

The receiver is practical rather than oracle-aided: both timing and channel
estimates are derived from the received PUSCH DM-RS. An invalid timing result
beyond the channel's reported maximum delay falls back to zero and is counted
in the output.

## Run

The wrapper resolves the registered R2026a installation instead of the older
MATLAB version on `PATH`:

```powershell
cd E:\ml-work\R260401-profilingai-trans\matlab_nr_validation\pusch_link_validation
powershell -ExecutionPolicy Bypass -File .\run_r2026a_pusch.ps1 -Profile smoke
powershell -ExecutionPolicy Bypass -File .\run_r2026a_pusch.ps1 -Profile formal
```

The formal sweep uses a fully fixed, predeclared sample design. Regular curve
points use three seeds and 60 new transport blocks per seed (N=180). Three
precision anchors near 10% BLER - MCS 4 at 6 dB, MCS 13 at 15 dB, and MCS 18
at 20 dB - use five seeds and 200 new transport blocks per seed (N=1000).
There is no error-count or confidence-interval stopping rule anywhere in the
execution path. Fixed denominators avoid the optional-stopping problem that
adaptive stopping would introduce into ordinary Wilson intervals. Exact
counts and Wilson 95% intervals are serialized; the curve is still not
presented as a conformance result.

Before either profile, an AWGN/no-impairment sanity check runs three transport
blocks for each MCS at 30 dB and must produce zero initial and residual errors.

## Verified formal run (2026-07-14)

R2026a Update 3 completed all 18 MCS/SNR points: 5700 new transport
blocks and 6731 total HARQ attempts. The high-SNR AWGN sanity check decoded
all 9 transport blocks on their first attempt. Representative TDL-C results
are:

| MCS | SNR | Initial errors / TB | Initial BLER (Wilson 95% CI) | Full-UL rate |
|---:|---:|---:|---:|---:|
| 4, QPSK | 6 dB | 101 / 1000 | 10.10% (8.38-12.12%) | 7.91 Mbit/s |
| 13, 16QAM | 15 dB | 135 / 1000 | 13.50% (11.52-15.76%) | 24.82 Mbit/s |
| 18, 64QAM | 20 dB | 141 / 1000 | 14.10% (12.08-16.39%) | 35.00 Mbit/s |

No transport block remained in error after the four-RV HARQ sequence in this
finite run. That observation is not a zero-error reliability guarantee: at a
precision anchor with 0/1000 residual errors, the two-sided Wilson 95% upper
bound is about 0.383%. At a regular point with 0/180, it is about 2.09%.

## Outputs

Each profile writes under `results/<profile>/`:

- `summary.csv`: aggregate MCS/SNR rows with the fixed design role, sample
  count, and Wilson 95% BLER intervals.
- `per_seed.csv`: seed-level counts and diagnostics.
- `awgn_sanity.csv`: high-SNR pipeline sanity results.
- `summary.json`: configuration, provenance, counts, and both tables.
- `results.mat`: MATLAB-native tables and configuration.

The paper-grade plot is exported to:

- `output/pdf/fig_pusch_link_calibration.pdf`
- `output/png/fig_pusch_link_calibration.png`

Smoke outputs use the `_smoke` suffix so they cannot overwrite the formal
figure.

In the formal figure, filled markers identify the N=1000 precision anchors;
open markers identify N=180 regular curve points. Vertical bars are two-sided
Wilson 95% intervals for initial-transmission BLER.

`supported_full_ul_rate_mbps` is successful transport-block payload divided
by the number of occupied 0.5 ms NR slots, including HARQ retransmissions. It
therefore assumes every slot and all 14 symbols are available for this one
PUSCH. `nominal_spectral_efficiency_bps_hz` divides that rate by 20 MHz;
`occupied_spectral_efficiency_bps_hz` divides it by the 18.36 MHz occupied RB
span. The 4/20 and 11/20 columns are simple ideal duty-cycle scalings, not a
second scheduler simulation.

## Claim boundary

These results are a **link-level cross-check**. They exercise a standards-
based PUSCH waveform, TDL fading, practical estimation, UL-SCH, and HARQ, but
they do not model multi-UE scheduling, power control, inter-cell interference,
mobility trajectories, RF impairments, RLC queues, TDD guard/special symbols,
RIC/E2 signaling, or Open5GS. They are not over-the-air measurements and are
not yet fed back into the abstract-PHY network simulation. The 4/20 and 11/20
rate columns must not be described as measured end-to-end throughput.

Primary implementation references are the MathWorks
[NR PUSCH Throughput](https://www.mathworks.com/help/5g/ug/nr-pusch-throughput.html),
[`nrChannelEstimate`](https://www.mathworks.com/help/5g/ref/nrchannelestimate.html),
and [`nrTDLChannel`](https://www.mathworks.com/help/5g/ref/nrtdlchannel-system-object.html)
documentation.
