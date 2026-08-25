#!/usr/bin/env python3
"""
Batching multiplexing coefficient mu(n) (Proposition 2 / Assumption A4).
Fires n concurrent decode-heavy requests at an Ollama server with continuous
batching enabled (OLLAMA_NUM_PARALLEL>1) and measures aggregate throughput.

  mu(n) = TPS(1) / TPS_agg(n)          in (0,1];  mu(1)=1
          (TPS_agg = total output tokens / wall time for n concurrent calls)
Convexity (A4): second forward difference mu(n+2)-2mu(n+1)+mu(n) >= 0.

HOST via env OLLAMA_HOST (default 127.0.0.1:11435, the batching side-server).
Output: batching_mu.json
"""
import json, os, time, threading, urllib.request

HOST = "http://" + os.environ.get("OLLAMA_HOST", "127.0.0.1:11435")
MODEL = os.environ.get("MU_MODEL", "qwen3:4b")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batching_mu.json")
NS = [1, 2, 4, 8, 16]
NUM_PREDICT = 200


def gen(prompt):
    req = urllib.request.Request(HOST + "/api/generate",
        data=json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "think": False,
                         "options": {"temperature": 0, "num_predict": NUM_PREDICT}}).encode(),
        headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return d.get("eval_count", 0)


def batch(n):
    res = []
    def one(i):
        res.append(gen(f"[stream {i}] Write a long detailed technical essay on packet routing:"))
    ths = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    t = time.time()
    for th in ths: th.start()
    for th in ths: th.join()
    wall = time.time() - t
    tot = sum(res)
    return wall, tot, tot / wall


def main():
    gen("warmup")  # load model
    tps = {}
    for n in NS:
        wall, tot, agg = batch(n)
        tps[n] = agg
        print(f"n={n:2d}: wall={wall:5.1f}s tokens={tot:4d} aggregate={agg:6.1f} tok/s", flush=True)
    base = tps[1]
    mu = {n: round(base / tps[n], 4) for n in NS}   # mu(n) = TPS(1)/TPS_agg(n)
    # convexity check on the sampled points
    conv = {}
    for i in range(len(NS) - 2):
        a, b, c = NS[i], NS[i+1], NS[i+2]
        conv[f"{a},{b},{c}"] = round(mu[c] - 2*mu[b] + mu[a], 4)
    out = {"host": HOST, "model": MODEL, "tps_agg": tps, "mu": mu,
           "throughput_gain_at_max": round(tps[NS[-1]]/base, 2),
           "second_diff_convexity": conv}
    json.dump(out, open(OUT, "w"), indent=2)
    print("\nmu(n):", mu)
    print("gain at n=%d: %.2fx" % (NS[-1], tps[NS[-1]]/base))
    print("convexity (>=0 means convex):", conv)


if __name__ == "__main__":
    main()
