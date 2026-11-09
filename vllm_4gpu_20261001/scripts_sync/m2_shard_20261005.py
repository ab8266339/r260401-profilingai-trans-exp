#!/usr/bin/env python3
"""R260401 RD-M2 on 4卡机 across several GPUs (2026-10-05).

User 10-05: drop the strict single-machine exclusivity of the BRJD313 run, finish M2 on 4卡机 as fast as possible,
expedite it in the NAS scheduler, and "no single-card exclusive run, it must use several GPUs".

The frozen runner scripts/m2_c034f74b.py (SHA-256 c034f74b...) runs unchanged, except for runtime edits of its
experiment() that are recorded in results/m2_pipeline/sharding/<stage>.json:
  shard mode (pilot K, main K)
    1. skip repetitions not assigned to this shard; the enumeration index is unchanged, so every repetition keeps
       the arm order of the serial run (rotation by its index in the full repetition list);
    2. return right after the cell loop; the summary is computed once, by the merge stage, over all shards' cells.
  merge mode (freeze, final)
    3. seed the startup-time list from the per-repetition server_<arm>_r<rep>.json files written by the shards
       (the merge starts no server: every cell file already exists, so the cell loop skips every repetition).
All shards of a phase share one output directory and one run manifest (identical: it lists all repetitions and all
arrival traces), so the merge runs the runner's own summary code over the union. Cell files are per point/arm/rep.
Shards start 40 s apart because write_json uses a fixed temporary name per target file.

Stages (each one NAS-daemon job on 4卡机; GPU stages get the GPU the daemon claimed in CUDA_VISIBLE_DEVICES):
  cal        calibration + pre-pilot sampling plan (m2.pipeline, stopped before the pilot)          1 GPU, ~0.5 h
  pilot K    pilot repetition K (K = 0, 1, 2)                                                       1 GPU each
  freeze     merge pilot -> pilot_summary.json; m2.pipeline writes the formal freeze, stops before main     CPU
  main K     formal repetitions rep_seeds[K::4] (K = 0..3)                                          1 GPU each
  final      merge main -> main_summary.json (frozen family, Bonferroni intervals); pipeline_state COMPLETE CPU
  check      CPU-only self-check of the edits and the shard assignment (no server, no output change)
"""
import argparse
import asyncio
import datetime
import hashlib
import importlib.util
import inspect
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

ROOT = Path("/home/host/exp/r260401_20261003")
M2_PATH = ROOT / "scripts" / "m2_c034f74b.py"
M2_SHA = "c034f74b9c9eca4de8defb45c3ad1a99820861afb125cce923d61d52642dfce8"
OUT = ROOT / "results" / "m2_pipeline"
SHARD_DIR = OUT / "sharding"
MODEL = "/home/host/models/Qwen/Qwen3-8B"
N_PILOT, N_MAIN = 3, 4
PORTS = {"cal": 18231, "pilot": 18240, "main": 18250, "freeze": 18260, "final": 18261}
STAGGER_S = 40

EDITS = [
    ("    for i, rep in enumerate(reps):\n",
     "    for i, rep in enumerate(reps):\n"
     "        if _SHARD_REPS is not None and rep not in _SHARD_REPS:\n"
     "            continue\n"),
    ("\n    for rep in reps:\n        for arm in ARMS:\n",
     "\n    if _SHARD_REPS is not None:\n        return\n    for rep in reps:\n        for arm in ARMS:\n"),
    ("    rows, startup = [], []\n",
     "    rows, startup = [], list(_PRESET_STARTUP)\n"),
]


class StopHere(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def h(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode("utf-8")).hexdigest()


def load_m2():
    if h(M2_PATH.read_bytes()) != M2_SHA:
        raise RuntimeError("Frozen runner m2_c034f74b.py changed; refusing to run.")
    spec = importlib.util.spec_from_file_location("m2_c034f74b", M2_PATH)
    m2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m2)
    return m2


def edited_experiment(m2, shard_reps, preset_startup):
    src = inspect.getsource(m2.experiment)
    info = {"experiment_source_sha256": h(src), "edits": [{"from": a, "to": b} for a, b in EDITS]}
    for a, b in EDITS:
        if src.count(a) != 1:
            raise RuntimeError("Edit target is not unique in experiment(): %r" % a)
        src = src.replace(a, b)
    info["edited_source_sha256"] = h(src)
    ns = dict(m2.__dict__)
    ns["_SHARD_REPS"] = None if shard_reps is None else set(shard_reps)
    ns["_PRESET_STARTUP"] = list(preset_startup)
    exec(compile(src, str(M2_PATH) + " (experiment, sharded edit)", "exec"), ns)
    return ns["experiment"], info


def args_for(phase, gpus, port, **kw):
    # Same values as run_m2_v3.sh's command line (pipeline over results/m2_pipeline).
    a = argparse.Namespace(phase=phase, legacy=str(ROOT / "scripts" / "kvbench.py"), model_path=MODEL, gpus=gpus, port=port,
                           out=str(OUT), calibration=None, prior_baseline=str(ROOT / "rd_r0_baseline.json"),
                           calibration_reps=3, pilot_reps=N_PILOT, uncap=2048, ready_timeout=1800, freeze=None, freeze_sha256=None)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def record(stage, **kw):
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    p = SHARD_DIR / ("%s.json" % stage)
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"runs": []}
    old["runs"].append(dict(kw, at=now(), host=socket.gethostname(), cuda_visible_devices=os.environ.get("CUDA_VISIBLE_DEVICES"),
                            m2_sha256=M2_SHA, wrapper_sha256=h(Path(__file__).read_bytes())))
    tmp = p.with_suffix(".json.tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def mark_state(m2, status, **kw):
    sp = OUT / "pipeline_state.json"
    st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    rec = {k: st.pop(k) for k in ("error", "stopped_utc") if k in st}
    if rec:
        st.setdefault("sharded_stops", []).append(dict(rec, status_written_by_pipeline=st.get("status")))
    st.update(status=status, sharded_run="R260401 M2 on 4卡机 across several GPUs, see results/m2_pipeline/sharding/", **kw)
    m2.write_json(sp, st)


def run_pipeline_until(m2, legacy, gpus, stop_phases):
    orig = m2.experiment

    async def guarded(a, lg, o):
        if a.phase in stop_phases:
            raise StopHere(a.phase)
        return await orig(a, lg, o)
    m2.experiment = guarded
    try:
        asyncio.run(m2.pipeline(args_for("pipeline", gpus, PORTS["cal"]), legacy, OUT))
    except StopHere as e:
        return str(e)
    finally:
        m2.experiment = orig
    return None


def cal_file():
    return OUT / "calibration" / "calibration.json"


def pilot_args(m2, gpus, port):
    cal = json.loads(cal_file().read_text(encoding="utf-8"))
    plan = json.loads((OUT / "sampling_plan_before_pilot.json").read_text(encoding="utf-8"))
    points = m2.pilot_points(cal)
    if plan["fixed_rules"]["points"] != points:
        raise RuntimeError("Pre-pilot plan points differ from pilot_points(calibration).")
    return args_for("pilot", gpus, port, calibration=str(cal_file()), predetermined_points=points), points, list(range(N_PILOT))


def main_args(m2, gpus, port):
    fz = OUT / "formal_freeze.json"
    frozen = json.loads(fz.read_text(encoding="utf-8"))
    return args_for("main", gpus, port, calibration=str(cal_file()), freeze=str(fz), freeze_sha256=m2.sha(fz)), frozen["points"], frozen["rep_seeds"]


def arm_order(m2, index):
    A = m2.ARMS
    return A[index % len(A):] + A[:index % len(A)]


def cells_and_startup(m2, out, points, reps):
    missing, startup = [], []
    for i, rep in enumerate(reps):
        for arm in arm_order(m2, i):
            for p in points:
                if not (out / "cells" / ("p%d_%s_r%d.json" % (p["index"], arm, rep))).exists():
                    missing.append("p%d_%s_r%d" % (p["index"], arm, rep))
            sj = out / ("server_%s_r%d.json" % (arm, rep))
            if sj.exists():
                startup.append(json.loads(sj.read_text(encoding="utf-8"))["ready_seconds"])
            else:
                missing.append("server_%s_r%d.json" % (arm, rep))
    return missing, startup


def merge(m2, legacy, args, out, points, reps, stage):
    missing, startup = cells_and_startup(m2, out, points, reps)
    if missing:
        raise RuntimeError("Shards incomplete, %d files missing (first: %s); the merge starts no server." % (len(missing), missing[:5]))
    exp, info = edited_experiment(m2, None, startup)
    record(stage, mode="merge", out=str(out), reps=reps, startup_seconds=startup, edit=info)
    asyncio.run(exp(args, legacy, out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("cal", "pilot", "freeze", "main", "final", "check"))
    ap.add_argument("k", nargs="?", type=int)
    ap.add_argument("--gpus", default="")
    a = ap.parse_args()

    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    m2 = load_m2()
    if a.stage == "check":
        for mode, reps in (("shard", {10000}), ("merge", None)):
            _, info = edited_experiment(m2, reps, [1.0])
            print(mode, "edit ok", info["experiment_source_sha256"][:16], "->", info["edited_source_sha256"][:16])
        full = list(range(10000, 10020))
        for k in range(N_MAIN):
            mine = full[k::N_MAIN]
            print("main shard", k, "reps", mine, "orders", [arm_order(m2, full.index(r)) for r in mine][:3], "...")
        print("pilot shards", [[k] for k in range(N_PILOT)], "orders", [arm_order(m2, k) for k in range(N_PILOT)])
        for p in (M2_PATH, ROOT / "scripts" / "kvbench.py", ROOT / "rd_r0_baseline.json", Path(MODEL) / "config.json",
                  ROOT / "results/preflight/qwen3_8b/auto_capacity/DONE"):
            print("exists" if p.exists() else "MISSING", p)
        print("output root", OUT, "exists" if OUT.exists() else "(new)")
        return
    if not (Path(MODEL) / "config.json").is_file():
        raise RuntimeError("Local model not ready; runner never downloads models.")
    legacy = m2.load_legacy(str(ROOT / "scripts" / "kvbench.py"))
    OUT.mkdir(parents=True, exist_ok=True)
    gpus = a.gpus

    if a.stage == "cal":
        record("cal", mode="pipeline until pilot", gpus=gpus)
        stopped = run_pipeline_until(m2, legacy, gpus, ("pilot", "main"))
        if stopped is None:
            raise RuntimeError("Pipeline did not stop before the pilot.")
        mark_state(m2, "CALIBRATED_PILOT_TO_SHARD" if stopped == "pilot" else "FROZEN_MAIN_TO_SHARD")
        record("cal", mode="done", stopped_before=stopped)
    elif a.stage == "pilot":
        k = a.k
        if k is None or not 0 <= k < N_PILOT:
            raise SystemExit("pilot needs K in 0..%d" % (N_PILOT - 1))
        args, points, reps = pilot_args(m2, gpus, PORTS["pilot"] + k)
        if (OUT / "pilot" / "pilot_summary.json").exists():
            print("pilot already merged; nothing to do")
            return
        exp, info = edited_experiment(m2, [reps[k]], [])
        record("pilot%d" % k, mode="shard", reps=[reps[k]], arm_order=list(arm_order(m2, k)), gpus=gpus, port=args.port, edit=info)
        time.sleep(STAGGER_S * k)
        asyncio.run(exp(args, legacy, OUT / "pilot"))
        record("pilot%d" % k, mode="done")
    elif a.stage == "freeze":
        args, points, reps = pilot_args(m2, "", PORTS["freeze"])
        if not (OUT / "pilot" / "pilot_summary.json").exists():
            merge(m2, legacy, args, OUT / "pilot", points, reps, "freeze")
        stopped = run_pipeline_until(m2, legacy, "", ("pilot", "main"))
        if stopped != "main":
            raise RuntimeError("Pipeline did not reach the formal freeze (stopped at %r)." % stopped)
        fr = json.loads((OUT / "formal_freeze.json").read_text(encoding="utf-8"))
        mark_state(m2, "FROZEN_MAIN_TO_SHARD", main_shards=N_MAIN, formal_reps=fr["rep_seeds"])
        record("freeze", mode="done", formal_reps=fr["rep_seeds"], underpowered=not fr["power_plan"]["feasible"])
    elif a.stage == "main":
        k = a.k
        if k is None or not 0 <= k < N_MAIN:
            raise SystemExit("main needs K in 0..%d" % (N_MAIN - 1))
        args, points, reps = main_args(m2, gpus, PORTS["main"] + k)
        mine = reps[k::N_MAIN]
        if not mine:
            record("main%d" % k, mode="empty shard", reps=[])
            return
        exp, info = edited_experiment(m2, mine, [])
        record("main%d" % k, mode="shard", reps=mine, arm_orders={r: list(arm_order(m2, reps.index(r))) for r in mine},
               gpus=gpus, port=args.port, edit=info)
        time.sleep(STAGGER_S * k)
        asyncio.run(exp(args, legacy, OUT / "main"))
        record("main%d" % k, mode="done")
    elif a.stage == "final":
        args, points, reps = main_args(m2, "", PORTS["final"])
        merge(m2, legacy, args, OUT / "main", points, reps, "final")
        mark_state(m2, "COMPLETE", finished_utc=now(), scientific_gate="No author M1/M2 pass assigned; intervals only")
        record("final", mode="done")


if __name__ == "__main__":
    main()
