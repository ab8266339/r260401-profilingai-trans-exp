"""Run the frozen branch sweeps (BRANCH_SWEEPS_CARD.md)."""
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import oran_mmas_sim as sim

OUTDIR = "results_branch_sweeps_20260803"
SEEDS = [0, 1, 2, 3, 4]
STATE_CAPS = [None, 64000, 32000, 16000, 8000, 4000, 2000, 1000, 500, 250]


def one(job):
    kind, n, seed, arm, scenario, cap = job
    r = sim.run_simulation(n, seed, 60.0, 10.0, mode=arm,
                           scenario=scenario, state_store_mb=cap)
    r["sweep"] = kind
    r["state_store_mb"] = cap
    return r


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    jobs = []
    # Sweep A: enforced admission, new arm only (tdd_joint and baseline reuse
    # the bit-exact-verified 2026-08-01 stored results)
    for n in range(180, 261):
        for s in SEEDS:
            jobs.append(("A", n, s, "tdd_joint_enforced", "default", None))
    # Sweep B: dl_heavy, three arms
    for n in range(100, 201):
        for arm in ("baseline", "tdd", "tdd_joint"):
            for s in SEEDS:
                jobs.append(("B", n, s, arm, "dl_heavy", None))
    # Sweep C: state store at N=140, full arm
    for cap in STATE_CAPS:
        for s in SEEDS:
            jobs.append(("C", 140, s, "full", "default", cap))

    print(f"{len(jobs)} runs")
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=16) as ex:
        for i, r in enumerate(ex.map(one, jobs, chunksize=8)):
            results.append(r)
            if (i + 1) % 400 == 0:
                print(f"{i+1}/{len(jobs)} ({time.time()-t0:.0f}s)")
    with open(os.path.join(OUTDIR, "branch_sweep_results.json"), "w") as f:
        json.dump(results, f)
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
