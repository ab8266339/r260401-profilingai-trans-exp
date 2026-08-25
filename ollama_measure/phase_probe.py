#!/usr/bin/env python3
"""
Phase-structured probe of multi-spec inference -- mining quantities BEYOND the
4D demand vector that bear on communication-protocol functional splitting.

Per Qwen3 tier we separate the two inference PHASES (prefill vs decode) and measure:
  - per-phase power draw (W)        -> energy/token asymmetry (UE-battery vs MEC offload)
  - TTFT (warm) vs ITL distribution -> two decoupled latency planes (prefill-bound vs decode-bound)
  - ITL jitter (std, p99)           -> real source of cascade latency variance (Thm 4)
  - cold-start load_duration (s)    -> model-residency / spec-swap cost (placement knob)

Honest scope: app-layer + nvidia-smi power sampling on one node; wall-clock not FLOPs.
Output: phase_probe.json
"""
import json, os, time, threading, subprocess, urllib.request, statistics as st
import numpy as np

HOST = "http://127.0.0.1:11434"
MODELS = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b", "qwen3:14b"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase_probe.json")
LONG_PROMPT = ("In modern distributed systems the agent observes its environment, "
               "reasons over available tools, and emits a structured action. ") * 60  # ~1.5k tok


def post(payload, stream=False, timeout=300):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(HOST + "/api/generate",
                data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            last = e; time.sleep(2.0 * (attempt + 1))   # transient 500 after model swap
    raise last


def power_w():
    try:
        return float(subprocess.check_output(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            text=True).split()[0])
    except Exception:
        return None


def sampled_call(model, prompt, num_predict):
    """Run a non-stream call while sampling GPU power; return timing + mean power."""
    samples, stop = [], threading.Event()
    def sampler():
        while not stop.is_set():
            p = power_w()
            if p: samples.append(p)
            time.sleep(0.03)
    th = threading.Thread(target=sampler); th.start()
    r = post({"model": model, "prompt": prompt, "stream": False, "think": False,
              "options": {"temperature": 0, "num_predict": num_predict, "num_ctx": 4096}})
    d = json.loads(r.read())
    stop.set(); th.join()
    return d, (np.mean(samples) if samples else None), (np.max(samples) if samples else None)


def stream_itl(model, prompt, num_predict):
    """Streaming call -> TTFT (warm) and inter-token latency stats."""
    t0 = time.time(); first = None; last = t0; gaps = []
    r = post({"model": model, "prompt": prompt, "stream": True, "think": False,
              "options": {"temperature": 0, "num_predict": num_predict}}, stream=True)
    for line in r:
        o = json.loads(line); now = time.time()
        if o.get("response"):
            if first is None: first = now - t0
            else: gaps.append(now - last)
            last = now
        if o.get("done"): break
    return first, gaps


def main():
    out = {}
    for model in MODELS:
        print(f"\n=== {model} ===", flush=True)
        try:
            _probe_one(model, out)
        except Exception as e:
            print(f"  FAILED: {e}", flush=True)
    json.dump(out, open(OUT, "w"), indent=2)
    print("\nwrote", OUT)


def _probe_one(model, out):
        # 0) cold-start load cost: unload then call
        subprocess.run(["ollama", "stop", model], capture_output=True)
        time.sleep(1.0)
        d_cold, _, _ = sampled_call(model, "hello", 4)
        load_s = d_cold.get("load_duration", 0) / 1e9

        # 1) PREFILL-dominant: big prompt, 1 output token  -> prefill power & time
        d_pf, pf_pow, pf_pk = sampled_call(model, LONG_PROMPT, 1)
        pf_tok = d_pf.get("prompt_eval_count", 0)
        pf_s = d_pf.get("prompt_eval_duration", 0) / 1e9

        # 2) DECODE-dominant: tiny prompt, many output tokens -> decode power & time
        d_dc, dc_pow, dc_pk = sampled_call(model, "Write a long detailed essay:", 256)
        dc_tok = d_dc.get("eval_count", 0)
        dc_s = d_dc.get("eval_duration", 0) / 1e9

        # 3) streaming ITL (warm now)
        ttft, gaps = stream_itl(model, "Explain congestion control in depth:", 128)
        itl_mean = st.mean(gaps) if gaps else None
        itl_p99 = (sorted(gaps)[int(0.99*len(gaps))-1] if len(gaps) > 5 else (max(gaps) if gaps else None))
        itl_jit = st.pstdev(gaps) if len(gaps) > 1 else None

        rec = {
            "load_cold_s": round(load_s, 3),
            "prefill_power_W": round(pf_pow, 1) if pf_pow else None,
            "prefill_peak_W": round(pf_pk, 1) if pf_pk else None,
            "prefill_tok_per_s": round(pf_tok / pf_s, 1) if pf_s else None,
            "decode_power_W": round(dc_pow, 1) if dc_pow else None,
            "decode_peak_W": round(dc_pk, 1) if dc_pk else None,
            "decode_tok_per_s": round(dc_tok / dc_s, 1) if dc_s else None,
            "ttft_warm_s": round(ttft, 3) if ttft else None,
            "itl_mean_ms": round(itl_mean*1e3, 2) if itl_mean else None,
            "itl_p99_ms": round(itl_p99*1e3, 2) if itl_p99 else None,
            "itl_jitter_ms": round(itl_jit*1e3, 2) if itl_jit else None,
        }
        # energy per token (J/tok) = power(W) / throughput(tok/s)
        if rec["decode_power_W"] and rec["decode_tok_per_s"]:
            rec["decode_energy_J_per_tok"] = round(rec["decode_power_W"]/rec["decode_tok_per_s"], 3)
        if rec["prefill_power_W"] and rec["prefill_tok_per_s"]:
            rec["prefill_energy_J_per_tok"] = round(rec["prefill_power_W"]/rec["prefill_tok_per_s"], 5)
        out[model] = rec
        print("  " + json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
