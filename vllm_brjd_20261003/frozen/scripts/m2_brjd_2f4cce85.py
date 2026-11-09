#!/usr/bin/env python3
"""R260401 BRJD-local RD-M2: once-only calibration, pilot freeze, independent main."""
import argparse
import copy
import datetime
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

SEED = 20261003
TAU0, STAGES, INSTR, OUTPUT = 3400, 8, 50, 128
WARMUP, OBSERVATION = 30.0, 120.0
ARMS = ("A0", "A-uncap", "P-admit")
DEADLINE_FACTORS = (1.5, 2.0, 3.0)
LOCAL_SCOPE = "R260401_BRJD_20261003"
HOLD_FACTORS = (1.0, 3.0, 6.0)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_legacy(path):
    spec = importlib.util.spec_from_file_location("m2_legacy_kvbench", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if (mod.INSTR_TOK, mod.OUT_TOK) != (INSTR, OUTPUT):
        raise RuntimeError("Legacy trace constants differ; never silently change the workload.")
    return mod


def package_version(name):
    from importlib.metadata import version
    return version(name)


class Reservation:
    """Single event-loop atomic logical peak reservation; NOT a physical pin API."""
    def __init__(self, token_budget, delta, block_size):
        self.limit = math.floor((1 - delta) * token_budget / block_size) * block_size
        self.block = block_size
        self.used = 0
        self.maximum = 0

    def claim(self, tokens):
        cost = math.ceil(tokens / self.block) * self.block
        if self.used + cost > self.limit:
            return None
        self.used += cost
        self.maximum = max(self.maximum, self.used)
        return cost

    def release(self, cost):
        self.used -= cost
        if self.used < 0:
            raise RuntimeError("Negative reservation: accounting bug.")


def pregen_open(point, rep, baseline_s, np):
    # Rate and holding policy are exogenous; neither is inferred from active slots.
    rng = np.random.default_rng([SEED, rep, point["index"], 991])
    arrivals, t = [], 0.0
    while True:
        t += float(rng.exponential(1.0 / point["rate_per_s"]))
        if t >= WARMUP + OBSERVATION:
            break
        hold = float(rng.choice(point["hold_factors"])) * baseline_s
        arrivals.append({"id": len(arrivals), "arrival_s": t, "target_hold_s": hold})
        if len(arrivals) > 20000:
            raise RuntimeError("Trace exceeds explicit 20000-session guard; revise a NEW plan.")
    return arrivals


def expected_token_seconds(cal, hold_factors):
    # Calibration-based offered logical occupancy approximation, never allocator truth.
    base = cal["measured_baseline_A0_s"]
    peak = cal["peak_tokens"]
    return cal["mean_stage_token_seconds"] + sum(
        peak * max(0.0, f * cal["baseline_A0_s"] - base) for f in hold_factors
    ) / len(hold_factors)


def pilot_points(cal):
    peak_n = math.floor(0.95 * cal["server"]["kv_cache_tokens"] / cal["peak_tokens"])
    ns = sorted(set(max(1, min(128, round(f * max(1, peak_n)))) for f in (0.7, 1.0, 1.3)))
    points = [{"kind": "closed", "N": n, "think_seconds": 1.0} for n in ns]
    holds = list(HOLD_FACTORS)
    lam0 = 0.95 * cal["server"]["kv_cache_tokens"] / expected_token_seconds(cal, holds)
    points.extend({"kind": "open", "rate_per_s": lam0 * f, "hold_factors": holds}
                  for f in (0.7, 1.0, 1.3))
    for i, point in enumerate(points):
        point["index"] = i
    return points


def validate_freeze(f, args, cal):
    required = ("status", "created_utc", "m2_sha256", "legacy_sha256", "calibration_sha256",
                "pilot_summary_sha256", "pilot_summary_path", "points", "rep_seeds",
                "power_plan", "estimated_wall_seconds", "workload", "interpretation")
    missing = [x for x in required if x not in f]
    if missing:
        raise RuntimeError("Formal freeze incomplete: " + ", ".join(missing))
    if f["status"] != "FROZEN":
        raise RuntimeError("Main requires status=FROZEN, not a draft.")
    if f["m2_sha256"] != sha(__file__) or f["legacy_sha256"] != sha(args.legacy):
        raise RuntimeError("Frozen code hashes do not match.")
    if f["calibration_sha256"] != sha(args.calibration):
        raise RuntimeError("Frozen calibration does not match.")
    if not Path(f["pilot_summary_path"]).is_file() or sha(f["pilot_summary_path"]) != f["pilot_summary_sha256"]:
        raise RuntimeError("Frozen pilot source is missing or its SHA-256 differs.")
    pilot = json.loads(Path(f["pilot_summary_path"]).read_text(encoding="utf-8"))
    if not pilot.get("pilot_only") or pilot.get("status") != "COMPLETE":
        raise RuntimeError("Pilot must be complete and explicitly excluded from formal results.")
    if f["created_utc"] <= pilot["finished_utc"]:
        raise RuntimeError("Freeze must be written after pilot completion.")
    if f["workload"] != {"tau0": TAU0, "first_prompt_tokens": TAU0 + INSTR,
                         "stages": STAGES, "instruction_tokens_each_stage": INSTR,
                         "output_tokens_each_stage": OUTPUT, "ignore_eos": True,
                         "warmup_s": WARMUP, "observation_s": OBSERVATION,
                         "drain_s": cal["drain_s"], "deadline_factors": list(DEADLINE_FACTORS),
                         "local_scope": LOCAL_SCOPE, "baseline_A0_s": cal["baseline_A0_s"],
                         "deadline_seconds": cal["deadline_seconds"], "hold_factors": list(HOLD_FACTORS),
                         "hold_target_seconds": cal["hold_target_seconds"]}:
        raise RuntimeError("Frozen workload differs from this runner; do not silently revise it.")
    reps = f["rep_seeds"]
    if len(set(reps)) != len(reps) or any(not isinstance(x, int) or x < 10000 for x in reps):
        raise RuntimeError("Formal rep_seeds must be unique integers >=10000 (pilot uses 0..99).")
    p = f["power_plan"]
    for key in ("primary_endpoint", "contrast", "family_members", "effect_to_detect",
                "pilot_sd_source", "calculation", "required_reps", "feasible"):
        if key not in p:
            raise RuntimeError("Power plan lacks " + key)
    if (not p["feasible"] and f.get("inference_mode") != "interval_estimation_only") or not isinstance(p["required_reps"], int) or p["required_reps"] < 2:
        raise RuntimeError("Formal power is not established as feasible.")
    if len(reps) < p["required_reps"]:
        raise RuntimeError("Frozen repetitions fall below the power plan.")
    if not f["interpretation"].get("open_loop_primary") or f["interpretation"].get("closed_loop_independent_prediction"):
        raise RuntimeError("Open loop must be primary; lambda*h=N is not an independent prediction.")
    if not f["estimated_wall_seconds"] > 0:
        raise RuntimeError("Wall-time estimate must use measured pilot timing.")
    if not f["points"] or not any(x.get("kind") == "open" for x in f["points"]):
        raise RuntimeError("No frozen independent open-loop points.")
    for i, point in enumerate(f["points"]):
        if point.get("index") != i:
            raise RuntimeError("Point indices must be consecutive and frozen.")
        if point["kind"] == "open":
            if point.get("rate_per_s", 0) <= 0 or not point.get("hold_factors") or min(point["hold_factors"]) <= 0:
                raise RuntimeError("Invalid frozen open-loop arrival or hold policy.")
        elif point["kind"] == "closed":
            if not 1 <= point.get("N", 0) <= 128 or point.get("think_seconds") != 1.0:
                raise RuntimeError("Closed loop is the disclosed 1-second pilot workload.")
        else:
            raise RuntimeError("Unknown frozen point kind.")


async def server_description(http, base, legacy, log):
    info = legacy.parse_server_log(str(log))
    try:
        async with http.get(base + "/server_info?config_format=json", timeout=legacy.aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                info["server_info_endpoint"] = await r.json()
    except Exception as e:
        info["server_info_endpoint_error"] = repr(e)
    raw = Path(log).read_text(encoding="utf-8", errors="replace")
    nums = re.findall(r"max_num_seqs[\"']?\s*[:=]\s*(\d+)", raw)
    if not nums and "server_info_endpoint" in info:
        nums = re.findall(r'max_num_seqs[\\\"\' ]*\s*[:=]\s*(\d+)', json.dumps(info["server_info_endpoint"]))
    info["effective_max_num_seqs"] = int(nums[-1]) if nums else None
    if not info.get("kv_cache_tokens", 0) > 0:
        raise RuntimeError("Actual allocator token budget missing from current server log.")
    info["block_size"] = info.get("block_size_seen", 16)
    info["budget_source"] = "allocator startup log; no pool override, no CPU offload"
    return info


async def wait_idle(http, base, legacy):
    for _ in range(120):
        async with http.get(base + "/metrics", timeout=legacy.aiohttp.ClientTimeout(total=5)) as r:
            m = legacy.parse_metrics(await r.text())
        running, waiting = m.get("vllm:num_requests_running"), m.get("vllm:num_requests_waiting")
        if running is None or waiting is None:
            raise RuntimeError("Server running/waiting metrics missing; cannot prove clean cell boundary.")
        if running == waiting == 0:
            return
        await asyncio.sleep(0.25)
    raise RuntimeError("Server did not become idle after drain/cancellation.")


async def session(http, base, legacy, pool, tag, rep, sid, generated, t0, target_hold, records, reservation):
    ctx, instr = legacy.session_trace(pool, TAU0, STAGES, rep, sid)
    peak = TAU0 + STAGES * (INSTR + OUTPUT)
    rec = {"session_id": sid, "generated_s": generated,
           "in_cohort": WARMUP <= generated < WARMUP + OBSERVATION,
           "target_hold_s": target_hold, "status": "generated", "stages": [], "dispatch_actual_s": time.perf_counter() - t0}
    rec["dispatch_lag_s"] = rec["dispatch_actual_s"] - generated
    rec["initial_context_sha256"] = digest(ctx)
    rec["instruction_trace_sha256"] = digest(instr)
    records.append(rec)  # Attributes generated BEFORE admission.
    cost = reservation.claim(peak) if reservation is not None else 0
    if cost is None:
        rec["status"] = "blocked"
        rec["rejection_reason"] = "atomic logical peak-token reservation"
        return rec
    rec["reserved_tokens"] = cost
    try:
        for k in range(STAGES):
            before = len(ctx)
            prompt = ctx + instr[k]  # Legacy semantics: first prompt = 3400+50.
            result = await legacy.stage_request(http, base, prompt, f"m2|{tag}|{rep}|{sid}")
            out = result.pop("out_ids")
            if len(out) != OUTPUT or result["completion_tokens"] != OUTPUT or result["cached_tokens"] is None:
                raise RuntimeError("Fixed-output or cached-token API assertion failed.")
            result.update({"stage": k + 1, "context_tokens_before": before,
                           "sent_prompt_tokens": len(prompt), "context_tokens_after": len(prompt) + len(out),
                           "input_sha256": digest(prompt), "output_sha256": digest(out),
                           "start_s": result.pop("t_start") - t0, "end_s": result.pop("t_end") - t0})
            rec["stages"].append(result)
            ctx = prompt + out
        rec.update(status="completed", completed_s=time.perf_counter() - t0)
        rec["latency_s"] = rec["completed_s"] - generated
        # Explicit app hold policy: beyond completion, APC entries remain evictable.
        await asyncio.sleep(max(0.0, generated + target_hold - (time.perf_counter() - t0)))
    except asyncio.CancelledError:
        if rec["status"] != "completed":
            rec["status"] = "unfinished_at_drain"
        rec["hold_right_censored"] = True
        raise
    except Exception as e:
        rec.update(status="execution_error", error=repr(e))
    finally:
        rec["logical_release_s"] = time.perf_counter() - t0
        rec["logical_holding_s"] = rec["logical_release_s"] - generated
        if reservation is not None:
            reservation.release(cost)
    return rec


async def run_cell(http, base, legacy, pool, point, rep, arm, server, cal, arrivals):
    await wait_idle(http, base, legacy)
    resets = await legacy.reset_prefix_cache(http, base, reset_external=True, tries=200)
    if resets < 1:
        raise RuntimeError("Cache reset failed; no timed cell may follow.")
    poller = legacy.Poller(http, base, dt=0.5)
    poller.start()
    records, tasks = [], []
    reserve = Reservation(server["kv_cache_tokens"], 0.05, server["block_size"]) if arm == "P-admit" else None
    tag = f"{point['kind']}|p{point['index']}"
    t0, wall_start = time.perf_counter(), legacy.now_iso()
    async def execute(sid, target_hold, scheduled=None):
        return await session(http, base, legacy, pool, tag, rep, sid,
                             (time.perf_counter() - t0) if scheduled is None else scheduled, t0, target_hold, records, reserve)
    async def closed_slot(slot):
        cycle = 0
        while time.perf_counter() - t0 < WARMUP + OBSERVATION:
            await execute(slot + cycle * point["N"], 0.0)
            cycle += 1
            await asyncio.sleep(point["think_seconds"])
    async def open_source():
        for a in arrivals:
            await asyncio.sleep(max(0, a["arrival_s"] - (time.perf_counter() - t0)))
            tasks.append(asyncio.create_task(execute(a["id"], a["target_hold_s"], a["arrival_s"])))
    try:
        if point["kind"] == "closed":
            sources = [asyncio.create_task(closed_slot(slot)) for slot in range(point["N"])]
        else:
            sources = [asyncio.create_task(open_source())]
        tasks.extend(sources)
        await asyncio.sleep(WARMUP + OBSERVATION + cal["drain_s"])
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        samples = await poller.stop()
    await wait_idle(http, base, legacy)
    cohort = [x for x in records if x["in_cohort"]]
    done = [x for x in cohort if x["status"] == "completed"]
    attained = {str(f): sum(x["latency_s"] <= cal["baseline_A0_s"] * f for x in done) / len(cohort)
                if cohort else None for f in DEADLINE_FACTORS}
    repeated_stages = [s for x in cohort for s in x["stages"] if s["stage"] > 1]
    denominator = sum(s["context_tokens_before"] for s in repeated_stages)
    hit_ratio = sum(s["cached_tokens"] for s in repeated_stages) / denominator if denominator else None
    first_stage_hits = [s["cached_tokens"] for x in records for s in x["stages"] if s["stage"] == 1]
    counters = []
    pi = legacy.POLL_KEYS.index("vllm:num_preemptions_total")
    for sample in samples:
        if sample[pi + 1] is not None:
            counters.append(sample[pi + 1])
    valid = bool(cohort) and not any(x["status"] == "execution_error" for x in records)
    if any(x != 0 for x in first_stage_hits):
        valid = False  # Unique session salt must not allow cross-session hits.
    return {"point": point, "rep_seed": rep, "arm": arm, "started_utc": wall_start,
            "finished_utc": legacy.now_iso(), "elapsed_wall_s": time.perf_counter() - t0,
            "valid": valid, "cohort_generated": len(cohort), "cohort_completed": len(done),
            "cohort_blocked": sum(x["status"] == "blocked" for x in cohort),
            "cohort_unfinished": sum(x["status"] == "unfinished_at_drain" for x in cohort),
            "attainment_by_deadline_factor": attained,
            "completion_rate_per_s": len(done) / OBSERVATION,
            "mean_completed_latency_s": statistics.mean(x["latency_s"] for x in done) if done else None,
            "cached_previous_context_ratio": hit_ratio,
            "preemptions_delta": counters[-1] - counters[0] if len(counters) >= 2 else None,
            "reservation_max_tokens": reserve.maximum if reserve else None,
            "reservation_limit_tokens": reserve.limit if reserve else None,
            "cache_reset_tries": resets, "sessions": records,
            "poll_keys": legacy.POLL_KEYS, "poll": samples}


def trajectory_audits(rows, points, reps):
    """Record endogenous token drift; never change seeds, admissions or inference."""
    audits = []
    pairs = (("A-uncap", "A0"), ("P-admit", "A0"), ("P-admit", "A-uncap"))
    for point in points:
        for rep in reps:
            cells = {r["arm"]: r for r in rows if r["point"]["index"] == point["index"] and r["rep_seed"] == rep}
            for arm, baseline in pairs:
                left = {x["session_id"]: x for x in cells[arm]["sessions"]}
                right = {x["session_id"]: x for x in cells[baseline]["sessions"]}
                common_generated = sorted(left.keys() & right.keys())
                initial_mismatch = [sid for sid in common_generated if any(
                    left[sid][key] != right[sid][key]
                    for key in ("initial_context_sha256", "instruction_trace_sha256"))]
                common_admitted = [sid for sid in common_generated
                    if left[sid]["in_cohort"] and right[sid]["in_cohort"]
                    and "reserved_tokens" in left[sid] and "reserved_tokens" in right[sid]]
                comparisons, examples, mismatch_count, observed_count = [], [], 0, 0
                for sid in common_admitted:
                    a = {x["stage"]: x for x in left[sid]["stages"]}
                    b = {x["stage"]: x for x in right[sid]["stages"]}
                    stages = sorted(a.keys() & b.keys())
                    mismatch_stages = []
                    for stage in stages:
                        observed_count += 1
                        fields = [key for key in ("input_sha256", "output_sha256") if a[stage][key] != b[stage][key]]
                        if fields:
                            mismatch_count += 1
                            mismatch_stages.append(stage)
                            if len(examples) < 20:
                                examples.append({"session_id": sid, "stage": stage, "different_fields": fields,
                                    "arm_input_sha256": a[stage]["input_sha256"], "baseline_input_sha256": b[stage]["input_sha256"],
                                    "arm_output_sha256": a[stage]["output_sha256"], "baseline_output_sha256": b[stage]["output_sha256"]})
                    signature = lambda values: digest([
                        [k, values[k]["input_sha256"], values[k]["output_sha256"]] for k in sorted(values)])
                    full = len(a) == len(b) == STAGES
                    comparisons.append({"session_id": sid, "arm_trace_sha256": signature(a),
                        "baseline_trace_sha256": signature(b), "arm_observed_stages": len(a),
                        "baseline_observed_stages": len(b), "compared_stages": stages,
                        "mismatch_stages": mismatch_stages, "complete_in_both": full,
                        "identical_full_trajectory_verified": full and not mismatch_stages})
                full_verified = bool(comparisons) and not initial_mismatch and all(
                    x["identical_full_trajectory_verified"] for x in comparisons)
                status = "DRIFT_DETECTED" if initial_mismatch or mismatch_count else (
                    "IDENTICAL_SHARED_ADMITTED_FULL_TRAJECTORIES" if full_verified else "PARTIAL_OR_NO_SHARED_TRAJECTORY_EVIDENCE")
                audits.append({"point_index": point["index"], "kind": point["kind"], "rep_seed": rep,
                    "contrast": arm + "-" + baseline, "arm": arm, "baseline_arm": baseline, "status": status,
                    "common_generated_session_ids": common_generated, "initial_or_instruction_mismatch_session_ids": initial_mismatch,
                    "common_cohort_admitted_session_ids": common_admitted, "compared_stage_count": observed_count,
                    "mismatched_stage_count": mismatch_count, "first_20_mismatch_examples": examples,
                    "mismatch_examples_truncated": mismatch_count > len(examples), "shared_admitted_trace_comparisons": comparisons,
                    "arrival_times_identical_by_design": point["kind"] == "open",
                    "identical_workload_claim_permitted": False,
                    "scope": "Common cohort-admitted IDs only; excluded blocked/unfinished IDs stay in the original endpoint denominator. Closed-loop same ID does not imply same generated time.",
                    "interpretation": "Greedy generation can drift with scheduling; compare policies under common initial/instruction seeds and (open loop) fixed arrivals. This is not proof of an identical realized token workload."})
    return audits


def launch_args(args, arm):
    extra = [] if arm == "A0" else ["--max-num-seqs", str(args.uncap)]
    if getattr(args, "pool_blocks", None) is not None:
        extra += ["--num-gpu-blocks-override", str(args.pool_blocks)]
    return SimpleNamespace(model_path=args.model_path, port=args.port, max_model_len=12288,
                           gpu_util=0.90, kv_dtype="auto", tp=1, offload_gib=0,
                           enforce_eager=False, gpus=args.gpus, extra=extra)



def installed_scheduler_evidence(args, arm, legacy):
    code = r"""import hashlib,inspect,json,sys
from vllm.engine.arg_utils import EngineArgs
from vllm.usage.usage_lib import UsageContext
kw=dict(model=sys.argv[1],served_model_name="m",max_model_len=12288,gpu_memory_utilization=0.90,
        kv_cache_dtype="auto",tensor_parallel_size=1,enable_prefix_caching=True,seed=0,enforce_eager=False)
if sys.argv[2]!="A0": kw["max_num_seqs"]=int(sys.argv[3])
cfg=EngineArgs(**kw).create_engine_config(usage_context=UsageContext.OPENAI_API_SERVER)
src=inspect.getsourcefile(EngineArgs)
out=dict(max_num_seqs=cfg.scheduler_config.max_num_seqs,
         max_num_batched_tokens=cfg.scheduler_config.max_num_batched_tokens,
         enable_chunked_prefill=cfg.scheduler_config.enable_chunked_prefill,
         usage_context="OPENAI_API_SERVER",source_file=src,
         source_sha256=hashlib.sha256(open(src,"rb").read()).hexdigest())
print("M2_SCHEDULER_JSON="+json.dumps(out))
"""
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=args.gpus, HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", VLLM_NO_USAGE_STATS="1")
    result = subprocess.run([sys.executable, "-c", code, args.model_path, arm, str(args.uncap)],
                            env=env, capture_output=True, text=True, timeout=120)
    candidates = [line.split("=",1)[1] for line in result.stdout.splitlines() if line.startswith("M2_SCHEDULER_JSON=")]
    if result.returncode or not candidates:
        raise RuntimeError("Installed scheduler config fallback failed: " + result.stderr[-2000:])
    return json.loads(candidates[-1])


async def capacity_preflight(args, legacy, out, pool):
    """Natural budgets first; common largest feasible matched pool declared explicitly."""
    natural = {}
    base = f"http://127.0.0.1:{args.port}"
    for arm in ("A0", "A-uncap"):
        probe_args = copy.copy(args)
        probe_args.pool_blocks = None
        log = out / f"natural_capacity_{arm}.log"
        process, cmd = legacy.launch_server(launch_args(probe_args, arm), str(log))
        try:
            ready = await legacy.wait_ready(base, process, args.ready_timeout)
            async with legacy.aiohttp.ClientSession(connector=legacy.aiohttp.TCPConnector(limit=0)) as http:
                info = await server_description(http, base, legacy, log)
                if info["effective_max_num_seqs"] is None:
                    info["installed_scheduler_evidence"] = installed_scheduler_evidence(args, arm, legacy)
                    info["effective_max_num_seqs"] = info["installed_scheduler_evidence"]["max_num_seqs"]
                probe = await legacy.api_probe(http, base, pool)
                if not probe["salt_isolates"] or not probe["token_ids_returned"]:
                    raise RuntimeError("Natural capacity API probe failed.")
                natural[arm] = {"server": info, "serve_cmd": cmd, "ready_seconds": ready, "api_probe": probe}
        finally:
            legacy.stop_server(process)
    default = natural["A0"]["server"]["effective_max_num_seqs"]
    uncapped = natural["A-uncap"]["server"]["effective_max_num_seqs"]
    if not uncapped > default:
        raise RuntimeError("Explicit uncap does not exceed the measured installed A0 default.")
    sizes = {natural[a]["server"]["block_size"] for a in natural}
    if len(sizes) != 1:
        raise RuntimeError("Different actual block sizes prevent matched capacity.")
    block = sizes.pop()
    blocks = min(natural[a]["server"]["kv_cache_tokens"] // block for a in natural)
    if blocks < 1:
        raise RuntimeError("No common feasible native KV pool.")
    matched = {"status": "FROZEN_BEFORE_PILOT", "created_utc": legacy.now_iso(),
               "natural_capacity": natural, "block_size": block, "common_blocks": blocks,
               "common_tokens": blocks * block,
               "configuration_change": "matched-capacity additional experiment: largest mutually feasible natural pool; same explicit block override in all arms",
               "not_natural_pool_claim": True, "not_selected_to_make_N_about_8": True}
    write_json(out / "capacity_before_pilot.json", matched)
    args.pool_blocks = blocks
    # Verify changed scheduler with this exact matched pool BEFORE spending pilot hours.
    log = out / "matched_capacity_A-uncap.log"
    process, cmd = legacy.launch_server(launch_args(args, "A-uncap"), str(log))
    try:
        ready = await legacy.wait_ready(base, process, args.ready_timeout)
        async with legacy.aiohttp.ClientSession(connector=legacy.aiohttp.TCPConnector(limit=0)) as http:
            info = await server_description(http, base, legacy, log)
            if info["effective_max_num_seqs"] is None:
                info["installed_scheduler_evidence"] = installed_scheduler_evidence(args, "A-uncap", legacy)
                info["effective_max_num_seqs"] = info["installed_scheduler_evidence"]["max_num_seqs"]
            if info["kv_cache_tokens"] != matched["common_tokens"]:
                raise RuntimeError("Matched A-uncap allocator tokens differ from declared common budget.")
            probe = await legacy.api_probe(http, base, pool)
            if not probe["salt_isolates"] or not probe["token_ids_returned"]:
                raise RuntimeError("Matched capacity API probe failed.")
            matched["matched_A-uncap_verification"] = {"server": info, "serve_cmd": cmd,
                                                      "ready_seconds": ready, "probe": probe}
            write_json(out / "capacity_before_pilot.json", matched)
    finally:
        legacy.stop_server(process)
    return matched



def hardware_identity():
    command = ["nvidia-smi", "--query-gpu=uuid,name,memory.total,driver_version", "--format=csv,noheader"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Cannot record BRJD GPU hardware identity: " + result.stderr[-1000:])
    return result.stdout.strip()


def validate_local_calibration(cal, args, legacy):
    if cal.get("status") != "COMPLETE" or cal.get("local_scope") != LOCAL_SCOPE:
        raise RuntimeError("Not a complete BRJD-local calibration; cross-host data reuse forbidden.")
    if cal.get("m2_sha256") != sha(__file__) or cal.get("legacy_sha256") != sha(args.legacy):
        raise RuntimeError("BRJD local calibration code hash differs.")
    if cal.get("machine") != socket.gethostname() or cal.get("hardware_identity") != hardware_identity():
        raise RuntimeError("BRJD local calibration host/GPU identity differs.")
    if cal.get("model_path") != args.model_path or cal.get("model_fingerprint") != legacy.model_fingerprint(args.model_path):
        raise RuntimeError("BRJD local calibration model differs.")
    freeze_file = Path(cal["deadline_source"])
    if not freeze_file.is_file() or sha(freeze_file) != cal["deadline_source_sha256"]:
        raise RuntimeError("Once-only BRJD baseline freeze is missing or its SHA differs.")
    frozen = json.loads(freeze_file.read_text(encoding="utf-8"))
    if frozen.get("status") != "FROZEN_BEFORE_PILOT" or frozen.get("local_scope") != LOCAL_SCOPE:
        raise RuntimeError("BRJD baseline is not frozen before pilot.")
    baseline = cal["baseline_A0_s"]
    if not math.isfinite(baseline) or baseline <= 0 or baseline != cal["measured_baseline_A0_s"]:
        raise RuntimeError("BRJD baseline must equal its local measured median.")
    if frozen["baseline_A0_s"] != baseline or frozen["measurements_sha256"] != digest(cal["measurements"]):
        raise RuntimeError("Local single-flow measurement evidence changed.")
    if frozen["measurement_count"] != len(cal["measurements"]) or len(cal["measurements"]) < 3:
        raise RuntimeError("Local single-flow calibration repetitions incomplete.")
    if baseline != statistics.median(x["latency_s"] for x in cal["measurements"]):
        raise RuntimeError("Local measured median does not match frozen baseline.")
    if cal["deadline_seconds"] != [baseline * f for f in DEADLINE_FACTORS] or cal["hold_target_seconds"] != [baseline * f for f in HOLD_FACTORS]:
        raise RuntimeError("Local frozen deadline/holding scale changed.")
    if cal["drain_s"] != math.ceil(6 * baseline) or frozen["deadline_seconds"] != cal["deadline_seconds"] or frozen["hold_target_seconds"] != cal["hold_target_seconds"]:
        raise RuntimeError("Local frozen deadline/drain/holding evidence differs.")
    if frozen["common_pool_tokens"] != cal["matched_capacity"]["common_tokens"] or frozen["capacity_before_pilot_sha256"] != cal["capacity_before_pilot_sha256"]:
        raise RuntimeError("Local baseline matched capacity evidence differs.")
    if frozen["cross_host_baseline_reuse"] or frozen["post_pilot_recalibration_allowed"] or cal["post_pilot_recalibration_allowed"]:
        raise RuntimeError("Local protocol forbids cross-host deadline reuse or post-pilot recalibration.")


async def calibration(args, legacy, out):
    # No recalibration may overwrite a previously frozen baseline, including direct CLI calls.
    if (out / "calibration.json").exists() or (out / "local_baseline_before_pilot.json").exists():
        raise RuntimeError("Calibration already frozen: resume pipeline or choose a NEW declared output root.")
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model_path, local_files_only=True)
    pool = legacy.word_pool(tok, args.model_path)
    if not len(pool):
        raise RuntimeError("Token word pool empty.")
    matched = await capacity_preflight(args, legacy, out, pool)
    log = out / "calibration_server.log"
    process, command = legacy.launch_server(launch_args(args, "A0"), str(log))
    base = f"http://127.0.0.1:{args.port}"
    try:
        ready_s = await legacy.wait_ready(base, process, args.ready_timeout)
        async with legacy.aiohttp.ClientSession(connector=legacy.aiohttp.TCPConnector(limit=0)) as http:
            server = await server_description(http, base, legacy, log)
            if server["effective_max_num_seqs"] is None:
                server["installed_scheduler_evidence"] = installed_scheduler_evidence(args, "A0", legacy)
                server["effective_max_num_seqs"] = server["installed_scheduler_evidence"]["max_num_seqs"]
            if server["kv_cache_tokens"] != matched["common_tokens"]:
                raise RuntimeError("Matched A0 allocator tokens differ from declared common budget.")
            probe = await legacy.api_probe(http, base, pool)
            if not probe["salt_isolates"] or not probe["token_ids_returned"]:
                raise RuntimeError("API probe failed: " + repr(probe))
            # Warmup and calibration are raw calibration only, never formal load cells.
            stop = time.perf_counter() + WARMUP
            while time.perf_counter() < stop:
                records = []
                t = time.perf_counter()
                await session(http, base, legacy, pool, "cal_warm", 500, 0, 0, t, 0, records, None)
                if records[0]["status"] != "completed":
                    raise RuntimeError("Calibration warmup failed.")
            measurements = []
            for rep in range(args.calibration_reps):
                await wait_idle(http, base, legacy)
                if await legacy.reset_prefix_cache(http, base, reset_external=True, tries=200) < 1:
                    raise RuntimeError("Calibration cache reset failed.")
                records = []
                t = time.perf_counter()
                await session(http, base, legacy, pool, "cal", 600 + rep, 0, 0, t, 0, records, None)
                if records[0]["status"] != "completed":
                    raise RuntimeError("Calibration execution failed: " + repr(records[0]))
                measurements.append(records[0])
        measured_baseline = statistics.median(x["latency_s"] for x in measurements)
        baseline = measured_baseline
        if not math.isfinite(baseline) or baseline <= 0:
            raise RuntimeError("BRJD local single-flow baseline is not finite and positive.")
        baseline_record = {
            "status": "FROZEN_BEFORE_PILOT", "created_utc": legacy.now_iso(),
            "local_scope": LOCAL_SCOPE, "machine": socket.gethostname(),
            "hardware_identity": hardware_identity(), "model_path": args.model_path,
            "model_fingerprint": legacy.model_fingerprint(args.model_path),
            "m2_sha256": sha(__file__), "legacy_sha256": sha(args.legacy),
            "baseline_A0_s": baseline, "measurement_count": len(measurements),
            "measurements_sha256": digest(measurements),
            "deadline_factors": list(DEADLINE_FACTORS),
            "deadline_seconds": [baseline * f for f in DEADLINE_FACTORS],
            "hold_factors": list(HOLD_FACTORS),
            "hold_target_seconds": [baseline * f for f in HOLD_FACTORS],
            "drain_s": math.ceil(6 * baseline),
            "common_pool_tokens": matched["common_tokens"],
            "capacity_before_pilot_sha256": sha(out / "capacity_before_pilot.json"),
            "workload": {"tau0": TAU0, "first_prompt_tokens": TAU0 + INSTR,
                         "stages": STAGES, "instruction_tokens": INSTR,
                         "output_tokens": OUTPUT, "ignore_eos": True},
            "selection_rule": "median of all predeclared local single-flow A0 calibration repeats; freeze once before pilot",
            "cross_host_baseline_reuse": False,
            "post_pilot_recalibration_allowed": False}
        baseline_file = out / "local_baseline_before_pilot.json"
        write_json(baseline_file, baseline_record)
        integrals = [sum(0.5 * (s["sent_prompt_tokens"] + s["context_tokens_after"]) * s["e2e"]
                         for s in x["stages"]) for x in measurements]
        result = {"status": "COMPLETE", "calibration_only": True, "started_utc": legacy.now_iso(),
                  "finished_utc": legacy.now_iso(), "machine": socket.gethostname(),
                  "python": platform.python_version(), "vllm": package_version("vllm"),
                  "model_path": args.model_path, "model_fingerprint": legacy.model_fingerprint(args.model_path),
                  "m2_sha256": sha(__file__), "legacy_sha256": sha(args.legacy),
                  "serve_cmd": command, "ready_seconds": ready_s, "server": server, "api_probe": probe,
                  "matched_capacity": matched, "capacity_before_pilot_sha256": sha(out / "capacity_before_pilot.json"),
                  "baseline_A0_s": baseline, "measured_baseline_A0_s": measured_baseline,
                  "local_scope": LOCAL_SCOPE, "hardware_identity": baseline_record["hardware_identity"],
                  "deadline_source_sha256": sha(baseline_file),
                  "deadline_source": str(baseline_file),
                  "deadline_source_rule": "once-only BRJD local measured baseline; no four-GPU timing reuse",
                  "deadline_seconds": baseline_record["deadline_seconds"],
                  "hold_target_seconds": baseline_record["hold_target_seconds"],
                  "post_pilot_recalibration_allowed": False,
                  "drain_s": math.ceil(6 * baseline),
                  "first_prompt_tokens": TAU0 + INSTR, "peak_tokens": TAU0 + STAGES * (INSTR + OUTPUT),
                  "mean_stage_token_seconds": statistics.mean(integrals),
                  "logical_occupancy_approximation": "stage midpoint token count × observed stage duration",
                  "measurements": measurements}
        write_json(out / "calibration.json", result)
        print(json.dumps({k: result[k] for k in ("baseline_A0_s", "drain_s", "peak_tokens", "ready_seconds")}), flush=True)
    finally:
        legacy.stop_server(process)


async def experiment(args, legacy, out):
    from transformers import AutoTokenizer
    cal = json.loads(Path(args.calibration).read_text(encoding="utf-8"))
    validate_local_calibration(cal, args, legacy)
    if cal.get("status") != "COMPLETE" or cal["legacy_sha256"] != sha(args.legacy):
        raise RuntimeError("Calibration incomplete or legacy hash changed.")
    if cal["model_path"] != args.model_path:
        raise RuntimeError("Calibration model path differs.")
    matched = cal.get("matched_capacity")
    if not matched or "matched_A-uncap_verification" not in matched:
        raise RuntimeError("Matched capacity/scheduler verification must precede pilot.")
    args.pool_blocks = matched["common_blocks"]
    formal = args.phase == "main"
    if formal:
        if not args.freeze or not args.freeze_sha256 or sha(args.freeze) != args.freeze_sha256:
            raise RuntimeError("Main requires an existing freeze file and its exact SHA-256.")
        frozen = json.loads(Path(args.freeze).read_text(encoding="utf-8"))
        validate_freeze(frozen, args, cal)
        points, reps = frozen["points"], frozen["rep_seeds"]
    else:
        points = getattr(args, "predetermined_points", None) or pilot_points(cal)
        reps = list(range(args.pilot_reps))
    tok = AutoTokenizer.from_pretrained(args.model_path, local_files_only=True)
    pool = legacy.word_pool(tok, args.model_path)
    traces = {}
    for point in points:
        if point["kind"] == "open":
            for rep in reps:
                trace = pregen_open(point, rep, cal["baseline_A0_s"], legacy.np)
                traces[(point["index"], rep)] = trace
                write_json(out / "arrival_traces" / f"p{point['index']}_r{rep}.json", {
                    "generated_before_any_timed_arm": True, "point": point, "rep_seed": rep,
                    "trace": trace, "trace_sha256": digest(trace)})
    manifest = {"phase": args.phase, "pilot_only": not formal, "points": points, "rep_seeds": reps,
                "calibration_sha256": sha(args.calibration), "m2_sha256": sha(__file__),
                "legacy_sha256": sha(args.legacy), "machine": socket.gethostname(),
                "local_scope": LOCAL_SCOPE, "hardware_identity": cal["hardware_identity"],
                "deadline_source_sha256": cal["deadline_source_sha256"],
                "baseline_A0_s": cal["baseline_A0_s"], "deadline_seconds": cal["deadline_seconds"],
                "model_fingerprint": legacy.model_fingerprint(args.model_path),
                "uncap": args.uncap, "eager_all_arms": False, "delta": 0.05,
                "matched_common_pool_blocks": args.pool_blocks,
                "matched_capacity_sha256": digest(matched),
                "natural_pool_claim": False,
                "trace_hashes": {f"p{k[0]}_r{k[1]}": digest(v) for k, v in traces.items()},
                "freeze_sha256": args.freeze_sha256 if formal else None,
                "P-pin": {"status": "NOT_MEASURED", "reason": "No verified physical pin API is wired."},
                "policy": "P-admit is peak reservation plus evictable APC; no pin guarantee.",
                "token_trace_audit": "SHA256 canonical JSON integer arrays for every prompt/output; common cohort-admitted SID comparisons are descriptive and never alter admissions or endpoint denominators."}
    mh = digest(manifest)
    manifest_path = out / "run_manifest.json"
    if manifest_path.exists() and digest(json.loads(manifest_path.read_text(encoding="utf-8"))) != mh:
        raise RuntimeError("Resume manifest differs: preserve old data and choose a fresh output path.")
    write_json(manifest_path, manifest)
    rows, startup = [], []
    base = f"http://127.0.0.1:{args.port}"
    for i, rep in enumerate(reps):
        order = ARMS[i % len(ARMS):] + ARMS[:i % len(ARMS)]
        for arm in order:
            pending = [p for p in points if not (out / "cells" / f"p{p['index']}_{arm}_r{rep}.json").exists()]
            if not pending:
                continue
            log = out / f"server_{arm}_r{rep}.log"
            process, command = legacy.launch_server(launch_args(args, arm), str(log))
            try:
                ready_s = await legacy.wait_ready(base, process, args.ready_timeout)
                startup.append(ready_s)
                async with legacy.aiohttp.ClientSession(connector=legacy.aiohttp.TCPConnector(limit=0)) as http:
                    info = await server_description(http, base, legacy, log)
                    if info["effective_max_num_seqs"] is None:
                        info["installed_scheduler_evidence"] = installed_scheduler_evidence(args, arm, legacy)
                        info["effective_max_num_seqs"] = info["installed_scheduler_evidence"]["max_num_seqs"]
                    if info["kv_cache_tokens"] != matched["common_tokens"]:
                        raise RuntimeError("Actual timed arm does not use the frozen matched KV budget.")
                    probe = await legacy.api_probe(http, base, pool)
                    if not probe["salt_isolates"] or not probe["token_ids_returned"]:
                        raise RuntimeError("Arm API probe failed.")
                    if formal:
                        if cal["server"]["effective_max_num_seqs"] is None or info["effective_max_num_seqs"] is None:
                            raise RuntimeError("Cannot verify default versus uncap scheduler for formal comparisons.")
                        if arm != "A0" and info["effective_max_num_seqs"] <= cal["server"]["effective_max_num_seqs"]:
                            raise RuntimeError("Uncap setting does not exceed measured A0 default.")
                        ratio = info["kv_cache_tokens"] / cal["server"]["kv_cache_tokens"]
                        if abs(ratio - 1) > 0.02:
                            raise RuntimeError("Actual arm KV budget differs >2%; formal comparison confounded.")
                    write_json(out / f"server_{arm}_r{rep}.json", {
                        "server": info, "probe": probe, "serve_cmd": command,
                        "ready_seconds": ready_s, "machine": socket.gethostname(),
                        "manifest_sha256": mh, "actual_budget_ratio_to_calibration": info["kv_cache_tokens"] / cal["server"]["kv_cache_tokens"]})
                    for point in pending:
                        row = await run_cell(http, base, legacy, pool, point, rep, arm, info, cal,
                                             traces.get((point["index"], rep), []))
                        row.update(manifest_sha256=mh, phase=args.phase, pilot_only=not formal,
                                   server=info, machine=socket.gethostname())
                        write_json(out / "cells" / f"p{point['index']}_{arm}_r{rep}.json", row)
                        print(legacy.now_iso(), args.phase, arm, rep, point, "valid=", row["valid"],
                              "generated=", row["cohort_generated"], "attainment=", row["attainment_by_deadline_factor"], flush=True)
                        if not row["valid"]:
                            raise RuntimeError("Invalid timed cell; preserved, not silently repeated or included.")
            finally:
                legacy.stop_server(process)
    for rep in reps:
        for arm in ARMS:
            for point in points:
                row = json.loads((out / "cells" / f"p{point['index']}_{arm}_r{rep}.json").read_text(encoding="utf-8"))
                if row["manifest_sha256"] != mh or not row["valid"]:
                    raise RuntimeError("Incomplete, invalid, or mixed-manifest cell set.")
                rows.append(row)
    startup = []
    for rep in reps:
        for arm in ARMS:
            record_file = out / f"server_{arm}_r{rep}.json"
            record = json.loads(record_file.read_text(encoding="utf-8"))
            if record["manifest_sha256"] != mh or record["ready_seconds"] <= 0:
                raise RuntimeError("Persisted startup evidence does not match this manifest.")
            startup.append(record["ready_seconds"])
    variances = []
    for point in points:
        for arm, baseline_arm in (("A-uncap", "A0"), ("P-admit", "A0"), ("P-admit", "A-uncap")):
            for factor in DEADLINE_FACTORS:
                differences = []
                for rep in reps:
                    a = next(r for r in rows if r["point"]["index"] == point["index"] and r["arm"] == arm and r["rep_seed"] == rep)
                    b = next(r for r in rows if r["point"]["index"] == point["index"] and r["arm"] == baseline_arm and r["rep_seed"] == rep)
                    differences.append(a["attainment_by_deadline_factor"][str(factor)] - b["attainment_by_deadline_factor"][str(factor)])
                variances.append({"point_index": point["index"], "contrast": arm + "-" + baseline_arm, "deadline_factor": factor,
                                  "paired_differences": differences,
                                  "sample_sd": statistics.stdev(differences) if len(differences) > 1 else None,
                                  "pilot_is_not_power_guarantee": not formal})
    audits = trajectory_audits(rows, points, reps)
    audit_path = out / "token_trajectory_audit.json"
    write_json(audit_path, {"phase": args.phase, "manifest_sha256": mh, "audits": audits,
        "identical_workload_claim_permitted": False, "endpoint_denominators_unchanged": True})
    drift = any(x["status"] == "DRIFT_DETECTED" for x in audits)
    per_cell = statistics.median(r["elapsed_wall_s"] for r in rows)
    summary = {"status": "COMPLETE", "pilot_only": not formal, "phase": args.phase,
               "finished_utc": legacy.now_iso(), "manifest_sha256": mh, "cells": len(rows),
               "median_observed_cell_wall_s": per_cell, "startup_seconds": startup,
               "median_observed_startup_s": statistics.median(startup) if startup else None,
               "paired_variances": variances, "points": points, "rep_seeds": reps,
               "formal_inference": "NOT_PERFORMED; predeclared family and power required before main" if not formal else
                   "Raw formal cells collected; analyze only frozen endpoints and family.",
               "closed_loop_prediction": "lambda*h=N is an identity, not independently validated",
               "P-pin": "NOT_MEASURED", "local_scope": LOCAL_SCOPE,
               "token_trajectory_audit_path": str(audit_path),
               "token_trajectory_audit_sha256": sha(audit_path),
               "token_generation_drift_observed": drift,
               "identical_realized_token_workload_claim": False}
    if startup:
        summary["estimated_10rep_same_grid_seconds"] = len(points) * len(ARMS) * 10 * per_cell + len(ARMS) * 10 * statistics.median(startup)
    if formal:
        from scipy.stats import t as student_t
        family = {(x["point_index"], x["contrast"], x["deadline_factor"]) for x in frozen["power_plan"]["family_members"]}
        intervals = []
        for x in variances:
            key = (x["point_index"], x["contrast"], x["deadline_factor"])
            if key not in family:
                continue
            n = len(x["paired_differences"])
            mean = statistics.mean(x["paired_differences"])
            critical = float(student_t.ppf(1 - 0.05 / (2 * len(family)), n - 1))
            half = critical * x["sample_sd"] / math.sqrt(n)
            intervals.append({**x, "mean": mean, "simultaneous_lower": max(-1.0, mean - half),
                              "simultaneous_upper": min(1.0, mean + half),
                              "observed_half_width": half, "method": "Bonferroni paired Student-t",
                              "assumption": "independent repeat differences approximately normal"})
        if len(intervals) != len(family):
            raise RuntimeError("Frozen family members are missing from collected endpoints.")
        summary["simultaneous_intervals"] = intervals
        summary["inference_mode"] = frozen.get("inference_mode", "interval_estimation_only")
        summary["pilot_forecast_underpowered"] = not frozen["power_plan"]["feasible"]
        summary["author_M1_M2_pass"] = "NOT_ASSIGNED; no independent occupancy proof or original no-think workload"
    write_json(out / ("main_summary.json" if formal else "pilot_summary.json"), summary)
    write_json(out / "DONE.json", {"finished_utc": legacy.now_iso(), "manifest_sha256": mh})
    print(legacy.now_iso(), args.phase, "COMPLETE", "cells=", len(rows), flush=True)



async def pipeline(args, legacy, out):
    from scipy.stats import t as student_t  # Check dependency BEFORE reserving GPU time.
    cal_dir, pilot_dir, main_dir = out / "calibration", out / "pilot", out / "main"
    cal_dir.mkdir(parents=True, exist_ok=True)
    state_path = out / "pipeline_state.json"
    state = {"started_utc": legacy.now_iso(), "status": "CALIBRATION"}
    write_json(state_path, state)
    try:
        cal_file = cal_dir / "calibration.json"
        if not cal_file.exists():
            await calibration(args, legacy, cal_dir)
        cal = json.loads(cal_file.read_text(encoding="utf-8"))
        if cal.get("status") != "COMPLETE" or cal["m2_sha256"] != sha(__file__) or cal["legacy_sha256"] != sha(args.legacy):
            raise RuntimeError("Pipeline calibration not current; preserve it and choose a new output root.")
        validate_local_calibration(cal, args, legacy)
        points = pilot_points(cal)
        preplan_path = out / "sampling_plan_before_pilot.json"
        fixed_rules = {"points": points, "m2_sha256": sha(__file__), "legacy_sha256": sha(args.legacy),
                       "calibration_sha256": sha(cal_file), "pilot_rep_seeds": list(range(args.pilot_reps)),
                       "formal_rep_min": 10, "formal_rep_cap": 20, "alpha": 0.05,
                       "target_primary_simultaneous_half_width": 0.05,
                       "selection_rule": "max across open-loop P-admit minus A-uncap paired SDs; smallest n in 10..20 whose Bonferroni t forecast half width <=0.05; otherwise n=20, underpowered",
                       "formal_range_rule": "Exactly the same capacity-derived points fixed before pilot; no endpoint-driven range selection",
                       "local_scope": LOCAL_SCOPE,
                       "baseline_before_pilot_sha256": cal["deadline_source_sha256"],
                       "baseline_A0_s": cal["baseline_A0_s"],
                       "deadline_factors": list(DEADLINE_FACTORS),
                       "deadline_seconds": cal["deadline_seconds"],
                       "hold_factors": list(HOLD_FACTORS),
                       "hold_target_seconds": cal["hold_target_seconds"],
                       "drain_s": cal["drain_s"],
                       "workload": {"tau0": TAU0, "first_prompt_tokens": TAU0 + INSTR,
                                    "stages": STAGES, "instruction_tokens_each_stage": INSTR,
                                    "output_tokens_each_stage": OUTPUT, "ignore_eos": True,
                                    "warmup_s": WARMUP, "observation_s": OBSERVATION},
                       "holding_targets": "Exogenous factors 1/3/6 of frozen BRJD local baseline; actual service may extend hold",
                       "closed_loop": "Disclosed 1-second think workload; descriptive only",
                       "formal_inference_rule": "Bonferroni paired-t simultaneous intervals; no automatic M1/M2 pass",
                       "P-pin": "NOT_MEASURED",
                       "token_trajectory_audit": "Per-stage prompt/output SHA audit will be produced after pilot/main; it is not a pre-pilot outcome.",
                       "identical_realized_token_workload_claim": False,
                       "token_trajectory_interpretation": "Initial/instruction seeds and open-loop arrivals are shared; model-generated tokens are endogenous. Observed drift disallows an identical-prompt latency interpretation; paired policy endpoint estimates retain every generated cohort session."}
        if preplan_path.exists():
            previous = json.loads(preplan_path.read_text(encoding="utf-8"))
            if previous["fixed_rules"] != fixed_rules:
                raise RuntimeError("Pre-pilot sampling rules changed; no adaptive overwrite.")
        else:
            write_json(preplan_path, {"created_utc": legacy.now_iso(), "status": "FROZEN_BEFORE_PILOT",
                                     "fixed_rules": fixed_rules})
        pilot_args = copy.copy(args)
        pilot_args.phase, pilot_args.calibration = "pilot", str(cal_file)
        pilot_args.predetermined_points = points
        state.update(status="PILOT", sampling_plan_sha256=sha(preplan_path))
        write_json(state_path, state)
        if not (pilot_dir / "pilot_summary.json").exists():
            await experiment(pilot_args, legacy, pilot_dir)
        pilot_file = pilot_dir / "pilot_summary.json"
        pilot = json.loads(pilot_file.read_text(encoding="utf-8"))
        if pilot.get("status") != "COMPLETE" or not pilot.get("pilot_only") or pilot["points"] != points:
            raise RuntimeError("Pipeline pilot incomplete or axes differ from pre-pilot freeze.")
        families = []
        for point in points:
            if point["kind"] == "open":
                for factor in DEADLINE_FACTORS:
                    families.append({"point_index": point["index"], "contrast": "P-admit-A-uncap",
                                     "deadline_factor": factor})
        sds = [x["sample_sd"] for x in pilot["paired_variances"]
               if x["contrast"] == "P-admit-A-uncap" and points[x["point_index"]]["kind"] == "open"]
        if len(sds) != len(families) or any(x is None or not math.isfinite(x) for x in sds):
            raise RuntimeError("Primary pilot variance is unavailable.")
        sd = max(sds)
        candidates = []
        for n in range(10, 21):
            half = float(student_t.ppf(1 - 0.05 / (2 * len(families)), n - 1)) * sd / math.sqrt(n)
            candidates.append({"n": n, "forecast_half_width": half})
        feasible = [x for x in candidates if x["forecast_half_width"] <= 0.05]
        n = feasible[0]["n"] if feasible else 20
        underpowered = not bool(feasible)
        startup = pilot.get("median_observed_startup_s")
        if startup is None:
            raise RuntimeError("Pilot lacks observed startup time; wall-time forecast unavailable.")
        estimated = len(points) * len(ARMS) * n * pilot["median_observed_cell_wall_s"] + len(ARMS) * n * startup
        power_record = {"forecast_only_not_guarantee": True, "pilot_excluded_from_main": True,
                        "alpha": 0.05, "primary_family_size": len(families),
                        "effect_or_precision": "5 percentage-point simultaneous CI half-width, not significance power",
                        "maximum_pilot_paired_sd": sd, "candidates": candidates, "selected_reps": n,
                        "underpowered_at_cap": underpowered,
                        "distribution_assumption": "independent repeat differences approximately normal",
                        "pilot_source_sha256": sha(pilot_file),
                        "zero_pilot_sd_caveat": "A zero sample SD from three repeats is not proof of zero population variance.",
                        "estimated_total_main_wall_s": estimated,
                        "cell_wall_source_s": pilot["median_observed_cell_wall_s"],
                        "startup_source_s": startup}
        write_json(out / "POWER_WORKTIME.json", power_record)
        freeze_file = out / "formal_freeze.json"
        freeze = {"status": "FROZEN", "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "m2_sha256": sha(__file__), "legacy_sha256": sha(args.legacy),
                  "calibration_sha256": sha(cal_file),
                  "pilot_summary_path": str(pilot_file), "pilot_summary_sha256": sha(pilot_file),
                  "sampling_plan_before_pilot_sha256": sha(preplan_path),
                  "points": points, "rep_seeds": list(range(10000, 10000 + n)),
                  "inference_mode": "interval_estimation_only",
                  "estimated_wall_seconds": estimated,
                  "power_plan": {"primary_endpoint": "all-generated attainment at three pre-fixed deadlines",
                                 "contrast": "P-admit-A-uncap on open loop", "family_members": families,
                                 "effect_to_detect": "simultaneous half-width 0.05",
                                 "pilot_sd_source": str(pilot_file), "calculation": power_record,
                                 "required_reps": n, "feasible": not underpowered},
                  "workload": {"tau0": TAU0, "first_prompt_tokens": TAU0 + INSTR,
                               "stages": STAGES, "instruction_tokens_each_stage": INSTR,
                               "output_tokens_each_stage": OUTPUT, "ignore_eos": True,
                               "warmup_s": WARMUP, "observation_s": OBSERVATION,
                               "drain_s": cal["drain_s"], "deadline_factors": list(DEADLINE_FACTORS),
                               "local_scope": LOCAL_SCOPE, "baseline_A0_s": cal["baseline_A0_s"],
                               "deadline_seconds": cal["deadline_seconds"], "hold_factors": list(HOLD_FACTORS),
                               "hold_target_seconds": cal["hold_target_seconds"]},
                  "interpretation": {"open_loop_primary": True, "closed_loop_independent_prediction": False,
                                     "author_M1_M2_pass_not_assigned": True, "underpowered_forecast": underpowered}}
        if freeze_file.exists():
            old_freeze = json.loads(freeze_file.read_text(encoding="utf-8"))
            comparable_old = {k: v for k, v in old_freeze.items() if k != "created_utc"}
            comparable_new = {k: v for k, v in freeze.items() if k != "created_utc"}
            if comparable_old != comparable_new:
                raise RuntimeError("Formal freeze differs on resume; never overwrite it.")
        else:
            write_json(freeze_file, freeze)
        state.update(status="MAIN_INTERVAL_ESTIMATION", formal_freeze_sha256=sha(freeze_file),
                     selected_reps=n, underpowered_forecast=underpowered,
                     estimated_main_wall_s=estimated)
        write_json(state_path, state)
        print(legacy.now_iso(), "MAIN FORECAST", power_record, flush=True)
        main_args = copy.copy(args)
        main_args.phase, main_args.calibration = "main", str(cal_file)
        main_args.freeze, main_args.freeze_sha256 = str(freeze_file), sha(freeze_file)
        await experiment(main_args, legacy, main_dir)
        state.update(status="COMPLETE", finished_utc=legacy.now_iso(),
                     scientific_gate="No author M1/M2 pass assigned; intervals only")
        write_json(state_path, state)
    except BaseException as e:
        state.update(status="FAILED_GATE_OR_EXECUTION", error=repr(e), stopped_utc=legacy.now_iso())
        write_json(state_path, state)
        raise


def selftest():
    import numpy as np
    r = Reservation(1000, 0.05, 16)
    a, b = r.claim(480), r.claim(480)
    assert a == 480 and b is None and r.used == 480
    r.release(a)
    assert r.used == 0
    point = {"index": 3, "rate_per_s": 0.2, "hold_factors": [1, 3, 6]}
    x = pregen_open(point, 0, 10, np)
    assert x == pregen_open(point, 0, 10, np)
    assert all(0 < a["arrival_s"] < 150 for a in x)
    assert all(a["target_hold_s"] in (10, 30, 60) for a in x)
    # Rejection is a denominator miss, never an omitted completion-only observation.
    cohort = [{"status": "completed", "latency_s": 4}, {"status": "blocked"},
              {"status": "unfinished_at_drain"}]
    assert sum(a.get("latency_s", math.inf) <= 5 for a in cohort) / len(cohort) == 1 / 3
    print("PASS reservation boundary, reproducible exogenous arrivals/holds, cohort misses")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("calibration", "pilot", "main", "pipeline", "selftest"))
    parser.add_argument("--legacy", default=str(Path(__file__).with_name("kvbench.py")))
    parser.add_argument("--model-path")
    parser.add_argument("--gpus", default="0")
    parser.add_argument("--port", type=int, default=8231)
    parser.add_argument("--out")
    parser.add_argument("--calibration")
    parser.add_argument("--calibration-reps", type=int, default=3)
    parser.add_argument("--pilot-reps", type=int, default=3)
    parser.add_argument("--uncap", type=int, default=2048)
    parser.add_argument("--ready-timeout", type=int, default=1800)
    parser.add_argument("--freeze")
    parser.add_argument("--freeze-sha256")
    args = parser.parse_args()
    if args.phase == "selftest":
        selftest()
        return
    if not args.model_path or not args.out:
        parser.error("--model-path and --out are required for GPU phases")
    if args.phase in ("pilot", "main") and not args.calibration:
        parser.error("--calibration is required for pilot/main")
    if not 2 <= args.pilot_reps <= 99 or args.calibration_reps < 3:
        parser.error("Calibration needs >=3 repetitions; pilot needs 2..99.")
    if not (Path(args.model_path) / "config.json").is_file():
        raise RuntimeError("Local model not ready; runner never downloads models.")
    legacy = load_legacy(args.legacy)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, terminate)
    if args.phase == "pipeline":
        asyncio.run(pipeline(args, legacy, out))
    else:
        asyncio.run(calibration(args, legacy, out) if args.phase == "calibration" else experiment(args, legacy, out))


if __name__ == "__main__":
    main()
