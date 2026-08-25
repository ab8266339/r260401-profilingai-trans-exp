"""MEC GPU emulator, standard-library build for the closed-loop run.

Same timing model as ../mec/mec_gpu_emulator.py (per-tier alpha/rho compute
demand, mu(n) batching coefficient, 5 ms floor), rebuilt on
http.server + threading so the WSL side needs no third-party packages.
The busy-wait admission of the Flask version is replaced by a bounded
semaphore, which measures queue wait correctly.
"""
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NUM_GPUS = int(os.environ.get("NUM_GPUS", "4"))
TFLOPS_PER_GPU = float(os.environ.get("TFLOPS_PER_GPU", "312"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "8"))
PORT = int(os.environ.get("MEC_PORT", "8080"))

MODEL_TIERS = {
    "7B": {"alpha": 0.014, "rho": 0.030},
    "33B": {"alpha": 0.066, "rho": 0.066},
    "70B": {"alpha": 0.140, "rho": 0.140},
}

TOTAL_TFLOPS = NUM_GPUS * TFLOPS_PER_GPU
MAX_TOTAL = NUM_GPUS * MAX_CONCURRENT

slots = threading.BoundedSemaphore(MAX_TOTAL)
state_lock = threading.Lock()
active_count = 0
completed_count = 0

# Windowed occupancy: integrate active-slot time over a sliding window, the
# same shape as the KPM PrbTotUl percentage. (occupancy_events holds
# (timestamp, active_after_change) transitions.)
WINDOW_S = 2.0
occupancy_events = []


def _record_transition():
    now = time.time()
    occupancy_events.append((now, active_count))
    cutoff = now - WINDOW_S - 1.0
    while len(occupancy_events) > 2 and occupancy_events[1][0] < cutoff:
        occupancy_events.pop(0)


def window_util():
    """Time-averaged busy-slot fraction over the last WINDOW_S seconds."""
    now = time.time()
    start = now - WINDOW_S
    with state_lock:
        events = list(occupancy_events) + [(now, active_count)]
    busy = 0.0
    prev_t, prev_n = start, 0
    for t, n in events:
        if t <= start:
            prev_n = n
            continue
        busy += prev_n * (min(t, now) - prev_t)
        prev_t, prev_n = t, n
    return min(busy / (WINDOW_S * MAX_TOTAL), 1.0)


def mu(n):
    if n <= 1:
        return 1.0
    return 1.0 / (1.0 + (3.0 / math.log(64)) * math.log(n))


def process(tier, tau_in, tau_out, is_pipeline):
    global active_count, completed_count
    t0 = time.time()
    slots.acquire()
    with state_lock:
        active_count += 1
        n = active_count
        _record_transition()
    queue_wait_ms = (time.time() - t0) * 1000
    p = MODEL_TIERS.get(tier, MODEL_TIERS["33B"])
    g = p["alpha"] * tau_in + p["rho"] * tau_out
    hit = 0.95 if is_pipeline else 0.70
    compute_ms = max(g * (1 - hit * 0.3) / TOTAL_TFLOPS * mu(n) * 1000, 5.0)
    time.sleep(compute_ms / 1000.0)
    with state_lock:
        active_count -= 1
        completed_count += 1
        _record_transition()
    slots.release()
    return {"queue_wait_ms": round(queue_wait_ms, 2),
            "compute_ms": round(compute_ms, 2)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            util = window_util()
            with state_lock:
                self._send({"active": active_count, "max_total": MAX_TOTAL,
                            "window_util": round(util, 4),
                            "completed_count": completed_count,
                            "mu_current": round(mu(max(active_count, 1)), 4)})
        elif self.path == "/health":
            self._send({"status": "healthy"})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/infer":
            self._send({"error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        res = process(data.get("tier", "33B"), float(data.get("tau_in", 500)),
                      float(data.get("tau_out", 200)),
                      bool(data.get("is_pipeline", False)))
        self._send(res)


if __name__ == "__main__":
    print(f"[MEC-stub] {NUM_GPUS} GPUs x {MAX_CONCURRENT} slots "
          f"(max_total={MAX_TOTAL}) on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
