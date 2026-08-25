"""Summarize the joint-gate and persistence card runs."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "results_joint_persistence_20260805" / "joint_persistence_results.json"
GATE = ROOT / "results_gate_control_100_260" / "gate_control_results.json"
BRANCH = ROOT / "results_branch_sweeps_20260803" / "branch_sweep_results.json"
OUT = RAW.parent
SEEDS = [0, 1, 2, 3, 4]
KEYS = ("count", "mean_total_ms", "p95_ms", "final_tdd_ratio")
T95 = 2.7764451051977987


def ci(vals):
    c = mean(vals)
    h = T95 * stdev(vals) / math.sqrt(len(vals))
    return c, c - h, c + h


def main():
    rows = json.loads(RAW.read_text())
    gate = {(r["N"], r["seed"], r["mode"]): r for r in json.loads(GATE.read_text())}
    dlb = {(r["N"], r["seed"], r["mode"]): r
           for r in json.loads(BRANCH.read_text()) if r["sweep"] == "B"}
    S = {}

    r1 = [r for r in rows if r["sweep"] == "R1"]
    ident = sum(1 for r in r1 if all(
        r[k] == gate[(r["N"], r["seed"], "tdd_joint")][k] for k in KEYS))
    S["R1_identity"] = {"identical": ident, "total": len(r1)}

    r2 = [r for r in rows if r["sweep"] == "R2"]
    ident2 = sum(1 for r in r2 if all(
        r[k] == dlb[(r["N"], r["seed"], "tdd_joint")][k] for k in KEYS))
    S["R2_identity"] = {"identical": ident2, "total": len(r2)}

    r3 = {(r["N"], r["seed"], r["mode"]): r
          for r in rows if r["sweep"] == "R3"}
    pts = list(range(70, 101))
    holds = [sum(r3[(n, s, "joint_full")]["margin_log"]["holds_state"]
                 for s in SEEDS) for n in pts]
    ratio_d = [mean(r3[(n, s, "joint_full")]["final_tdd_ratio"]
                    - r3[(n, s, "full")]["final_tdd_ratio"] for s in SEEDS)
               for n in pts]
    raises = {arm: median(mean(r3[(n, s, arm)]["margin_log"]["raises"]
                               for s in SEEDS) for n in pts)
              for arm in ("joint_full", "full")}
    S["R3_state_branch"] = {
        "points_with_state_holds": sum(1 for h in holds if h > 0),
        "points_total": len(pts),
        "median_state_holds_per_point": median(holds),
        "median_final_ratio_delta": median(ratio_d),
        "points_ratio_delta_positive": sum(1 for d in ratio_d if d > 1e-9),
        "median_raises_per_run": raises,
    }

    p = {(r["N"], r["seed"], r["mode"]): r for r in rows if r["sweep"] == "P"}
    regions = {"radio_dominant_60_100": range(60, 101),
               "mixed_101_130": range(101, 131)}
    for rname, rng in regions.items():
        out = {}
        for m, better_lower in (("pipeline_mean_ms", True),
                                ("pipeline_p95_ms", True),
                                ("pipeline_sla", False),
                                ("mean_total_ms", True)):
            sgn = {"improvement": 0, "degradation": 0, "unresolved": 0}
            med = []
            for n in rng:
                d = [p[(n, s, "persist_only")][m] - p[(n, s, "baseline")][m]
                     for s in SEEDS]
                c, lo, hi = ci(d)
                med.append(c)
                if better_lower:
                    sgn["improvement" if hi < 0 else
                        "degradation" if lo > 0 else "unresolved"] += 1
                else:
                    sgn["improvement" if lo > 0 else
                        "degradation" if hi < 0 else "unresolved"] += 1
            out[m] = {"median_delta": median(med), "signs": sgn}
        out["median_baseline_ul_ms"] = median(
            mean(p[(n, s, "baseline")]["mean_ul_ms"] for s in SEEDS) for n in rng)
        out["median_baseline_gpuq_ms"] = median(
            mean(p[(n, s, "baseline")]["mean_gpu_queue_ms"] for s in SEEDS)
            for n in rng)
        S[f"P_{rname}"] = out

    (OUT / "joint_persistence_summary.json").write_text(json.dumps(S, indent=1))
    print(json.dumps(S, indent=1))


if __name__ == "__main__":
    main()
