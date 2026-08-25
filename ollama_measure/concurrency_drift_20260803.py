"""Concurrent-serving drift of the sequential service calibration (2026-08-03).

Frozen design (before run):
- Server: side instance at 127.0.0.1:11435 with OLLAMA_NUM_PARALLEL=8
  (continuous batching enabled), same model files as the calibration server.
- Models: qwen3:1.7b and qwen3:4b (two of the five calibrated tiers; 4b is
  the replay-testbed model).
- Concurrency levels n in {1, 2, 4, 8}; 3 repetitions per level; within a
  repetition, n identical-workload calls fire simultaneously.
- Workload per call: fixed prompt of about 512 tokens with a unique nonce
  (defeats prefix cache), num_predict 128, temperature 0, thinking off.
- Logged per call: prompt_eval_count, eval_count, prompt_eval_duration,
  eval_duration, total wall latency.
- Endpoints: per-call wall latency versus n; aggregate output tokens per
  second versus n; drift ratio = mean per-call wall latency at n divided by
  the sequential-fit prediction from per_model.json
  (alpha_ns*prompt + rho_ns*output + overheads).

The sequential calibration measured one synchronous single-stream
configuration; this experiment measures how far that fit drifts when the
same hardware serves n concurrent streams with batching on.

Output: concurrency_drift_20260803.json
"""
import json
import os
import threading
import time
import urllib.request

HOST = "http://127.0.0.1:11435"
MODELS = ["qwen3:1.7b", "qwen3:4b"]
LEVELS = [1, 2, 4, 8]
REPS = 3
NUM_PREDICT = 128
HERE = os.path.dirname(os.path.abspath(__file__))

BASE_PROMPT = ("Explain, in careful detail, how a packet travels from a "
               "user device through a radio access network to an edge "
               "server and back, covering scheduling, retransmission, "
               "transport, and queueing at every hop. ") * 8  # ~512 tokens


def gen(model, nonce):
    req = urllib.request.Request(
        HOST + "/api/generate",
        data=json.dumps({
            "model": model, "prompt": f"[{nonce}] " + BASE_PROMPT,
            "stream": False, "think": False,
            "options": {"temperature": 0, "num_predict": NUM_PREDICT},
        }).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.loads(urllib.request.urlopen(req, timeout=900).read())
    wall = time.time() - t0
    return {"wall_s": wall,
            "prompt_tokens": d.get("prompt_eval_count", 0),
            "output_tokens": d.get("eval_count", 0),
            "prompt_ns": d.get("prompt_eval_duration", 0),
            "eval_ns": d.get("eval_duration", 0)}


def main():
    fits = json.load(open(os.path.join(HERE, "per_model.json"),
                          encoding="utf-8"))
    out = {"host": HOST, "levels": LEVELS, "reps": REPS,
           "num_predict": NUM_PREDICT, "models": {}}
    for model in MODELS:
        gen(model, "warmup")  # load model, excluded from stats
        f = fits[model]
        levels = {}
        for n in LEVELS:
            calls = []
            for rep in range(REPS):
                res, ths = [], []
                def one(i, rep=rep):
                    res.append(gen(model, f"{n}-{rep}-{i}"))
                ths = [threading.Thread(target=one, args=(i,))
                       for i in range(n)]
                t0 = time.time()
                for t in ths:
                    t.start()
                for t in ths:
                    t.join()
                wall_batch = time.time() - t0
                calls.append({"batch_wall_s": wall_batch, "calls": res})
            per_call = [c["wall_s"] for b in calls for c in b["calls"]]
            toks = [c["output_tokens"] for b in calls for c in b["calls"]]
            ptoks = [c["prompt_tokens"] for b in calls for c in b["calls"]]
            agg_tps = sum(toks) / sum(b["batch_wall_s"] for b in calls) * REPS \
                / 1  # tokens per second per batch, averaged via totals
            # sequential-fit prediction for the mean workload
            mp, mo = sum(ptoks) / len(ptoks), sum(toks) / len(toks)
            pred_s = (f["alpha_ns_per_tok"] * mp + f["rho_ns_per_tok"] * mo
                      + f["prefill_overhead_ns"] + f["decode_overhead_ns"]) / 1e9
            mean_wall = sum(per_call) / len(per_call)
            levels[n] = {
                "mean_wall_s": round(mean_wall, 3),
                "pred_sequential_s": round(pred_s, 3),
                "drift_ratio": round(mean_wall / pred_s, 3),
                "agg_tokens_per_s": round(
                    sum(toks) / sum(b["batch_wall_s"] for b in calls), 1),
                "mean_prompt_tokens": round(mp, 1),
                "mean_output_tokens": round(mo, 1),
            }
            print(model, "n=", n, levels[n])
        out["models"][model] = levels
    with open(os.path.join(HERE, "concurrency_drift_20260803.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print("saved")


if __name__ == "__main__":
    main()
