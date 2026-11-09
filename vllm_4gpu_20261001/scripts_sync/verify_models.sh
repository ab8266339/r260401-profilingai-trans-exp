#!/bin/bash
# CPU only: verify every downloaded file against ModelScope's published SHA-256; write ~/exp/r260401/model_verify.json
~/venvs/vllm/bin/python - <<'PY'
import json, os, hashlib, urllib.request, concurrent.futures as cf
models = ["Qwen/Qwen3-4B", "Qwen/Qwen3-8B", "Qwen/Qwen3-14B-AWQ", "Qwen/Qwen3-32B-AWQ", "Qwen/Qwen2.5-72B-Instruct-AWQ"]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""): h.update(b)
    return h.hexdigest()
out = {}
for m in models:
    meta = json.load(urllib.request.urlopen(f"https://www.modelscope.cn/api/v1/models/{m}/repo/files?Recursive=true", timeout=60))
    files = [f for f in meta["Data"]["Files"] if f.get("Type") == "blob"]
    root = os.path.expanduser(f"~/models/{m}")
    with cf.ThreadPoolExecutor(16) as ex:
        futs = {f["Path"]: ex.submit(sha, os.path.join(root, f["Path"])) for f in files if os.path.exists(os.path.join(root, f["Path"]))}
    res = []
    for f in files:
        p = f["Path"]; local = futs[p].result() if p in futs else None
        res.append({"path": p, "size": f.get("Size"), "sha256_remote": f.get("Sha256"), "sha256_local": local,
                    "ok": (local is not None and (not f.get("Sha256") or local == f.get("Sha256")))})
    out[m] = {"revision": meta["Data"].get("Revision") or meta["Data"].get("LatestCommitter"), "all_ok": all(r["ok"] for r in res), "files": res}
    print(m, "all_ok", out[m]["all_ok"], "n", len(res), flush=True)
json.dump(out, open(os.path.expanduser("~/exp/r260401/model_verify.json"), "w"), indent=1)
PY
# also cache the token pools (CPU) so GPU time is not spent on it
for p in Qwen3-4B Qwen3-8B Qwen3-14B-AWQ Qwen3-32B-AWQ Qwen2.5-72B-Instruct-AWQ; do
  CUDA_VISIBLE_DEVICES="" ~/venvs/vllm/bin/python ~/exp/r260401/kvbench.py --model-key x --model-path ~/models/Qwen/$p --grid capacity --gpus "" --prep-only 2>&1 | tail -1
done
echo VERIFY_DONE
