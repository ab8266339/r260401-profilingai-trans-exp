#!/usr/bin/env python3
"""Summarize R260401 4卡机 vLLM cells -> CSV/JSON. Pure CPU; safe to rerun.

Outputs (in <results>/summary/):
  stage_records.csv   one row per (cell, session, stage): raw per-request timings/tokens
  depth_pairs.csv     per model/config/setting/depth/rep: arm means (repetition = unit)
  depth_contrasts.csv paired pinned-vs-replay (and restore) contrasts with t 95% CI over reps
  kv_bytes.json       theoretical and allocator KV bytes/token, per-session state bytes,
                      application-context (UTF-8) bytes, holding times
  exponents.csv       log-log slope of cumulative prompt-phase time vs depth (d=2..32)
Depth-d pipeline metrics are prefix sums of the first d stages of the same pipeline
(stages run strictly in sequence, so this equals a depth-d pipeline). See PLAN.md.
"""
import csv, glob, json, math, os, sys
from collections import defaultdict

import numpy as np
from scipy import stats

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/exp/r260401/results")
OUT = os.path.join(ROOT, "summary")
DEPTHS = (1, 2, 4, 8, 16, 32)


def tci(x):
    x = np.asarray([v for v in x if v is not None and not math.isnan(v)], float)
    n = len(x)
    if n == 0:
        return (float("nan"),) * 3 + (0,)
    m = x.mean()
    if n < 2:
        return (m, float("nan"), float("nan"), n)
    h = stats.t.ppf(0.975, n - 1) * x.std(ddof=1) / math.sqrt(n)
    return (m, m - h, m + h, n)


def main():
    os.makedirs(OUT, exist_ok=True)
    infos = {}
    for f in glob.glob(os.path.join(ROOT, "*", "*", "server_info.json")):
        cfg = os.path.relpath(os.path.dirname(f), ROOT)
        infos[cfg] = json.load(open(f))
    rows, per = [], defaultdict(lambda: defaultdict(dict))
    for f in sorted(glob.glob(os.path.join(ROOT, "*", "*", "cells", "*.json"))):
        c = json.load(open(f))
        cfg = os.path.relpath(os.path.dirname(os.path.dirname(f)), ROOT)
        key = (cfg, c["tau0"], c["conc"], c["stages"])
        sess_depth = defaultdict(list)  # depth -> list of per-session dicts
        for s in c["sessions"]:
            st = s["stages"]
            for r in st:
                rows.append({"cfg": cfg, "cell": c["cell"], "tau0": c["tau0"], "conc": c["conc"], "arm": c["arm"],
                             "rep": c["rep"], "arm_position": c["arm_position"], "sess": s["sess"], "stage": r["stage"],
                             "sent_prompt_len": r["sent_prompt_len"], "prompt_tokens": r["prompt_tokens"],
                             "cached_tokens": r["cached_tokens"], "completion_tokens": r["completion_tokens"],
                             "n_out_ids": r["n_out_ids"], "ttft_s": r["ttft"], "e2e_s": r["e2e"],
                             "decode_s": (r["e2e"] - r["ttft"]) if r["ttft"] is not None else None,
                             "restore_reset_tries": r.get("restore_reset_tries"), "out_sha1": r["out_sha1"]})
            for d in DEPTHS:
                if d > len(st):
                    continue
                sub = st[:d]
                sess_depth[d].append({
                    "wall": sub[-1]["t_end"] - sub[0]["t_start"],
                    "sum_e2e": sum(r["e2e"] for r in sub),
                    "sum_ttft": sum(r["ttft"] for r in sub),
                    "sum_decode": sum(r["e2e"] - r["ttft"] for r in sub),
                    "prefill_tokens": sum(r["prompt_tokens"] - (r["cached_tokens"] or 0) for r in sub),
                    "cached_frac_later": (np.mean([(r["cached_tokens"] or 0) / r["prompt_tokens"] for r in sub[1:]])
                                          if d > 1 else None),
                    "bad_len": sum(r["n_out_ids"] != c["out_tok"] for r in sub),
                })
        for d, lst in sess_depth.items():
            agg = {k: float(np.mean([x[k] for x in lst if x[k] is not None])) if any(x[k] is not None for x in lst) else None
                   for k in lst[0]}
            agg["bad_len"] = int(sum(x["bad_len"] for x in lst))
            per[key + (d,)][c["rep"]][c["arm"]] = agg
    with open(os.path.join(OUT, "stage_records.csv"), "w", newline="") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    pair_rows, con_rows = [], []
    for key in sorted(per):
        cfg, tau0, conc, stages, d = key
        reps = per[key]
        for rep in sorted(reps):
            for arm, agg in reps[rep].items():
                pair_rows.append({"cfg": cfg, "tau0": tau0, "conc": conc, "depth": d, "rep": rep, "arm": arm, **agg})
        for trt in ("pinned", "restore"):
            for metric in ("wall", "sum_e2e", "sum_ttft", "sum_decode"):
                sav, diff, a_l, b_l = [], [], [], []
                for rep, arms in reps.items():
                    if trt in arms and "replay" in arms:
                        a, b = arms[trt][metric], arms["replay"][metric]
                        a_l.append(a); b_l.append(b)
                        diff.append(a - b)
                        sav.append(1 - a / b if b else float("nan"))
                if not diff:
                    continue
                m_s, lo_s, hi_s, n = tci(sav)
                m_d, lo_d, hi_d, _ = tci(diff)
                wil = stats.wilcoxon(diff).pvalue if n >= 6 and any(diff) else float("nan")
                con_rows.append({"cfg": cfg, "tau0": tau0, "conc": conc, "depth": d, "treatment": trt, "metric": metric,
                                 "n_reps": n, "treat_mean_s": float(np.mean(a_l)), "replay_mean_s": float(np.mean(b_l)),
                                 "saving_mean": m_s, "saving_lo95": lo_s, "saving_hi95": hi_s,
                                 "diff_mean_s": m_d, "diff_lo95_s": lo_d, "diff_hi95_s": hi_d, "wilcoxon_p": wil})
    for name, rr in (("depth_pairs.csv", pair_rows), ("depth_contrasts.csv", con_rows)):
        with open(os.path.join(OUT, name), "w", newline="") as fh:
            if rr:
                w = csv.DictWriter(fh, fieldnames=list(rr[0]))
                w.writeheader()
                w.writerows(rr)
    # growth exponents of cumulative prompt-phase time (sum_ttft) over d=2..32, per rep then CI over reps
    exp_rows = []
    for (cfg, tau0, conc, stages) in sorted({k[:4] for k in per}):
        if stages < 32:
            continue
        for arm in ("pinned", "replay", "restore"):
            slopes = []
            reps = set().union(*[per[(cfg, tau0, conc, stages, d)].keys() for d in DEPTHS if (cfg, tau0, conc, stages, d) in per])
            for rep in reps:
                xs, ys = [], []
                for d in (2, 4, 8, 16, 32):
                    v = per.get((cfg, tau0, conc, stages, d), {}).get(rep, {}).get(arm)
                    if v:
                        xs.append(math.log(d)); ys.append(math.log(v["sum_ttft"]))
                if len(xs) == 5:
                    slopes.append(np.polyfit(xs, ys, 1)[0])
            if slopes:
                m, lo, hi, n = tci(slopes)
                exp_rows.append({"cfg": cfg, "tau0": tau0, "conc": conc, "arm": arm, "n_reps": n,
                                 "slope_mean": m, "slope_lo95": lo, "slope_hi95": hi})
    with open(os.path.join(OUT, "exponents.csv"), "w", newline="") as fh:
        if exp_rows:
            w = csv.DictWriter(fh, fieldnames=list(exp_rows[0]))
            w.writeheader()
            w.writerows(exp_rows)
    # KV bytes
    kv = {}
    for cfg, info in infos.items():
        sl = info.get("server_log", {})
        th = info.get("theoretical_kv", {})
        tp = info.get("tp", 1)
        alloc = (sl["kv_cache_gib"] * 2**30 * tp / sl["kv_cache_tokens"]) if sl.get("kv_cache_tokens") and sl.get("kv_cache_gib") else None
        kv[cfg] = {"machine": info.get("machine"), "vllm": info.get("vllm"), "torch": info.get("torch"),
                   "kv_dtype": info.get("kv_dtype"), "tp": tp, "theoretical_bytes_per_token": th.get("bytes_per_token"),
                   "theoretical_MB_per_token": th.get("bytes_per_token", 0) / 1e6, "arch": th,
                   "kv_cache_tokens": sl.get("kv_cache_tokens"), "kv_cache_gib_per_gpu": sl.get("kv_cache_gib"),
                   "alloc_bytes_per_token": alloc,
                   "alloc_vs_theory": (alloc / th["bytes_per_token"]) if alloc and th.get("bytes_per_token") else None,
                   "api_probe": info.get("api_probe"), "offload_probe": info.get("offload_probe")}
    # per-session state bytes at each checkpoint depth (pinned arm, theoretical bytes/token x context tokens),
    # application-context UTF-8 bytes, and holding time (pinned session wall time)
    ctxb = defaultdict(lambda: defaultdict(list))
    for f in glob.glob(os.path.join(ROOT, "*", "*", "cells", "*_pinned_r*.json")):
        c = json.load(open(f))
        cfg = os.path.relpath(os.path.dirname(os.path.dirname(f)), ROOT)
        for s in c["sessions"]:
            for d, v in s["ctx_bytes"].items():
                d = int(d)
                ctxb[(cfg, c["tau0"], c["conc"])][d].append(
                    (v["tokens"], v["utf8_bytes"], s["stages"][d - 1]["t_end"] - s["stages"][0]["t_start"]))
    sess_state = []
    for (cfg, tau0, conc), dd in sorted(ctxb.items()):
        bpt = kv.get(cfg, {}).get("theoretical_bytes_per_token")
        for d, lst in sorted(dd.items()):
            tok = np.mean([x[0] for x in lst]); ub = np.mean([x[1] for x in lst]); hold = np.mean([x[2] for x in lst])
            sess_state.append({"cfg": cfg, "tau0": tau0, "conc": conc, "depth": d, "n_sessions": len(lst),
                               "context_tokens": tok, "kv_bytes_theory": tok * bpt if bpt else None,
                               "app_context_utf8_bytes": ub, "app_bytes_per_token": ub / tok,
                               "kv_to_app_ratio": (tok * bpt / ub) if bpt else None, "holding_time_s": hold})
    json.dump({"kv_per_config": kv, "session_state": sess_state}, open(os.path.join(OUT, "kv_bytes.json"), "w"), indent=1)
    print(f"cells rows={len(rows)} pairs={len(pair_rows)} contrasts={len(con_rows)} configs={len(infos)} -> {OUT}")


if __name__ == "__main__":
    main()
