# R260401 Profiling AI — Experiment Artifact

Reproducibility package for the manuscript **Profiling AI for Massive Multi-Agent Sessions** (R260401).

This repository packs the experiment code, configuration, run records, and result summaries used in the paper: serving calibration and resident-prefix / KV measurements (vLLM), discrete-event gate and admission controls (SimPy), radio studies (MATLAB / ns-3), protocol and O-RAN interface runs, and measured radio-bound replay (Ollama).

It is **not** the LaTeX manuscript tree. Paper sources remain in the authors’ working-paper workspace.

## Layout

| Path | Content |
|---|---|
| `vllm_4gpu_20261001/` | Four-GPU vLLM 0.30.0 resident-prefix / replay campaign (scripts, summaries, provenance) |
| `vllm_4gpu_20261003/`, `vllm_brjd_20261003/` | Follow-on vLLM calibration / BRJD measurements |
| `discrete_event_sim/` | SimPy multi-resource discrete-event controller used for gate / admission sweeps |
| `tier_b_simpy/` | Discrete-event experiment workspace and outputs |
| `tier_c_oran/` | O-RAN closed-loop protocol runs and logs |
| `matlab_nr_validation/` | MATLAB NR system- and link-level checks |
| `ns3_ul_validation/`, `ns3-oran/` | ns-3 uplink / O-RAN validation |
| `ollama_measure/` | Measured radio-bound replay with a live model server |
| `cross_backend_validation/`, `testbed_hil/`, `afs_path_validation/` | Cross-backend and testbed studies |
| `analysis_anchors/` | JSON summaries cited by the manuscript controls |
| `flexric/`, `integration/`, `open5gs/` | Integration placeholders |

## Quick start (vLLM KV campaign)

See `vllm_4gpu_20261001/PLAN.md` and `vllm_4gpu_20261001/DATA_PROVENANCE.md`.

- Hardware used for the main table: 4×RTX 5090 server, vLLM 0.30.0.
- Summaries used in the paper: `vllm_4gpu_20261001/results_4gpu/summary/` (or the mirrored summary under the warehouse backup tree).
- Re-aggregation: `python summarize.py <results_root>` (requires numpy / scipy).

Raw per-cell JSON for the 2026-10-01 campaign is kept as `raw_cells.tar.gz` inside the vLLM package where present; models weights are **not** included (download Qwen checkpoints separately).

## Discrete-event controls

`discrete_event_sim/` contains the controller used for the all-integer population sweeps and branch controls. Dependencies: see `requirements.txt` in that folder. Large paired-seed JSON anchors are under `analysis_anchors/`.

## License / citation

Experiment scripts and generated numerical artifacts are released to support verification of the manuscript results. Please cite the paper when using this material. Third-party stacks (vLLM, srsRAN, FlexRIC, MATLAB, ns-3, …) remain under their own licenses.

## Provenance

Internal NAS git history: `r260401-profilingai-trans-exp` (initial import 2026-08-25). This GitHub export adds the 2026-10 vLLM campaigns and paper analysis anchors, and omits bulk workstation dump mirrors.
