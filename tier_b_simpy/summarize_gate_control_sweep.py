"""Summarize the frozen four-arm gate-control sweep (GATE_CONTROL_SWEEP_CARD.md)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "results_gate_control_100_260" / "gate_control_results.json"
OUT = ROOT / "results_gate_control_100_260"
NS = list(range(100, 261))
SEEDS = [0, 1, 2, 3, 4]
ARMS = ["baseline", "tdd", "tdd_joint", "full"]
CONTRASTS = [("tdd", "baseline"), ("tdd_joint", "baseline"),
             ("full", "tdd_joint"), ("full", "baseline")]
METRICS = ["mean_total_ms", "p95_ms", "sla_rate", "mean_ul_ms",
           "mean_gpu_queue_ms", "mean_dl_ms", "count", "final_tdd_ratio"]
T95_DF4 = 2.7764451051977987
REGIONS = {"transition_100_179": range(100, 180),
           "saturated_180_260": range(180, 261)}


def ci95(vals):
    c = mean(vals)
    h = T95_DF4 * stdev(vals) / math.sqrt(len(vals))
    return c, c - h, c + h


def main():
    rows = json.loads(RAW.read_text())
    by = {(r["N"], r["seed"], r["mode"]): r for r in rows}
    assert len(by) == len(NS) * len(SEEDS) * len(ARMS), "grid incomplete"

    points = {}
    for n in NS:
        p = {"N": n}
        for arm in ARMS:
            for m in METRICS:
                p[f"{arm}_{m}"] = mean(by[(n, s, arm)][m] for s in SEEDS)
        for a, b in CONTRASTS:
            for m in ("mean_total_ms", "p95_ms"):
                d = [by[(n, s, a)][m] - by[(n, s, b)][m] for s in SEEDS]
                c, lo, hi = ci95(d)
                p[f"{a}-{b}_{m}"] = c
                p[f"{a}-{b}_{m}_lo"] = lo
                p[f"{a}-{b}_{m}_hi"] = hi
                p[f"{a}-{b}_{m}_sign"] = ("improvement" if hi < 0 else
                                          "degradation" if lo > 0 else "unresolved")
        # bit-identical hold check: gated arm equals baseline trace
        p["gate_held_all_seeds"] = all(
            by[(n, s, "tdd_joint")]["mean_total_ms"] == by[(n, s, "baseline")]["mean_total_ms"]
            and by[(n, s, "tdd_joint")]["count"] == by[(n, s, "baseline")]["count"]
            for s in SEEDS)
        points[n] = p

    grid = {}
    for rname, rng in REGIONS.items():
        g = {}
        for a, b in CONTRASTS:
            for m in ("mean_total_ms", "p95_ms"):
                key = f"{a}-{b}_{m}"
                g[f"median_{key}"] = median(points[n][key] for n in rng)
                for s in ("improvement", "degradation", "unresolved"):
                    g[f"{key}_{s}"] = sum(1 for n in rng
                                          if points[n][f"{key}_sign"] == s)
        for arm in ARMS:
            g[f"median_{arm}_ul_ms"] = median(points[n][f"{arm}_mean_ul_ms"] for n in rng)
            g[f"median_{arm}_gpuq_ms"] = median(points[n][f"{arm}_mean_gpu_queue_ms"] for n in rng)
            g[f"median_{arm}_tdd"] = median(points[n][f"{arm}_final_tdd_ratio"] for n in rng)
            g[f"median_{arm}_count"] = median(points[n][f"{arm}_count"] for n in rng)
        g["gate_held_points"] = sum(1 for n in rng if points[n]["gate_held_all_seeds"])
        grid[rname] = g

    OUT.joinpath("gate_control_summary.json").write_text(
        json.dumps({"design": {"Ns": [100, 260], "step": 1, "seeds": SEEDS,
                               "arms": ARMS}, "grid": grid}, indent=1))
    OUT.joinpath("gate_control_points.json").write_text(json.dumps(points))
    print(json.dumps(grid, indent=1))


if __name__ == "__main__":
    main()
