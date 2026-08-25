"""Audit-fix reruns (2026-08-05, after the cross-model evidence audit).

Fix A: rerun Sweep A with BOTH arms under the generated-request counter, so
attainment uses the full post-warmup generation denominator (blocked and
unfinished requests count as misses). Dynamics are unchanged; determinism is
verified against the stored gate-control rows before use.

Fix B: fresh-seed confirmation of the dl_heavy sweep. The downlink step-down
rule was frozen after the disclosed pilot; seeds 5 through 9 never touched
any prior run of this scenario.
"""
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from statistics import mean, median, stdev

import oran_mmas_sim as sim

OUTDIR = "results_branch_sweeps_20260803"
T95 = 2.7764451051977987


def one(job):
    kind, n, seed, arm, scenario = job
    r = sim.run_simulation(n, seed, 60.0, 10.0, mode=arm, scenario=scenario)
    r["sweep"] = kind
    return r


def ci(vals):
    c = mean(vals)
    h = T95 * stdev(vals) / math.sqrt(len(vals))
    return c, c - h, c + h


def main():
    jobs = []
    for n in range(180, 261):
        for arm in ("tdd_joint", "tdd_joint_enforced"):
            for s in range(5):
                jobs.append(("A2", n, s, arm, "default"))
    for n in range(100, 201):
        for arm in ("baseline", "tdd", "tdd_joint"):
            for s in range(5, 10):
                jobs.append(("B2", n, s, arm, "dl_heavy"))
    print(len(jobs), "runs")
    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=16) as ex:
        for r in ex.map(one, jobs, chunksize=8):
            res.append(r)
    print(f"runs done {time.time()-t0:.0f}s")
    with open(os.path.join(OUTDIR, "audit_fix_results.json"), "w") as f:
        json.dump(res, f)

    a = {(r["N"], r["seed"], r["mode"]): r for r in res if r["sweep"] == "A2"}
    b = {(r["N"], r["seed"], r["mode"]): r for r in res if r["sweep"] == "B2"}

    # Sweep A2: enforced minus advisory on the generated denominator
    summ = {"A2": {}, "B2": {}}
    sgn = {"improvement": 0, "degradation": 0, "unresolved": 0}
    deltas, served = [], []
    blocked_frac, unfinished_frac = [], []
    for n in range(180, 261):
        d = [a[(n, s, "tdd_joint_enforced")]["sla_rate_gen"]
             - a[(n, s, "tdd_joint")]["sla_rate_gen"] for s in range(5)]
        c, lo, hi = ci(d)
        deltas.append(c)
        sgn["improvement" if lo > 0 else
            "degradation" if hi < 0 else "unresolved"] += 1
        dm = [a[(n, s, "tdd_joint_enforced")]["mean_total_ms"]
              - a[(n, s, "tdd_joint")]["mean_total_ms"] for s in range(5)]
        served.append(mean(dm))
        e = [a[(n, s, "tdd_joint_enforced")] for s in range(5)]
        blocked_frac.append(mean(r["blocked"] / max(r["generated"], 1) for r in e))
        unfinished_frac.append(mean(
            (r["generated"] - r["count"] - r["blocked"]) / max(r["generated"], 1)
            for r in e))
    summ["A2"] = {
        "median_delta_sla_gen_pp": 100 * median(deltas),
        "sla_gen_sign_counts": sgn,
        "median_served_mean_delta_ms": median(served),
        "median_blocked_frac": median(blocked_frac),
        "median_unfinished_frac_enforced": median(unfinished_frac),
    }

    # Sweep B2: fresh-seed contrasts vs baseline
    for arm in ("tdd", "tdd_joint"):
        s2 = {"improvement": 0, "degradation": 0, "unresolved": 0}
        med = []
        for n in range(100, 201):
            d = [b[(n, s, arm)]["mean_total_ms"]
                 - b[(n, s, "baseline")]["mean_total_ms"] for s in range(5, 10)]
            c, lo, hi = ci(d)
            med.append(c)
            s2["improvement" if hi < 0 else
               "degradation" if lo > 0 else "unresolved"] += 1
        summ["B2"][arm] = {"median_delta_mean_ms": median(med),
                           "sign_counts": s2}
    summ["B2"]["median_dl_ms"] = {
        arm: median(mean(b[(n, s, arm)]["mean_dl_ms"] for s in range(5, 10))
                    for n in range(100, 201))
        for arm in ("baseline", "tdd", "tdd_joint")}

    with open(os.path.join(OUTDIR, "audit_fix_summary.json"), "w") as f:
        json.dump(summ, f, indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
