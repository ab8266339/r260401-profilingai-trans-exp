#!/usr/bin/env python3
"""
Coupling-matrix measurement on real Ollama models (Qwen3 dense tiers).

Grounds the R260401 theory on real workloads:
  - alpha(M) proxy : prefill time / prompt token       (prompt_eval_duration / prompt_eval_count)
  - rho(M)   proxy : decode  time / completion token   (eval_duration / eval_count)
  - gamma(M)       : KV-cache bytes / token            (from architecture, 2*L*n_kv*d_head*dtype)
  - 4D demand vector v=(b_UL, b_DL, g, c) per (model, prompt) -> covariance -> effective rank
    (Theorem 3 / Lemma comonotonicity on REAL data)

Honest scope: Ollama timings are wall-clock (ns), not FLOPs. We validate the STRUCTURE
(rank-2 affine, prefill/decode asymmetry, scaling with P, effective rank<=2), not absolute FLOPs.

Outputs (next to this script):
  samples.csv         one row per inference call
  per_model.json      per-tier alpha/rho/gamma + param count + scaling
  summary.json        pooled covariance eigenvalues / effective rank / fits
"""
import json, time, csv, os, subprocess, urllib.request, urllib.error
import numpy as np

HOST = "http://127.0.0.1:11434"
MODELS = ["qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b", "qwen3:14b"]
BETA = 4.0  # bytes/token (validated 4.14 in companion)
KV_DTYPE_BYTES = 2  # f16 KV cache (Ollama default)
OUTDIR = os.path.dirname(os.path.abspath(__file__))

# (approx input tokens via filler, requested output tokens). prompt_eval_count gives the REAL tau_in.
FILLER = ("The agent observes the environment, reasons over its tools, and emits an action. ")
INPUT_STEPS = [40, 160, 640, 1600]       # target prompt token sizes
OUTPUT_STEPS = [16, 64, 256]             # num_predict
REPEAT = 2                               # repeats per (in,out) cell for stability


def http_json(path, payload=None, timeout=600):
    url = HOST + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def nvidia_mem_used_mib():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True).strip().splitlines()
        return int(out[0])
    except Exception:
        return None


def model_arch(model):
    """Pull architecture from /api/show -> model_info; compute gamma and param count."""
    info = http_json("/api/show", {"model": model})
    mi = info.get("model_info", {})
    # keys are like 'qwen3.block_count', 'qwen3.attention.head_count_kv', etc.
    # exclude vision/sub-encoder keys so we only read the text decoder.
    def find(suffix):
        for k, v in mi.items():
            if ".vision." in k or k.startswith("vision."):
                continue
            if k.endswith(suffix) and v is not None:
                return v
        return None
    L      = find("block_count")
    n_head = find("attention.head_count")
    n_kv   = find("attention.head_count_kv")
    d_emb  = find("embedding_length")
    d_key  = find("attention.key_length")  # head_dim if present
    head_dim = d_key if d_key else (int(d_emb / n_head) if (d_emb and n_head) else None)
    kv_is_mha_fallback = False
    if n_kv is None and n_head is not None:
        n_kv = n_head           # no GQA field -> assume multi-head attention
        kv_is_mha_fallback = True
    gamma = None
    if L and n_kv and head_dim:
        # KV cache bytes/token = 2 (K and V) * layers * kv_heads * head_dim * dtype_bytes
        gamma = 2 * L * n_kv * head_dim * KV_DTYPE_BYTES
    # param count if exposed
    psize = None
    for k, v in mi.items():
        if k.endswith("parameter_count"):
            psize = v
    details = info.get("details", {})
    return {
        "layers": L, "n_head": n_head, "n_kv": n_kv, "kv_mha_fallback": kv_is_mha_fallback,
        "d_emb": d_emb, "head_dim": head_dim, "gamma_bytes_per_tok": gamma,
        "param_count": psize, "param_label": details.get("parameter_size"),
        "quant": details.get("quantization_level"),
    }


def one_call(model, prompt, num_predict):
    payload = {
        "model": model, "prompt": prompt, "stream": False, "think": False,
        "options": {"temperature": 0, "num_predict": num_predict, "num_ctx": 4096},
    }
    r = http_json("/api/generate", payload)
    return {
        "tau_in":  r.get("prompt_eval_count", 0),
        "tau_out": r.get("eval_count", 0),
        "prefill_ns": r.get("prompt_eval_duration", 0),
        "decode_ns":  r.get("eval_duration", 0),
        "total_ns":   r.get("total_duration", 0),
    }


def main():
    samples = []
    per_model = {}
    for model in MODELS:
        print(f"\n=== {model} ===", flush=True)
        try:
            arch = model_arch(model)
        except Exception as e:
            print(f"  skip (show failed): {e}"); continue
        print(f"  arch L={arch['layers']} n_kv={arch['n_kv']} head_dim={arch['head_dim']} "
              f"gamma={arch['gamma_bytes_per_tok']} B/tok  P={arch['param_label']}")
        # warm load + VRAM baseline
        mem0 = nvidia_mem_used_mib()
        one_call(model, "warmup", 4)
        mem_load = nvidia_mem_used_mib()
        model_rows = []   # (tau_in, tau_out, prefill_ns, decode_ns) for affine regression
        call_id = 0
        for it in INPUT_STEPS:
            reps = max(1, it // 12)
            base = (FILLER * reps)[:it * 6]
            for ot in OUTPUT_STEPS:
                for _ in range(REPEAT):
                    call_id += 1
                    # unique prefix per call defeats Ollama prefix/KV caching,
                    # forcing a full prefill every time (clean alpha regression).
                    prompt = f"[request {call_id}-{model}] " + base + \
                        "\n\nContinue the passage above with a long, detailed multi-paragraph elaboration:"
                    try:
                        m = one_call(model, prompt, ot)
                    except Exception as e:
                        print(f"  call err: {e}"); continue
                    if m["tau_in"] == 0 or m["tau_out"] == 0:
                        continue
                    c = (arch["gamma_bytes_per_tok"] or 0) * m["tau_in"]  # KV footprint (bytes)
                    g = m["prefill_ns"] + m["decode_ns"]                  # MEASURED compute (ns)
                    samples.append({
                        "model": model, "param": arch["param_label"],
                        "tau_in": m["tau_in"], "tau_out": m["tau_out"],
                        "b_UL": BETA * m["tau_in"], "b_DL": BETA * m["tau_out"],
                        "g_ns": g, "c_bytes": c,
                        "prefill_ns": m["prefill_ns"], "decode_ns": m["decode_ns"],
                    })
                    model_rows.append((m["tau_in"], m["tau_out"], m["prefill_ns"], m["decode_ns"]))
            print(f"  in~{it}: {len(model_rows)} samples", flush=True)
        mem_ctx = nvidia_mem_used_mib()

        # affine regression: prefill_ns ~ c0 + alpha*tau_in ; decode_ns ~ c0 + rho*tau_out
        def affine_fit(x, y):
            x, y = np.asarray(x, float), np.asarray(y, float)
            if len(x) < 2 or np.ptp(x) == 0:
                return None, None, None
            slope, icpt = np.polyfit(x, y, 1)
            sst = ((y - y.mean()) ** 2).sum()
            r2 = 1 - ((y - (slope * x + icpt)) ** 2).sum() / sst if sst > 0 else None
            return float(slope), float(icpt), (float(r2) if r2 is not None else None)
        rows = np.array(model_rows, float)
        alpha, a0, a_r2 = affine_fit(rows[:, 0], rows[:, 2])   # slope = alpha (ns/prompt-token)
        rho,   r0, r_r2 = affine_fit(rows[:, 1], rows[:, 3])   # slope = rho   (ns/output-token)
        per_model[model] = {
            **arch,
            "alpha_ns_per_tok": round(alpha, 1) if alpha else None, "alpha_fit_r2": round(a_r2, 3) if a_r2 else None,
            "rho_ns_per_tok": round(rho, 1) if rho else None, "rho_fit_r2": round(r_r2, 3) if r_r2 else None,
            "rho_over_alpha": round(rho / alpha, 2) if (alpha and rho) else None,
            "prefill_overhead_ns": round(a0, 0) if a0 is not None else None,
            "decode_overhead_ns": round(r0, 0) if r0 is not None else None,
            "n_samples": len(model_rows),
            "vram_base_mib": mem0, "vram_loaded_mib": mem_load, "vram_ctx_mib": mem_ctx,
        }
        print(f"  -> alpha={per_model[model]['alpha_ns_per_tok']} (R2={per_model[model]['alpha_fit_r2']})  "
              f"rho={per_model[model]['rho_ns_per_tok']} (R2={per_model[model]['rho_fit_r2']})  "
              f"rho/alpha={per_model[model]['rho_over_alpha']}", flush=True)

    # write samples
    with open(os.path.join(OUTDIR, "samples.csv"), "w", newline="") as f:
        if samples:
            w = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
            w.writeheader(); w.writerows(samples)
    with open(os.path.join(OUTDIR, "per_model.json"), "w") as f:
        json.dump(per_model, f, indent=2)

    # pooled covariance / effective rank (Theorem 3) -- needs numpy
    summary = {"n_samples": len(samples)}
    try:
        V = np.array([[s["b_UL"], s["b_DL"], s["g_ns"], s["c_bytes"]] for s in samples], float)
        # standardize columns (units differ wildly) before covariance, per Thm 3 (correlation structure)
        Vs = (V - V.mean(0)) / (V.std(0) + 1e-12)
        C = np.cov(Vs, rowvar=False)
        ev = np.sort(np.clip(np.linalg.eigvalsh(C), 0, None))[::-1]
        tot = ev.sum()
        summary.update({
            "cov_eigenvalues": [round(float(e), 4) for e in ev],
            "var_top2_frac": round(float(ev[:2].sum()/tot), 4),
            "var_pc3_pc4_frac": round(float(ev[2:].sum()/tot), 4),
            "participation_ratio": round(float((tot**2)/(ev**2).sum()), 3),
        })
        # raw (unstandardized) covariance rank for reference
        Cr = np.cov(V, rowvar=False)
        summary["raw_numerical_rank"] = int(np.linalg.matrix_rank(Cr))
    except Exception as e:
        summary["cov_error"] = str(e)
    with open(os.path.join(OUTDIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
