"""Saturating load injector for the MEC stub (standard library only).

Runs THREADS concurrent /infer clients while the flag file exists.
"""
import json
import os
import sys
import threading
import time
import urllib.request

MEC = os.environ.get("MEC_URL", "http://127.0.0.1:8080")
THREADS = int(os.environ.get("INJECTOR_THREADS", "48"))
FLAG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/profilingai_inject_on"

PAYLOAD = json.dumps({"tier": "70B", "tau_in": 4000, "tau_out": 2000,
                      "is_pipeline": False}).encode()


def worker():
    while os.path.exists(FLAG):
        try:
            req = urllib.request.Request(
                MEC + "/infer", data=PAYLOAD,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            time.sleep(0.2)


if __name__ == "__main__":
    ths = [threading.Thread(target=worker, daemon=True) for _ in range(THREADS)]
    for t in ths:
        t.start()
    while os.path.exists(FLAG):
        time.sleep(0.5)
    for t in ths:
        t.join(timeout=35)
    print("[injector] drained", flush=True)
