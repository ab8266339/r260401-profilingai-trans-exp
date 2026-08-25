"""Run the frozen four-arm gate-control integer sweep (GATE_CONTROL_SWEEP_CARD.md)."""
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import oran_mmas_sim as sim

OUTDIR = "results_gate_control_100_260"
ARMS = ["baseline", "tdd", "tdd_joint", "full"]
NS = list(range(100, 261))
SEEDS = [0, 1, 2, 3, 4]


def one(job):
    n, seed, arm = job
    r = sim.run_simulation(n, seed, 60.0, 10.0, mode=arm)
    return r


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    jobs = [(n, s, a) for n in NS for a in ARMS for s in SEEDS]
    print(f"{len(jobs)} runs")
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=16) as ex:
        for i, r in enumerate(ex.map(one, jobs, chunksize=8)):
            results.append(r)
            if (i + 1) % 400 == 0:
                print(f"{i+1}/{len(jobs)} ({time.time()-t0:.0f}s)")
    with open(os.path.join(OUTDIR, "gate_control_results.json"), "w") as f:
        json.dump(results, f)
    print(f"done in {time.time()-t0:.0f}s -> {OUTDIR}/gate_control_results.json")


if __name__ == "__main__":
    main()
