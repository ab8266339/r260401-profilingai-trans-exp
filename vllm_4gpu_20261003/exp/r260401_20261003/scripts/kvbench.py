#!/usr/bin/env python3
"""R260401 (TNSM R1, Reviewer 2 Comment 3 / internal review F04-F06): real-server
session-state (KV) size and pinned-vs-replay prefill cost on vLLM, 4卡机 (host).

One invocation = one model x one server configuration (KV dtype, offload on/off).
It launches `vllm serve`, records versions and the allocator's KV capacity, runs a
pre-fixed grid of cells and writes one JSON per completed cell. Completed cells are
skipped on restart (resumable). Nothing here is tuned after seeing results; the grid,
arms and repetitions are fixed in PLAN.md.

Arms (all see an identical token-count workload; prompts are token-ID lists):
  pinned  : append-only session prefix, one cache_salt per session -> the server reuses
            the session's own KV blocks (prefix cache); only new tokens are prefilled.
  replay  : identical token sequence, fresh cache_salt per stage -> forced full prefill
            (stateless replay of the whole accumulated context). No nonce tokens are
            added, so the semantic input and token counts equal the pinned arm.
  restore : (offload server only) like pinned, but the GPU prefix cache is reset before
            every stage (reset_external=false keeps the CPU tier), so the session KV is
            restored from host memory (KV save/restore).
Each stage generates exactly OUT_TOK tokens (min_tokens=max_tokens, ignore_eos,
greedy); the generated token IDs are appended to the context, so later stages of the
pinned arm prefill only the new instruction (Eq. (3) accounting).
"""
import argparse, asyncio, datetime, hashlib, json, os, platform, re, signal, socket, subprocess, sys, time

import numpy as np
import aiohttp

WORKLOAD_SEED = 20261001   # workload token trace; independent of arm and arm order
INSTR_TOK = 50             # per-stage new instruction (Table VI: ~50 tokens)
OUT_TOK = 128              # per-stage generated tokens (Table VI: 128)
CHECKPOINT_DEPTHS = (1, 2, 4, 8, 16, 32)

# (tau0 first-stage context tokens, concurrency, stages, arms)
GRIDS = {
    # single-card models, bf16/auto KV
    "main": [(900, 1, 32, ("pinned", "replay")), (900, 4, 32, ("pinned", "replay")),
             (3400, 1, 32, ("pinned", "replay")), (3400, 4, 32, ("pinned", "replay")),
             (6700, 1, 8, ("pinned", "replay")), (6700, 4, 8, ("pinned", "replay")),
             (900, 16, 8, ("pinned", "replay"))],
    # FP8 KV cache (separate server start)
    "fp8": [(900, 1, 32, ("pinned", "replay")), (900, 4, 32, ("pinned", "replay"))],
    # KV save/restore via native CPU offloading (separate server start)
    "offload": [(900, 1, 16, ("pinned", "restore", "replay")),
                (3400, 1, 16, ("pinned", "restore", "replay"))],
    # 70B-class TP=4
    "tp4": [(900, 1, 32, ("pinned", "replay")), (900, 4, 32, ("pinned", "replay")),
            (3400, 1, 32, ("pinned", "replay")), (3400, 4, 32, ("pinned", "replay")),
            (6700, 1, 8, ("pinned", "replay")), (6700, 4, 8, ("pinned", "replay"))],
    "tp4_fp8": [(900, 1, 32, ("pinned", "replay"))],
    # smoke (tiny; timing meaningless)
    "smoke": [(300, 1, 4, ("pinned", "replay")), (300, 2, 3, ("pinned", "replay"))],
    "smoke_offload": [(300, 1, 4, ("pinned", "restore", "replay"))],
    # capacity only: start server, record allocator numbers, run nothing
    "capacity": [],
}

VLLM_BIN = os.path.expanduser("~/venvs/vllm/bin/vllm")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:  # noqa
        return f"ERR {e!r}"


def atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def model_fingerprint(model_path):
    files = sorted(os.listdir(model_path))
    out = {"files": {}, "config_sha256": None}
    for fn in files:
        p = os.path.join(model_path, fn)
        if os.path.isfile(p):
            out["files"][fn] = os.path.getsize(p)
    for fn in ("config.json", "model.safetensors.index.json", "tokenizer.json", "generation_config.json"):
        p = os.path.join(model_path, fn)
        if os.path.exists(p):
            out[fn.replace(".", "_") + "_sha256"] = file_digest(p)
    for fn in (".complete_r260401", ".complete"):
        p = os.path.join(model_path, fn)
        if os.path.exists(p):
            out["download_marker"] = open(p).read().strip()
    return out


def theoretical_kv(model_path, kv_dtype):
    cfg = json.load(open(os.path.join(model_path, "config.json")))
    cfg = cfg.get("text_config", cfg)
    L = cfg["num_hidden_layers"]
    hkv = cfg.get("num_key_value_heads", cfg["num_attention_heads"])
    hd = cfg.get("head_dim") or cfg["hidden_size"] // cfg["num_attention_heads"]
    b = 1 if kv_dtype.startswith("fp8") else 2
    return {"layers": L, "kv_heads": hkv, "head_dim": hd, "elem_bytes": b,
            "bytes_per_token": 2 * L * hkv * hd * b,
            "torch_dtype": cfg.get("torch_dtype") or cfg.get("dtype"),
            "quantization_config": cfg.get("quantization_config", {}).get("quant_method")}


def parse_metrics(text):
    """Return selected scalar metrics (summed over label sets)."""
    want = ("vllm:kv_cache_usage_perc", "vllm:num_requests_running", "vllm:num_requests_waiting",
            "vllm:prefix_cache_queries_total", "vllm:prefix_cache_hits_total",
            "vllm:external_prefix_cache_queries_total", "vllm:external_prefix_cache_hits_total",
            "vllm:num_preemptions_total", "vllm:prompt_tokens_cached_total", "vllm:prompt_tokens_total",
            "vllm:request_prefill_time_seconds_sum", "vllm:request_prefill_time_seconds_count")
    out = {}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name in want:
            try:
                out[name] = out.get(name, 0.0) + float(line.rsplit(" ", 1)[1])
            except ValueError:
                pass
    return out


class Poller:
    """Samples /metrics every `dt` seconds while a cell runs."""

    def __init__(self, http, base, dt=0.2):
        self.http, self.base, self.dt = http, base, dt
        self.samples, self._task, self._stop = [], None, False

    async def _run(self):
        while not self._stop:
            t = time.perf_counter()
            try:
                async with self.http.get(self.base + "/metrics", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    m = parse_metrics(await r.text())
                self.samples.append([t] + [m.get(k) for k in POLL_KEYS])
            except Exception:
                pass
            await asyncio.sleep(self.dt)

    def start(self):
        self.samples, self._stop = [], False
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop = True
        if self._task:
            await self._task
        return self.samples


POLL_KEYS = ("vllm:kv_cache_usage_perc", "vllm:num_requests_running", "vllm:num_requests_waiting",
             "vllm:prefix_cache_queries_total", "vllm:prefix_cache_hits_total",
             "vllm:external_prefix_cache_hits_total", "vllm:num_preemptions_total")


async def stage_request(http, base, prompt_ids, salt):
    body = {"model": "m", "prompt": prompt_ids, "max_tokens": OUT_TOK, "min_tokens": OUT_TOK,
            "ignore_eos": True, "temperature": 0.0, "stream": True,
            "stream_options": {"include_usage": True}, "return_token_ids": True}
    if salt is not None:
        body["cache_salt"] = salt
    t0 = time.perf_counter()
    ttft, out_ids, usage, n_chunks = None, [], None, 0
    async with http.post(base + "/v1/completions", json=body,
                         timeout=aiohttp.ClientTimeout(total=1800)) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {(await r.text())[:500]}")
        buf = b""
        async for chunk in r.content.iter_any():
            buf += chunk
            while b"\n\n" in buf:
                ev, buf = buf.split(b"\n\n", 1)
                for line in ev.split(b"\n"):
                    if not line.startswith(b"data: "):
                        continue
                    data = line[6:].strip()
                    if data == b"[DONE]":
                        continue
                    d = json.loads(data)
                    if d.get("usage"):
                        usage = d["usage"]
                    for ch in d.get("choices") or []:
                        ids = ch.get("token_ids") or []
                        if ids or ch.get("text"):
                            n_chunks += 1
                            if ttft is None:
                                ttft = time.perf_counter() - t0
                        out_ids.extend(ids)
    t1 = time.perf_counter()
    ptd = (usage or {}).get("prompt_tokens_details") or {}
    return {"t_start": t0, "t_end": t1, "ttft": ttft, "e2e": t1 - t0,
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
            "completion_tokens": (usage or {}).get("completion_tokens"),
            "cached_tokens": ptd.get("cached_tokens"),
            "n_out_ids": len(out_ids), "out_ids": out_ids, "n_chunks": n_chunks}


async def reset_prefix_cache(http, base, reset_external=False, tries=50):
    for k in range(tries):
        async with http.post(base + f"/reset_prefix_cache?reset_external={'true' if reset_external else 'false'}") as r:
            ok = r.status == 200 and (await r.json()).get("success")
        if ok:
            return k + 1
        await asyncio.sleep(0.05)
    return -1


def session_trace(pool, tau0, stages, rep, sess):
    rng = np.random.default_rng([WORKLOAD_SEED, tau0, rep + 1000, sess])   # rep=-1 is warm-up
    ctx = rng.choice(pool, tau0).tolist()
    instr = [rng.choice(pool, INSTR_TOK).tolist() for _ in range(stages)]
    return ctx, instr


async def run_session(http, base, tok, pool, run_tag, tau0, stages, arm, rep, sess):
    ctx, instr = session_trace(pool, tau0, stages, rep, sess)
    recs, ctx_bytes = [], {}
    for k in range(stages):
        prompt = ctx + instr[k]
        if arm == "replay":
            salt = f"rp|{run_tag}|{tau0}|{rep}|{sess}|{k}"
        else:
            salt = f"pin|{run_tag}|{arm}|{tau0}|{rep}|{sess}"
        restore_resets = None
        if arm == "restore" and k > 0:
            restore_resets = await reset_prefix_cache(http, base, reset_external=False)
        r = await stage_request(http, base, prompt, salt)
        out_ids = r.pop("out_ids")
        r.update({"stage": k + 1, "sent_prompt_len": len(prompt), "restore_reset_tries": restore_resets,
                  "out_sha1": hashlib.sha1(np.asarray(out_ids, dtype=np.int64).tobytes()).hexdigest()[:12]})
        recs.append(r)
        ctx = prompt + out_ids
        if (k + 1) in CHECKPOINT_DEPTHS or k + 1 == stages:
            # application-context bytes (text of the accumulated context, UTF-8) for the context-only comparison
            ctx_bytes[k + 1] = {"tokens": len(ctx), "utf8_bytes": len(tok.decode(ctx).encode("utf-8"))}
    return {"sess": sess, "stages": recs, "ctx_bytes": ctx_bytes}


def gpu_snapshot():
    return sh("nvidia-smi --query-gpu=index,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.sm "
              "--format=csv,noheader")


def launch_server(a, log_path):
    cmd = [VLLM_BIN, "serve", a.model_path, "--served-model-name", "m", "--host", "127.0.0.1",
           "--port", str(a.port), "--max-model-len", str(a.max_model_len),
           "--gpu-memory-utilization", str(a.gpu_util), "--kv-cache-dtype", a.kv_dtype,
           "--seed", "0", "--enable-prompt-tokens-details", "--tensor-parallel-size", str(a.tp),
           "--enable-prefix-caching"]
    if a.offload_gib:
        cmd += ["--kv-offloading-size", str(a.offload_gib), "--kv-offloading-backend", "native"]
    if a.enforce_eager:
        cmd += ["--enforce-eager"]
    cmd += a.extra
    # No system CUDA toolkit on 4卡机: FlashInfer's sampler JIT needs nvcc -> use the PyTorch
    # sampler (greedy decoding here, so sampler choice does not affect outputs) and point any
    # other JIT at the pip-installed CUDA 13 toolkit inside the venv.
    cu = os.path.join(os.path.dirname(os.path.dirname(VLLM_BIN)), "lib", "python3.12", "site-packages", "nvidia", "cu13")
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=a.gpus, VLLM_SERVER_DEV_MODE="1",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", VLLM_NO_USAGE_STATS="1",
               DO_NOT_TRACK="1", VLLM_USE_FLASHINFER_SAMPLER="0", CUDA_HOME=cu,
               PATH=os.path.join(cu, "bin") + ":" + os.environ.get("PATH", ""))
    logf = open(log_path, "a")
    logf.write(f"==== {now_iso()} {' '.join(cmd)}\n")
    logf.flush()
    p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    return p, cmd


def stop_server(p):
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGINT)
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(30)


async def wait_ready(base, p, timeout):
    t0 = time.time()
    async with aiohttp.ClientSession() as http:
        while time.time() - t0 < timeout:
            if p.poll() is not None:
                raise RuntimeError(f"server exited rc={p.returncode}")
            try:
                async with http.get(base + "/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        return time.time() - t0
            except Exception:
                pass
            await asyncio.sleep(3)
    raise RuntimeError("server not ready in time")


def parse_server_log(log_path):
    txt = open(log_path, errors="replace").read()
    txt = txt[txt.rfind("==== "):]  # current launch only
    out = {}
    m = re.findall(r"KV cache size: ([\d,]+) tokens", txt)
    if m:
        out["kv_cache_tokens"] = int(m[-1].replace(",", ""))
    m = re.findall(r"Available KV cache memory: ([\d.]+) GiB", txt)
    if m:
        out["kv_cache_gib"] = float(m[-1])
    m = re.findall(r"Maximum concurrency for ([\d,]+) tokens per request: ([\d.]+)x", txt)
    if m:
        out["max_concurrency"] = m[-1][1]
    m = re.findall(r"Model loading took ([\d.]+) GiB", txt)
    if m:
        out["model_load_gib"] = float(m[-1])
    m = re.findall(r"block_size[=: ]+(\d+)", txt)
    if m:
        out["block_size_seen"] = int(m[-1])
    lines = [l for l in txt.splitlines() if re.search(r"KV cache|offload|block_size|chunked|prefix cach|quantiz|dtype", l, re.I)]
    out["relevant_log_lines"] = lines[:60]
    # NB: "Available KV cache memory" is per GPU worker; with TP=t the per-token bytes over
    # all ranks = kv_cache_gib * 2**30 * t / kv_cache_tokens (computed in summarize.py).
    return out


async def api_probe(http, base, pool):
    """Checks that the API features the arms depend on actually work (recorded, and gates arms)."""
    rng = np.random.default_rng([WORKLOAD_SEED, 7, 7])
    p = rng.choice(pool, 256).tolist()
    r1 = await stage_request(http, base, p, "probe|A")
    r2 = await stage_request(http, base, p, "probe|A")
    r3 = await stage_request(http, base, p, "probe|B")
    res = {"token_ids_returned": r1["n_out_ids"] == OUT_TOK, "completion_tokens": r1["completion_tokens"],
           "cached_same_salt": r2["cached_tokens"], "cached_other_salt": r3["cached_tokens"],
           "cached_field_present": r2["cached_tokens"] is not None}
    res["salt_isolates"] = (res["cached_field_present"] and (r2["cached_tokens"] or 0) > 0
                            and (r3["cached_tokens"] or 0) == 0)
    return res


async def offload_probe(http, base, pool):
    rng = np.random.default_rng([WORKLOAD_SEED, 8, 8])
    p = rng.choice(pool, 512).tolist()
    await stage_request(http, base, p, "oprobe")
    await asyncio.sleep(1.0)
    tries = await reset_prefix_cache(http, base, reset_external=False)
    r = await stage_request(http, base, p, "oprobe")
    return {"reset_tries": tries, "cached_after_gpu_reset": r["cached_tokens"],
            "restore_works": (r["cached_tokens"] or 0) > 0}


def word_pool(tok, model_path):
    cache = os.path.join(os.environ.get("R260401_CACHE_DIR", os.path.expanduser("~/exp/r260401_20261003/cache")),
                         hashlib.sha1(model_path.encode()).hexdigest()[:12] + "_wordpool.npy")
    if os.path.exists(cache):
        return np.load(cache)
    pool = []
    vocab = tok.get_vocab()
    for s, i in vocab.items():
        t = tok.decode([i])
        if re.fullmatch(r" [a-z]{3,10}", t):
            pool.append(i)
    pool.sort()
    pool = np.asarray(pool, dtype=np.int64)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    np.save(cache, pool)
    return pool


async def run_cells(a, out_dir, info):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.model_path)
    pool = word_pool(tok, a.model_path)
    info["word_pool_size"] = int(len(pool))
    base = f"http://127.0.0.1:{a.port}"
    cells_dir = os.path.join(out_dir, "cells")
    os.makedirs(cells_dir, exist_ok=True)
    conn = aiohttp.TCPConnector(limit=64)
    async with aiohttp.ClientSession(connector=conn) as http:
        info["api_probe"] = await api_probe(http, base, pool)
        if a.offload_gib:
            info["offload_probe"] = await offload_probe(http, base, pool)
        atomic_json(os.path.join(out_dir, "server_info.json"), info)
        if not info["api_probe"]["salt_isolates"] or not info["api_probe"]["token_ids_returned"]:
            raise RuntimeError(f"API probe failed: {info['api_probe']}")
        poller = Poller(http, base)
        for (tau0, conc, stages, arms) in GRIDS[a.grid]:
            if "restore" in arms and not info.get("offload_probe", {}).get("restore_works"):
                arms = tuple(x for x in arms if x != "restore")
                print(f"{now_iso()} restore arm dropped: offload probe failed", flush=True)
            setting = f"t{tau0}_c{conc}_s{stages}"
            # untimed warm-up per setting and arm (rep=-1 trace, discarded)
            wfile = os.path.join(cells_dir, f"{setting}_warmup.done")
            if not os.path.exists(wfile):
                for arm in arms:
                    await asyncio.gather(*[run_session(http, base, tok, pool, a.run_tag, tau0, min(stages, 4), arm, -1, s)
                                           for s in range(conc)])
                open(wfile, "w").write(now_iso())
            for rep in range(a.reps):
                order = list(arms) if rep % 2 == 0 else list(reversed(arms))
                for pos, arm in enumerate(order):
                    cid = f"{setting}_{arm}_r{rep:02d}"
                    path = os.path.join(cells_dir, cid + ".json")
                    if os.path.exists(path):
                        continue
                    rtries = await reset_prefix_cache(http, base, reset_external=True, tries=200)
                    snap0, load0 = gpu_snapshot(), os.getloadavg()
                    poller.start()
                    t0w = now_iso()
                    sess = await asyncio.gather(*[run_session(http, base, tok, pool, a.run_tag, tau0, stages, arm, rep, s)
                                                  for s in range(conc)])
                    samples = await poller.stop()
                    rec = {"cell": cid, "model_key": a.model_key, "kv_dtype": a.kv_dtype, "grid": a.grid,
                           "offload_gib": a.offload_gib, "tp": a.tp, "gpus": a.gpus,
                           "tau0": tau0, "conc": conc, "stages": stages, "arm": arm, "rep": rep,
                           "arm_position": pos, "workload_seed": WORKLOAD_SEED, "instr_tok": INSTR_TOK,
                           "out_tok": OUT_TOK, "t_wall_start_utc": t0w, "t_wall_end_utc": now_iso(),
                           "pre_reset_tries": rtries, "gpu_snapshot_start": snap0, "loadavg_start": load0,
                           "gpu_snapshot_end": gpu_snapshot(), "loadavg_end": os.getloadavg(),
                           "sessions": sess, "poll_keys": POLL_KEYS, "poll": samples,
                           "machine": socket.gethostname()}
                    atomic_json(path, rec)
                    tot = [s["stages"][-1]["t_end"] - s["stages"][0]["t_start"] for s in sess]
                    print(f"{now_iso()} {a.model_key}/{a.kv_dtype} {cid} mean_pipeline_s={np.mean(tot):.2f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-key", required=True)
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--grid", required=True, choices=list(GRIDS))
    ap.add_argument("--gpus", required=True, help="CUDA_VISIBLE_DEVICES for the server")
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--kv-dtype", default="auto")
    ap.add_argument("--offload-gib", type=float, default=0)
    ap.add_argument("--gpu-util", type=float, default=0.90)
    ap.add_argument("--max-model-len", type=int, default=12288)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--enforce-eager", action="store_true")
    ap.add_argument("--out-root", default=os.path.expanduser("~/exp/r260401/results"))
    ap.add_argument("--ready-timeout", type=int, default=1800)
    ap.add_argument("--prep-only", action="store_true", help="CPU only: build/cache the token pool and exit")
    ap.add_argument("extra", nargs="*", help="extra vllm serve args after --")
    a = ap.parse_args()
    if a.prep_only:
        from transformers import AutoTokenizer
        pool = word_pool(AutoTokenizer.from_pretrained(a.model_path), a.model_path)
        print(f"word pool {a.model_path}: {len(pool)} tokens; theoretical KV {theoretical_kv(a.model_path, a.kv_dtype)}")
        return 0
    cfg_tag = f"{a.kv_dtype}" + (f"_off{int(a.offload_gib)}" if a.offload_gib else "") + f"_{a.grid}"
    a.run_tag = f"{a.model_key}|{cfg_tag}"
    out_dir = os.path.join(a.out_root, a.model_key, cfg_tag)
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(os.path.join(out_dir, "DONE")):
        print(f"{now_iso()} {out_dir} already DONE, skipping", flush=True)
        return 0
    if not os.path.exists(os.path.join(a.model_path, "config.json")) or not (
            os.path.exists(os.path.join(a.model_path, ".complete_r260401")) or os.path.exists(os.path.join(a.model_path, ".complete"))):
        print(f"{now_iso()} MODEL NOT READY: {a.model_path}", flush=True)
        return 3
    log_path = os.path.join(out_dir, "server.log")
    global OUT_TOK
    if a.grid.startswith("smoke"):
        OUT_TOK = 16   # smoke only: keep GPU time on a shared card under 3 min
    from importlib.metadata import version as pv
    info = {"machine": socket.gethostname(), "machine_label": "4卡机 (host, 192.168.5.11)",
            "started_utc": now_iso(), "python": platform.python_version(), "vllm": pv("vllm"),
            "torch": pv("torch"), "transformers": pv("transformers"),
            "aiohttp": aiohttp.__version__, "numpy": np.__version__,
            "driver": sh("nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1"),
            "gpu_names": sh("nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader"),
            "cpu": sh("lscpu | grep 'Model name' | head -1"), "kernel": platform.release(),
            "model_key": a.model_key, "model_path": a.model_path, "model_fingerprint": model_fingerprint(a.model_path),
            "kv_dtype": a.kv_dtype, "tp": a.tp, "gpus": a.gpus, "gpu_util": a.gpu_util,
            "max_model_len": a.max_model_len, "offload_gib": a.offload_gib, "grid": a.grid,
            "grid_def": GRIDS[a.grid], "reps": a.reps, "workload_seed": WORKLOAD_SEED,
            "instr_tok": INSTR_TOK, "out_tok": OUT_TOK, "server_seed": 0,
            "theoretical_kv": theoretical_kv(a.model_path, a.kv_dtype),
            "gpu_snapshot_before_launch": gpu_snapshot(), "loadavg_before": os.getloadavg()}
    def _term(signum, frame):  # make SIGTERM/SIGHUP run the finally-block that stops the server
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGHUP, _term)
    p, cmd = launch_server(a, log_path)
    info["serve_cmd"] = cmd
    rc = 0
    try:
        info["ready_seconds"] = asyncio.run(wait_ready(f"http://127.0.0.1:{a.port}", p, a.ready_timeout))
        info["server_log"] = parse_server_log(log_path)
        atomic_json(os.path.join(out_dir, "server_info.json"), info)
        asyncio.run(run_cells(a, out_dir, info))
        info["finished_utc"] = now_iso()
        atomic_json(os.path.join(out_dir, "server_info.json"), info)
        open(os.path.join(out_dir, "DONE"), "w").write(now_iso())
    except Exception as e:
        rc = 1
        info["error"] = repr(e)
        atomic_json(os.path.join(out_dir, "server_info_error.json"), info)
        print(f"{now_iso()} ERROR {a.model_key}/{cfg_tag}: {e!r}", flush=True)
    finally:
        stop_server(p)
    return rc


if __name__ == "__main__":
    sys.exit(main())
