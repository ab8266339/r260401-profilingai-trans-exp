"""Summarize the frozen branch sweeps (BRANCH_SWEEPS_CARD.md)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "results_branch_sweeps_20260803" / "branch_sweep_results.json"
GATE = ROOT / "results_gate_control_100_260" / "gate_control_results.json"
OUT = ROOT / "results_branch_sweeps_20260803"
SEEDS = [0, 1, 2, 3, 4]
T95_DF4 = 2.7764451051977987


def ci95(vals):
    c = mean(vals)
    h = T95_DF4 * stdev(vals) / math.sqrt(len(vals))
    return c, c - h, c + h


def sign(lo, hi, lower_is_better=True):
    if lower_is_better:
        return "improvement" if hi < 0 else ("degradation" if lo > 0 else "unresolved")
    return "improvement" if lo > 0 else ("degradation" if hi < 0 else "unresolved")


def main():
    rows = json.loads(RAW.read_text())
    gate = json.loads(GATE.read_text())
    gate_by = {(r["N"], r["seed"], r["mode"]): r for r in gate}
    a = {(r["N"], r["seed"]): r for r in rows if r["sweep"] == "A"}
    b = {(r["N"], r["seed"], r["mode"]): r for r in rows if r["sweep"] == "B"}
    c = [r for r in rows if r["sweep"] == "C"]

    summary = {}

    # ---- Sweep A: enforced vs advisory (stored) over 180..260
    ns = range(180, 261)
    pts = {}
    for n in ns:
        p = {}
        for m in ("mean_total_ms", "p95_ms", "sla_rate", "sla_rate_all"):
            d = [a[(n, s)][m] -
                 (gate_by[(n, s, "tdd_joint")][m] if m in gate_by[(n, s, "tdd_joint")]
                  else gate_by[(n, s, "tdd_joint")]["sla_rate"])
                 for s in SEEDS]
            cme, lo, hi = ci95(d)
            p[m] = {"delta": cme, "lo": lo, "hi": hi,
                    "sign": sign(lo, hi, m in ("mean_total_ms", "p95_ms"))}
        p["blocked_frac"] = mean(
            a[(n, s)]["blocked"] /
            max(a[(n, s)]["blocked"] + a[(n, s)]["count"], 1) for s in SEEDS)
        pts[n] = p
    summary["A_enforced_minus_advisory"] = {
        "median_delta_mean_ms": median(pts[n]["mean_total_ms"]["delta"] for n in ns),
        "median_delta_p95_ms": median(pts[n]["p95_ms"]["delta"] for n in ns),
        "median_delta_sla_all_pp": 100 * median(
            pts[n]["sla_rate_all"]["delta"] for n in ns),
        "mean_delta_sign_counts": {
            s: sum(1 for n in ns if pts[n]["mean_total_ms"]["sign"] == s)
            for s in ("improvement", "degradation", "unresolved")},
        "p95_delta_sign_counts": {
            s: sum(1 for n in ns if pts[n]["p95_ms"]["sign"] == s)
            for s in ("improvement", "degradation", "unresolved")},
        "sla_all_sign_counts": {
            s: sum(1 for n in ns
                   if pts[n]["sla_rate_all"]["sign"] == s)
            for s in ("improvement", "degradation", "unresolved")},
        "median_blocked_frac": median(pts[n]["blocked_frac"] for n in ns),
    }
    (OUT / "sweep_a_points.json").write_text(json.dumps(
        {str(n): pts[n] for n in ns}))

    # ---- Sweep B: dl_heavy contrasts vs baseline over 100..200
    ns_b = range(100, 201)
    bpts = {}
    for n in ns_b:
        p = {"n": n}
        for arm in ("tdd", "tdd_joint"):
            for m in ("mean_total_ms", "p95_ms"):
                d = [b[(n, s, arm)][m] - b[(n, s, "baseline")][m] for s in SEEDS]
                cme, lo, hi = ci95(d)
                p[f"{arm}_{m}"] = {"delta": cme, "lo": lo, "hi": hi,
                                   "sign": sign(lo, hi)}
        for arm in ("baseline", "tdd", "tdd_joint"):
            p[f"{arm}_dl_ms"] = mean(b[(n, s, arm)]["mean_dl_ms"] for s in SEEDS)
            p[f"{arm}_tdd"] = mean(b[(n, s, arm)]["final_tdd_ratio"] for s in SEEDS)
        bpts[n] = p
    summary["B_dl_heavy"] = {
        "tdd_mean_sign_counts": {
            s: sum(1 for n in ns_b if bpts[n]["tdd_mean_total_ms"]["sign"] == s)
            for s in ("improvement", "degradation", "unresolved")},
        "tdd_joint_mean_sign_counts": {
            s: sum(1 for n in ns_b
                   if bpts[n]["tdd_joint_mean_total_ms"]["sign"] == s)
            for s in ("improvement", "degradation", "unresolved")},
        "median_tdd_delta_mean_ms": median(
            bpts[n]["tdd_mean_total_ms"]["delta"] for n in ns_b),
        "median_tdd_joint_delta_mean_ms": median(
            bpts[n]["tdd_joint_mean_total_ms"]["delta"] for n in ns_b),
        "median_dl_ms": {arm: median(bpts[n][f"{arm}_dl_ms"] for n in ns_b)
                         for arm in ("baseline", "tdd", "tdd_joint")},
        "median_final_tdd": {arm: median(bpts[n][f"{arm}_tdd"] for n in ns_b)
                             for arm in ("baseline", "tdd", "tdd_joint")},
    }
    (OUT / "sweep_b_points.json").write_text(json.dumps(
        {str(n): bpts[n] for n in ns_b}))

    # ---- Sweep C: state store at N=140
    caps = sorted({r["state_store_mb"] for r in c},
                  key=lambda x: (x is None, x))
    cpts = {}
    for cap in caps:
        rows_c = [r for r in c if r["state_store_mb"] == cap]
        hit = mean(r["pipeline_cached"] / max(r["pipeline_cycles"], 1)
                   for r in rows_c)
        ul = mean(r["pipeline_ul_bytes"] for r in rows_c)
        cpts[str(cap)] = {"hit_rate": round(hit, 3),
                          "pipeline_ul_mb": round(ul / 1e6, 2),
                          "mean_total_ms": round(
                              mean(r["mean_total_ms"] for r in rows_c), 1)}
    summary["C_state_store_N140"] = cpts

    (OUT / "branch_sweep_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
