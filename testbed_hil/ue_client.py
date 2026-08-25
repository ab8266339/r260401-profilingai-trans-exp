#!/usr/bin/env python3
"""Emulated UE population for the hardware-in-the-loop testbed (WSL side).

N_bg sensing-heavy UEs plus N_llm agent UEs send Poisson-arriving uploads
through the tc-shaped shared uplink to the MEC relay on the Windows host.
Per-UE payload sizes follow the same mixture as the ns-3 M/G/1 mode:
sensing bytes U(0, 50 KB) + 4 B/token * truncLogNormal(5, 1.5, max 2048).
One JSON line is logged per completed request.

Usage:
  python3 ue_client.py --host 172.28.48.1 --port 18090 \
      --n-bg 192 --n-llm 8 --lam 0.2 --dur 720 --seed 1 --out arm.jsonl
"""
import argparse
import http.client
import json
import random
import threading
import time

PROMPT = ("You are stage 2 of an agent pipeline. Summarize the following "
          "state in three sentences and list two follow-up actions. State: "
          "fleet of delivery drones, 12 waypoints, wind 6 m/s NE, battery "
          "margins nominal, one no-fly-zone update received.")


def payload_bytes(rng):
    sens = rng.uniform(0, 50_000)
    tok = min(rng.lognormvariate(5, 1.5), 2048)
    return int(sens + 4 * tok)


def ue(kind, uid, args, out, lock, stop_at):
    rng = random.Random(1_000_003 * args.seed + uid)
    lam = args.lam if kind == "sense" else args.lam_llm
    while True:
        time.sleep(rng.expovariate(lam))
        if time.monotonic() > stop_at:
            return
        size = payload_bytes(rng)
        t_arr = time.monotonic()
        rec = {"kind": kind, "uid": uid, "size": size,
               "t": round(t_arr - T0, 2)}
        try:
            c = http.client.HTTPConnection(args.host, args.port, timeout=120)
            if kind == "sense":
                c.request("POST", "/sense", body=b"x" * size)
            else:
                head = json.dumps({"model": args.model, "prompt": PROMPT,
                                   "num_predict": args.num_predict}
                                  ).encode() + b"\n"
                pad = b"x" * max(0, size - len(head))
                c.request("POST", "/agent", body=head + pad)
            resp = json.loads(c.getresponse().read())
            rec["e2e_s"] = round(time.monotonic() - t_arr, 4)
            rec.update(resp)
            c.close()
        except Exception as e:
            rec["error"] = str(e)
        with lock:
            out.write(json.dumps(rec) + "\n")
            out.flush()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=18090)
    p.add_argument("--n-bg", type=int, default=192)
    p.add_argument("--n-llm", type=int, default=8)
    p.add_argument("--lam", type=float, default=0.2)
    p.add_argument("--lam-llm", type=float, default=0.05)
    p.add_argument("--dur", type=float, default=720)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--model", default="qwen3:4b")
    p.add_argument("--num-predict", type=int, default=120)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    T0 = time.monotonic()
    stop_at = T0 + args.dur
    lock = threading.Lock()
    out = open(args.out, "w")
    threads = []
    for i in range(args.n_bg):
        threads.append(threading.Thread(
            target=ue, args=("sense", i, args, out, lock, stop_at)))
    for i in range(args.n_llm):
        threads.append(threading.Thread(
            target=ue, args=("agent", 10_000 + i, args, out, lock, stop_at)))
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join()
    out.close()
    print("done:", args.out)
