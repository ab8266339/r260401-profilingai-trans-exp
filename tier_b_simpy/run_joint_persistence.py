"""Run the frozen joint-gate and persistence card (JOINT_GATE_PERSISTENCE_CARD.md)."""
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import oran_mmas_sim as sim

OUTDIR = "results_joint_persistence_20260805"
SEEDS = [0, 1, 2, 3, 4]


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
    for n in range(100, 261):
        for s in SEEDS:
            jobs.append(("R1", n, s, "joint", "default", None))
    for n in range(100, 201):
        for s in SEEDS:
            jobs.append(("R2", n, s, "joint", "dl_heavy", None))
    for n in range(70, 101):
        for arm in ("joint_full", "full"):
            for s in SEEDS:
                jobs.append(("R3", n, s, arm, "default", 500))
    for n in range(60, 131):
        for arm in ("baseline", "persist_only"):
            for s in SEEDS:
                jobs.append(("P", n, s, arm, "default", None))
    print(len(jobs), "runs")
    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=16) as ex:
        for i, r in enumerate(ex.map(one, jobs, chunksize=8)):
            res.append(r)
            if (i + 1) % 500 == 0:
                print(f"{i+1}/{len(jobs)} ({time.time()-t0:.0f}s)")
    with open(os.path.join(OUTDIR, "joint_persistence_results.json"), "w") as f:
        json.dump(res, f)
    print(f"done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
