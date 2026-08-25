"""Seed extension E1 for Run P (JOINT_GATE_PERSISTENCE_CARD.md)."""
import json
import time
from concurrent.futures import ProcessPoolExecutor

import oran_mmas_sim as sim


def one(job):
    n, seed, arm = job
    r = sim.run_simulation(n, seed, 60.0, 10.0, mode=arm)
    return {"N": n, "seed": seed, "mode": arm,
            "pipeline_mean_ms": r.get("pipeline_mean_ms", 0),
            "pipeline_p95_ms": r.get("pipeline_p95_ms", 0),
            "pipeline_sla": r.get("pipeline_sla", 0),
            "mean_total_ms": r["mean_total_ms"]}


if __name__ == "__main__":
    jobs = [(n, s, a) for n in range(60, 131)
            for a in ("baseline", "persist_only") for s in range(5, 25)]
    print(len(jobs), "runs")
    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=16) as ex:
        for r in ex.map(one, jobs, chunksize=16):
            res.append(r)
    json.dump(res, open(
        "results_joint_persistence_20260805/persistence_ext_seeds5_24.json",
        "w"))
    print("done", round(time.time() - t0), "s")
