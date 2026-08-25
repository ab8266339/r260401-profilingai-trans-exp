#!/usr/bin/env python3
"""
Real multi-stage pipeline cascade (Theorem 4) across model tiers.
Drives a depth d=1..D software-dev-style pipeline; each stage is one LLM call.
  - sigma=1.0  (full retention)   : stage k prompt carries the FULL transcript
  - sigma<1    (summarization)    : transcript truncated to a sigma-fraction window
Measures per-stage processed prompt tokens and the cumulative amplification
(stage-D cumulative / stage-1), reproducing the magazine 19x (full) vs 3.2x (summ).
Output: cascade_sweep.json
"""
import json, os, urllib.request, time

HOST = "http://127.0.0.1:11434"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cascade_sweep.json")
TIERS = ["qwen3:0.6b", "qwen3:4b"]      # representative low/mid tiers
DEPTH = 10
TASK = "You are stage {k} of a software pipeline. Given the context, produce the next design step in 2-3 sentences."


def gen(model, prompt, n=80):
    req = urllib.request.Request(HOST + "/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                         "options": {"temperature": 0, "num_predict": n, "num_ctx": 8192}}).encode(),
        headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=300).read())
    return d.get("response", ""), d.get("prompt_eval_count", 0), d.get("eval_count", 0)


def run_pipeline(model, sigma):
    history = []                      # list of (text) per stage
    per_stage_in, cumulative = [], 0
    for k in range(1, DEPTH + 1):
        ctx = "\n".join(history)
        if sigma < 1.0 and history:   # summarization: keep only a sigma-fraction tail window
            keep = max(1, int(len(history) * sigma))
            ctx = "\n".join(history[-keep:])
        prompt = TASK.format(k=k) + "\n\nContext so far:\n" + ctx
        out, tin, tout = gen(model, prompt)
        per_stage_in.append(tin)
        cumulative += tin
        history.append(out)
    amp = per_stage_in[-1] / per_stage_in[0] if per_stage_in[0] else None
    return {"per_stage_in": per_stage_in, "cumulative_in": cumulative,
            "amplification_last_over_first": round(amp, 2) if amp else None}


def main():
    out = {}
    for model in TIERS:
        out[model] = {}
        for sigma in [1.0, 0.3]:
            print(f"=== {model} sigma={sigma} ===", flush=True)
            r = run_pipeline(model, sigma)
            out[model][f"sigma_{sigma}"] = r
            print(f"  per-stage in: {r['per_stage_in']}  cum={r['cumulative_in']}  amp={r['amplification_last_over_first']}", flush=True)
    json.dump(out, open(OUT, "w"), indent=2)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
