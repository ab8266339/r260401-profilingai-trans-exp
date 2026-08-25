"""Closed-loop gate runner (CLOSED_LOOP_CARD.md, standard library only).

Every control tick: read the newest RRU.PrbTotUl from the continuously
running KPM xApp log (live E2 telemetry), read GPU occupancy from the MEC
stub (edge adapter), apply the frozen gate rule, and when the decision is an
action invoke the parameterized RC xApp and require the synchronous
acknowledgment. Phases annotate ticks for scoring; they never drive
decisions.
"""
import argparse
import json
import re
import subprocess
import time
import urllib.request

PRB_RE = re.compile(r"PrbTotUl[^0-9]*([0-9]+(?:\.[0-9]+)?)")

HIGH, LOW = 60.0, 30.0
GPU_CEIL = 0.80
RAISE_Q = ("10", "90", "80")
RELEASE_Q = ("10", "50", "40")


def read_phase(path):
    try:
        return open(path).read().strip() or "unknown"
    except OSError:
        return "unknown"


def mec_util(url):
    try:
        with urllib.request.urlopen(url + "/status", timeout=0.8) as r:
            d = json.loads(r.read())
        if "window_util" in d:
            return d["window_util"]
        return d["active"] / max(d["max_total"], 1)
    except Exception:
        return None


def send_control(xapp, quota, conf, sm):
    t0 = time.time()
    try:
        p = subprocess.run([xapp, "0", *quota, "-c", conf, "-p", sm],
                           capture_output=True, text=True, timeout=30)
        out = p.stdout + p.stderr
        acked = ("synchronous control response received: true" in out
                 and p.returncode == 0)
        return acked, time.time() - t0, out[-400:]
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, "timeout"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kpm-log", required=True)
    ap.add_argument("--mec", default="http://127.0.0.1:8080")
    ap.add_argument("--xapp", required=True)
    ap.add_argument("--ric-conf", required=True)
    ap.add_argument("--sm-path", required=True)
    ap.add_argument("--phase-file", default="/tmp/profilingai_phase")
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=100.0)
    a = ap.parse_args()

    quota_state = "baseline"
    pos = 0
    last_prb = None
    t_end = time.time() + a.duration
    with open(a.out, "w") as fh:
        while time.time() < t_end:
            tick_t0 = time.time()
            phase = read_phase(a.phase_file)

            try:
                with open(a.kpm_log, "rb") as f:
                    f.seek(pos)
                    chunk = f.read().decode(errors="replace")
                    pos = f.tell()
            except OSError:
                chunk = ""
            vals = PRB_RE.findall(chunk)
            stale = not vals
            if vals:
                last_prb = float(vals[-1])

            gpu = mec_util(a.mec)
            decision, reason, control = "hold", "no-pressure", None

            if last_prb is None or gpu is None:
                decision, reason = "hold", "telemetry-missing"
            elif last_prb > HIGH and quota_state == "baseline":
                if gpu < GPU_CEIL:
                    decision, reason = "raise", "ul-pressure-compute-headroom"
                    acked, lat, tail = send_control(
                        a.xapp, RAISE_Q, a.ric_conf, a.sm_path)
                    control = {"quota": RAISE_Q, "acked": acked,
                               "latency_s": round(lat, 2), "tail": tail}
                    if acked:
                        quota_state = "raised"
                else:
                    decision, reason = "decline", "compute-hold"
            elif last_prb > HIGH and quota_state == "raised":
                decision, reason = "hold", "at-target"
            elif last_prb < LOW and quota_state == "raised":
                decision, reason = "release", "ul-pressure-cleared"
                acked, lat, tail = send_control(
                    a.xapp, RELEASE_Q, a.ric_conf, a.sm_path)
                control = {"quota": RELEASE_Q, "acked": acked,
                           "latency_s": round(lat, 2), "tail": tail}
                if acked:
                    quota_state = "baseline"

            rec = {"t": round(tick_t0, 3), "phase": phase,
                   "prb_tot_ul": last_prb, "prb_stale": stale,
                   "gpu_util": None if gpu is None else round(gpu, 3),
                   "decision": decision, "reason": reason,
                   "quota_state": quota_state,
                   "decide_ms": round((time.time() - tick_t0) * 1000, 1),
                   "control": control}
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            time.sleep(max(0.0, 1.0 - (time.time() - tick_t0)))
    print("[gate-runner] done", flush=True)


if __name__ == "__main__":
    main()
