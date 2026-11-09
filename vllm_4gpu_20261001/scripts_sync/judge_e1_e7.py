"""Apply the frozen interpretation rules E1-E7 of PLAN.md section 8 to the 4卡机 results.

Inputs: results_4gpu/summary/*.csv, kv_bytes.json (as produced by summarize.py on 4卡机 and
re-derived locally) and, for E5 preemption counts, the raw cells (an extracted copy of
results_4gpu/raw_cells.tar.gz given as argv[2]). Writes results_4gpu/judge_e1_e7.json.
Usage: python judge_e1_e7.py <results_4gpu dir> <extracted results dir with cells>
All numbers belong to 4卡机, vLLM 0.30.0; do not tabulate them with DESKTOP-BRJD313 Ollama data.
"""
import csv, glob, json, os, sys
from collections import defaultdict
import numpy as np
from scipy import stats

R = sys.argv[1]
RAW = sys.argv[2] if len(sys.argv) > 2 else None
S = os.path.join(R, "summary")
SIM_TIERS_MB = {"7B": 0.1, "33B": 0.5, "70B": 1.0}   # oran_mmas_sim.py MODEL_TIERS gamma (MB/token)
SIM_APP_BYTES_PER_TOKEN = 4.0                          # paper assumption for application context


def ti(x):
    x = np.asarray(x, float)
    m = float(x.mean())
    if len(x) < 2:
        return [m, float("nan"), float("nan")]
    h = float(stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)))
    return [m, m - h, m + h]


def rows(name):
    return list(csv.DictReader(open(os.path.join(S, name), newline="", encoding="utf-8")))


out = {}
kv = json.load(open(os.path.join(S, "kv_bytes.json"), encoding="utf-8"))

# E1 per-token KV bytes
e1 = {}
for cfg, k in sorted(kv["kv_per_config"].items()):
    e1[cfg] = {"kv_dtype": k["kv_dtype"], "theory_MB": k["theoretical_MB_per_token"],
               "alloc_MB": k["alloc_bytes_per_token"] / 1e6, "alloc_vs_theory": k["alloc_vs_theory"],
               "confirmed": 0.98 <= k["alloc_vs_theory"] <= 1.02}
bf16 = [v["alloc_MB"] for c, v in e1.items() if v["kv_dtype"] == "auto"]
lo, hi = min(bf16), max(bf16)
out["E1"] = {"per_config": e1, "bf16_range_MB": [lo, hi],
             "tiers": {t: {"MB": mb, "verdict": ("within measured range" if lo <= mb <= hi else
                                                  "overestimates state cost (conservative)" if mb > hi else
                                                  "underestimates")} for t, mb in SIM_TIERS_MB.items()}}

# E2 application context bytes
ss = kv["session_state"]
app = [s["app_bytes_per_token"] for s in ss]
ratio = [s["kv_to_app_ratio"] for s in ss]
out["E2"] = {"app_bytes_per_token_range": [min(app), max(app)], "app_bytes_per_token_mean": float(np.mean(app)),
             "paper_assumption": SIM_APP_BYTES_PER_TOKEN, "kv_to_app_ratio_range": [min(ratio), max(ratio)]}

# E3 depth scaling
pairs = rows("depth_pairs.csv")
cached = defaultdict(list)
for r in pairs:
    if r["arm"] == "pinned" and r["cached_frac_later"] not in ("", "nan"):
        cached[(r["cfg"], int(r["tau0"]), int(r["conc"]), int(r["depth"]))].append(float(r["cached_frac_later"]))
e3_tok = {f"{c}|t{t}|c{cc}|d{d}": float(np.mean(v)) for (c, t, cc, d), v in cached.items() if cc == 1}
exps = rows("exponents.csv")
e3_slope = {}
for r in exps:
    key = f"{r['cfg']}|t{r['tau0']}|c{r['conc']}|{r['arm']}"
    hi95 = float(r["slope_hi95"])
    e3_slope[key] = {"slope": [float(r["slope_mean"]), float(r["slope_lo95"]), hi95],
                     "verdict": (None if r["arm"] != "pinned" else
                                 "linear within d<=32" if hi95 <= 1.3 else "token work linear, time superlinear")}
by = defaultdict(dict)   # (cfg,tau0,conc,arm,depth) -> {rep: wall}
for r in pairs:
    by[(r["cfg"], int(r["tau0"]), int(r["conc"]), r["arm"], int(r["depth"]))][int(r["rep"])] = float(r["wall"])
e3_growth = {}
for (cfg, t, c, arm, d) in list(by):
    if arm != "pinned" or d != 32:
        continue
    def sav(dd):
        p, q = by[(cfg, t, c, "pinned", dd)], by[(cfg, t, c, "replay", dd)]
        return {k: 1 - p[k] / q[k] for k in p if k in q}
    s32, s2 = sav(32), sav(2)
    diff = [s32[k] - s2[k] for k in s32 if k in s2]
    iv = ti(diff)
    e3_growth[f"{cfg}|t{t}|c{c}"] = {"saving_d32_minus_d2": iv,
                                     "verdict": "saving grows with depth" if iv[1] > 0 else "unresolved"}
out["E3"] = {"cached_frac_later_conc1": e3_tok,
             "cached_frac_min_conc1": min(e3_tok.values()) if e3_tok else None,
             "token_rule": ("server prefilled only new tokens" if e3_tok and min(e3_tok.values()) >= 0.95
                            else "see per-cell fractions"),
             "slopes": e3_slope, "saving_growth": e3_growth}

# E4 Table VI direction on second stack (depth 8 wall saving), and concurrency amplification
con = rows("depth_contrasts.csv")
e4 = {}
for r in con:
    if r["treatment"] == "pinned" and r["metric"] == "wall" and int(r["depth"]) == 8:
        e4[f"{r['cfg']}|t{r['tau0']}|c{r['conc']}"] = {
            "saving": [float(r["saving_mean"]), float(r["saving_lo95"]), float(r["saving_hi95"])],
            "direction_reproduced": float(r["saving_lo95"]) > 0}
amp = {}
for (cfg, t, c, arm, d) in list(by):
    if arm == "pinned" and d == 8 and c == 4 and (cfg, t, 1, "pinned", 8) in by:
        def sav8(cc):
            p, q = by[(cfg, t, cc, "pinned", 8)], by[(cfg, t, cc, "replay", 8)]
            return {k: 1 - p[k] / q[k] for k in p if k in q}
        a4, a1 = sav8(4), sav8(1)
        iv = ti([a4[k] - a1[k] for k in a4 if k in a1])
        amp[f"{cfg}|t{t}"] = {"saving_c4_minus_c1": iv, "amplified": iv[1] > 0}
out["E4"] = {"depth8": e4, "concurrency": amp}

# E5 pinning under memory pressure (conc 16 and tight cells): cached fraction and preemptions
e5 = {}
for (cfg, t, c, d), v in cached.items():
    if c >= 4 and d == max(dd for (cf, tt, ccc, dd) in cached if (cf, tt, ccc) == (cfg, t, c)):
        e5[f"{cfg}|t{t}|c{c}|d{d}"] = {"cached_frac_later_mean": float(np.mean(v)), "cached_frac_later_min": float(np.min(v))}
if RAW:
    for f in glob.glob(os.path.join(RAW, "*", "*", "cells", "*.json")):
        if f.endswith(".done"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        if d["arm"] != "pinned" or d["conc"] < 4 or not d.get("poll"):
            continue
        cfg = os.path.relpath(f, RAW).replace(chr(92), "/").split("/cells/")[0]
        key = f"{cfg}|t{d['tau0']}|c{d['conc']}|d{d['stages']}"
        pre = d["poll"][-1][7] - d["poll"][0][7]
        e5.setdefault(key, {}).setdefault("preemptions_per_rep", []).append(pre)
for k, v in e5.items():
    p = v.get("preemptions_per_rep", [])
    v["preemptions_total"] = float(sum(p)) if p else None
    v["verdict"] = ("automatic prefix caching does not hold session state under pressure"
                    if (v.get("cached_frac_later_min", 1) < 0.95 or (p and sum(p) > 0)) else "held")
out["E5"] = e5

# E6 host restore
e6 = {}
for r in con:
    if r["metric"] == "sum_ttft" and r["treatment"] in ("restore", "pinned") and "offload" in r["cfg"]:
        e6.setdefault(f"{r['cfg']}|t{r['tau0']}|c{r['conc']}|d{r['depth']}", {})[r["treatment"]] = {
            "treat_s": float(r["treat_mean_s"]), "replay_s": float(r["replay_mean_s"]),
            "saving": [float(r["saving_mean"]), float(r["saving_lo95"]), float(r["saving_hi95"])]}
for k, v in e6.items():
    if "restore" in v and "pinned" in v:
        rs, ps, rp = v["restore"]["treat_s"], v["pinned"]["treat_s"], v["restore"]["replay_s"]
        v["strictly_between"] = ps < rs < rp
        v["verdict"] = ("host-resident state keeps an accelerator benefit"
                        if v["strictly_between"] and v["restore"]["saving"][1] > 0 else "not met")
out["E6"] = e6

# E7 holding time
out["E7"] = {f"{s['cfg']}|t{s['tau0']}|c{s['conc']}|d{s['depth']}": s["holding_time_s"] for s in ss}

json.dump(out, open(os.path.join(R, "judge_e1_e7.json"), "w", encoding="utf-8"), indent=1)
print("written", os.path.join(R, "judge_e1_e7.json"))
