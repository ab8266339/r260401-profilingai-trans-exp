#!/usr/bin/env python3
"""R260401 unified serving measurements on 4卡机 (vLLM 0.30.0), 2026-10-05.

User 10-05: replace every serving number measured on DESKTOP-BRJD313 (Ollama) with one serving stack, vLLM on
4卡机. Protocol: PROTOCOL.md next to this file, written before any run. One job = one model on one GPU:

  calib      4 input sizes x 3 output sizes x 2 repeats = 24 synchronous single-stream calls per model. Unique
             leading prefix per call, temperature 0, ignore_eos, streaming; first-token time is the prefill time and
             last-minus-first token time is the decode time. Affine fits give the prefill and decode slopes.
  drift      (0.6B, 4B, 8B) n in {1, 2, 4, 8} simultaneous calls of one workload (about 512 prompt tokens, unique
             nonce, 128 output tokens, temperature 0, ignore_eos), 3 repetitions per n. Same-server ratios only.
  pipelines  (4B) 20 five-stage paths, 4 topics x 5 paths, temperature 0.7 with per-stage seed, at most 256 output
             tokens, unique nonce + full accumulated context + stage instruction per call, thinking off.

Writes <out>/<model>.json and <out>/server_<model>.log.
"""
import argparse
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np

VLLM_BIN = os.path.expanduser("~/venvs/vllm/bin/vllm")
MODEL_ROOT = "/home/host/models/Qwen"
FILLER = "The agent observes the environment, reasons over its tools, and emits an action. "
INPUT_STEPS = [40, 160, 640, 1600]
OUTPUT_STEPS = [16, 64, 256]
REPEAT = 2
DRIFT_LEVELS = [1, 2, 4, 8]
DRIFT_REPS = 3
DRIFT_OUT = 128
DRIFT_PROMPT = ("Explain, in careful detail, how a packet travels from a user device through a radio access network "
                "to an edge server and back, covering scheduling, retransmission, transport, and queueing at every "
                "hop. ") * 8
TOPICS = [
    "a rooftop delivery drone route through a dense city block",
    "an energy-saving plan for a five-site radio access cluster",
    "a warehouse robot picking schedule for a holiday sale day",
    "a live-event network slice for a stadium concert evening",
]
STAGES = [
    "Draft a concise plan for {topic}. List the key steps.",
    "Write the detailed operational draft for the plan above.",
    "Critique the draft above. List concrete weaknesses.",
    "Revise the draft to fix the weaknesses listed above.",
    "Summarize the final plan above in one short paragraph.",
]
DRIFT_MODELS = {"Qwen3-0.6B", "Qwen3-4B", "Qwen3-8B"}
PIPELINE_MODELS = {"Qwen3-4B"}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def launch(model, port, log):
    cmd = [VLLM_BIN, "serve", os.path.join(MODEL_ROOT, model), "--served-model-name", "m", "--host", "127.0.0.1",
           "--port", str(port), "--max-model-len", "8192", "--gpu-memory-utilization", "0.85", "--seed", "0"]
    # Same environment as the frozen kvbench.launch_server on 4卡机: no system CUDA toolkit, so FlashInfer's sampler
    # JIT is off and other JIT uses the pip CUDA 13 toolkit inside the venv.
    cu = os.path.join(os.path.dirname(os.path.dirname(VLLM_BIN)), "lib", "python3.12", "site-packages", "nvidia", "cu13")
    env = dict(os.environ, VLLM_SERVER_DEV_MODE="0", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               VLLM_NO_USAGE_STATS="1", DO_NOT_TRACK="1", VLLM_USE_FLASHINFER_SAMPLER="0", CUDA_HOME=cu,
               PATH=os.path.join(cu, "bin") + ":" + os.environ.get("PATH", ""))
    fh = open(log, "a")
    fh.write("==== %s %s\n" % (now(), " ".join(cmd)))
    fh.flush()
    return subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, start_new_session=True), cmd


def wait_ready(base, proc, timeout=1200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError("vLLM exited with %s before it was ready" % proc.returncode)
        try:
            with urllib.request.urlopen(base + "/v1/models", timeout=5) as r:
                if r.status == 200:
                    return time.time() - t0
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("vLLM not ready after %d s" % timeout)


def stop(proc):
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=60)
        except Exception:  # noqa: BLE001
            os.killpg(proc.pid, signal.SIGKILL)


def stream_call(base, path, body, timeout=900):
    """POST with stream=True. Returns wall, first-token and last-token times (s from send), usage, text."""
    body = dict(body, stream=True, stream_options={"include_usage": True})
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    first = last = None
    usage, parts = None, []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            d = json.loads(data)
            if d.get("usage"):
                usage = d["usage"]
            for ch in d.get("choices") or []:
                piece = ch.get("text")
                if piece is None:
                    piece = (ch.get("delta") or {}).get("content")
                if piece:
                    t = time.perf_counter() - t0
                    first = t if first is None else first
                    last = t
                    parts.append(piece)
    wall = time.perf_counter() - t0
    if usage is None or first is None:
        raise RuntimeError("stream ended without usage or tokens")
    return {"wall_s": wall, "first_s": first, "decode_s": last - first, "prompt_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"], "text": "".join(parts)}


def affine(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    slope, icpt = np.polyfit(x, y, 1)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(((y - (slope * x + icpt)) ** 2).sum()) / sst if sst > 0 else None
    return float(slope), float(icpt), r2


def calib(base, model):
    rows, cid = [], 0
    for it in INPUT_STEPS:
        text = (FILLER * max(1, it // 12))[:it * 6]
        for ot in OUTPUT_STEPS:
            for _ in range(REPEAT):
                cid += 1
                prompt = "[request %d-%s] " % (cid, model) + text + \
                    "\n\nContinue the passage above with a long, detailed multi-paragraph elaboration:"
                m = stream_call(base, "/v1/completions", {"model": "m", "prompt": prompt, "max_tokens": ot,
                                                          "temperature": 0, "ignore_eos": True})
                m.pop("text")
                rows.append(dict(m, target_in=it, target_out=ot, call=cid))
                print(now(), "calib", model, cid, m["prompt_tokens"], m["output_tokens"], round(m["first_s"], 4),
                      round(m["decode_s"], 4), flush=True)
    a, a0, ar2 = affine([r["prompt_tokens"] for r in rows], [r["first_s"] for r in rows])
    rho, r0, rr2 = affine([r["output_tokens"] for r in rows], [r["decode_s"] for r in rows])
    return {"calls": rows, "prefill_s_per_token": a, "prefill_intercept_s": a0, "prefill_r2": ar2,
            "decode_s_per_token": rho, "decode_intercept_s": r0, "decode_r2": rr2,
            "decode_over_prefill": rho / a if a else None}


def drift(base, model):
    out = {}
    for n in DRIFT_LEVELS:
        reps = []
        for rep in range(DRIFT_REPS):
            res, errs = [None] * n, []

            def one(i):
                try:
                    res[i] = stream_call(base, "/v1/completions", {
                        "model": "m", "prompt": "[%d-%d-%d] " % (n, rep, i) + DRIFT_PROMPT, "max_tokens": DRIFT_OUT,
                        "temperature": 0, "ignore_eos": True})
                    res[i].pop("text")
                except Exception as e:  # noqa: BLE001
                    errs.append(repr(e))
            ths = [threading.Thread(target=one, args=(i,)) for i in range(n)]
            t0 = time.perf_counter()
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            if errs:
                raise RuntimeError("drift call failed: %s" % errs[0])
            reps.append({"batch_wall_s": time.perf_counter() - t0, "calls": res})
        per_call = [c["wall_s"] for b in reps for c in b["calls"]]
        toks = sum(c["output_tokens"] for b in reps for c in b["calls"])
        out[str(n)] = {"reps": reps, "mean_call_wall_s": statistics.mean(per_call),
                       "agg_output_tokens_per_s": toks / sum(b["batch_wall_s"] for b in reps)}
        print(now(), "drift", model, n, round(out[str(n)]["mean_call_wall_s"], 3),
              round(out[str(n)]["agg_output_tokens_per_s"], 1), flush=True)
    one_ = out["1"]
    for n in DRIFT_LEVELS:
        out[str(n)]["call_wall_ratio_vs_n1"] = out[str(n)]["mean_call_wall_s"] / one_["mean_call_wall_s"]
        out[str(n)]["throughput_change_vs_n1"] = out[str(n)]["agg_output_tokens_per_s"] / one_["agg_output_tokens_per_s"] - 1
    return out


def pipelines(base, model):
    paths, idx = [], 0
    for topic in TOPICS:
        for rep in range(5):
            idx += 1
            nonce = "[%04d%04d] " % (idx, rep)
            context, stages = "", []
            for k, tmpl in enumerate(STAGES):
                instr = tmpl.format(topic=topic)
                prompt = nonce + context + "\n\nInstruction: " + instr
                m = stream_call(base, "/v1/chat/completions", {
                    "model": "m", "messages": [{"role": "user", "content": prompt}], "max_tokens": 256,
                    "temperature": 0.7, "seed": idx * 100 + k, "chat_template_kwargs": {"enable_thinking": False}})
                context = context + "\n\nInstruction: " + instr + "\n" + m["text"]
                stages.append({"prompt_tokens": m["prompt_tokens"], "output_tokens": m["output_tokens"],
                               "first_s": m["first_s"], "wall_s": m["wall_s"]})
            paths.append({"topic": topic, "rep": rep, "stages": stages})
            print(now(), "pipeline", idx, "final prompt tokens", stages[-1]["prompt_tokens"], flush=True)
    return {"depth": len(STAGES), "max_tokens": 256, "paths": paths}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    gpu = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0]
    if not gpu:
        raise SystemExit("no GPU assigned (CUDA_VISIBLE_DEVICES empty)")
    port = a.port or 18300 + int(gpu)
    base = "http://127.0.0.1:%d" % port
    log = os.path.join(a.out, "server_%s.log" % a.model)
    res = {"model": a.model, "host": socket.gethostname(), "gpu": gpu, "started": now(), "stack": "vLLM 0.30.0",
           "script": os.path.abspath(__file__)}
    signal.signal(signal.SIGTERM, lambda s, f: (_ for _ in ()).throw(SystemExit(143)))
    proc = None
    try:
        proc, cmd = launch(a.model, port, log)
        res["serve_cmd"] = cmd
        res["ready_s"] = wait_ready(base, proc)
        stream_call(base, "/v1/completions", {"model": "m", "prompt": "warmup", "max_tokens": 4, "temperature": 0})
        res["calib"] = calib(base, a.model)
        if a.model in DRIFT_MODELS:
            res["drift"] = drift(base, a.model)
        if a.model in PIPELINE_MODELS:
            res["pipelines"] = pipelines(base, a.model)
        res["status"] = "COMPLETE"
    except BaseException as e:  # noqa: BLE001
        res["status"] = "FAILED"
        res["error"] = repr(e)
        raise
    finally:
        res["finished"] = now()
        stop(proc)
        with open(os.path.join(a.out, "%s.json" % a.model), "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)


if __name__ == "__main__":
    main()
