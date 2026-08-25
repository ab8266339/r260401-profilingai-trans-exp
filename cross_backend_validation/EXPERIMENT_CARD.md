# Experiment Card — cross-backend radio and O-RAN validation

Experiment ID: EXP-CROSS-BACKEND-01  
Primary claim: Increasing usable UL opportunity can relieve a radio-bound mMAS
path, but benefit and feasibility depend on complete NR slot/control timing and
on whether the next bottleneck has headroom.  
Question or hypothesis: Do independent backends agree on the direction of the
4-to-11 UL-slot intervention, and which backend-specific mechanisms explain
different effect sizes?  
System object: Single-site NR uplink plus protocol-in-loop feasibility controls.  
Workload or scenario: Backend-appropriate fixed workload; endpoints are mapped,
not assumed identical.  
Baseline: Four complete UL slots per 20-slot pattern.  
Changed variable: Full TDD pattern and usable UL opportunity.  
Fixed variables: Within each backend, seed, traffic, topology, radio parameters,
scheduler and run window are paired.  
Metrics: Delivery/completion fraction, mean and tail delay, throughput, failed
or zero-delivery flows, protocol setup/subscription/control outcome and timing.  
Repetitions: Five paired 5G-LENA RNG runs for the first anchor; five paired
MATLAB runs per formal population; five Sionna seeds with 40 TB per seed and
SNR; one complete srsRAN, UERANSIM, and OAI SA run per backend; one combined
srsRAN/FlexRIC loaded KPM+RC run; and five simultaneous official ns-O-RAN Setup
nodes against the O-RAN SC M platform. One Sionna CPU/GPU reproducibility pair
uses 1000 TB per device; one compatibility-profiled NORI ns-3 Setup exchange is
retained as a secondary control.  
Statistical method: Seed-level paired differences; descriptive protocol timing
until repeated trials exist.  
Failure or rejection condition: Mixed endpoints presented as the same metric;
unrecorded pattern changes; incomplete runs; or a backend that cannot exercise
the intended mechanism.  
Tool and version: MATLAB R2026a; ns-3-dev with 5G-LENA nr v5.0; Sionna 1.2.2;
srsRAN 25.10.0; UERANSIM 3.3.0; OAI develop 70508eb; FlexRIC built with
gcc-12; Open5GS 2.8.0; O-RAN SC M integration commit 0ce5f68; NORI on ns-3.42.  
Raw result location: `simulation/cross_backend_validation/results/`.  
Seed policy: Paired within backend; no claim that seed numbers reproduce the
same physical channel across different simulators.  
Eligibility for main paper: Only after endpoint, configuration, and repeated-run
checks pass.  Backend disagreement is a result to explain, not a value to hide.

## Model boundary

| Backend | Included | Omitted or different |
| --- | --- | --- |
| MATLAB | Static UMi LOS/NLOS and shadowing, abstract PHY, PF, 51 RB, 16 HARQ, burst traffic | Fast fading, mobility, multicell interference, explicit K2/control feasibility |
| 5G-LENA anchor | NR EESM, explicit TDD/K2/control timing, PF, three sectors, UMi geometry | First anchor disables fading and shadowing; traffic is calibration CBR, not the exact mMAS burst process |
| Sionna | Waveform/channel receiver behavior | Scheduler, queues, TDD controller and device scaling |
| srsRAN/OAI | Real protocol implementations over virtual RF and real E2AP/SCTP when connected | OTA RF propagation and large simulated UE populations |
