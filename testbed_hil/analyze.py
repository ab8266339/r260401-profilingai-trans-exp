#!/usr/bin/env python3
"""Analyze hardware-in-the-loop soak logs.

For each arm: sense-UE UL sojourn stats vs the M/G/1 (P-K) prediction
computed from the REALIZED payload moments in the log, plus agent-UE
end-to-end decomposition (UL share vs inference share).

Usage: python3 analyze.py <rate_mbit> <warmup_s> <file.jsonl> [...]
"""
import json
import statistics as st
import sys


def analyze(path, rate_mbit, warmup):
    sense, agent, errors, bad = [], [], 0, 0
    for line in open(path):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if "error" in r:
            errors += 1
            continue
        if r.get("t", 0) < warmup:
            continue
        (sense if r["kind"] == "sense" else agent).append(r)

    C = rate_mbit * 1e6 / 8  # bytes/s
    sizes = [r["size"] for r in sense]
    n_dur = max(r["t"] for r in sense) - warmup
    lam = len(sense) / n_dur
    es = st.mean(s / C for s in sizes)
    es2 = st.mean((s / C) ** 2 for s in sizes)
    rho = lam * es
    w_pk = lam * es2 / (2 * (1 - rho)) if rho < 1 else float("inf")
    sojourn_pk = w_pk + es

    e2e = [r["e2e_s"] for r in sense]
    print(f"\n=== {path}  (rate {rate_mbit} Mbit/s, warmup {warmup}s) ===")
    print(f"sense: n={len(e2e)}, lambda={lam:.1f}/s, rho={rho:.3f}, "
          f"errors={errors}, malformed_lines={bad}")
    print(f"  measured sojourn: mean={st.mean(e2e)*1e3:.1f} ms, "
          f"p50={st.median(e2e)*1e3:.1f}, "
          f"p95={sorted(e2e)[int(0.95*len(e2e))]*1e3:.1f}, "
          f"max={max(e2e)*1e3:.0f}")
    print(f"  M/G/1 (P-K, realized moments): sojourn={sojourn_pk*1e3:.1f} ms"
          f"  (wait {w_pk*1e3:.1f} + service {es*1e3:.1f})")

    if agent:
        ae = [r["e2e_s"] for r in agent]
        ai = [r.get("infer_s", 0) for r in agent]
        ul = [e - i for e, i in zip(ae, ai)]
        print(f"agent: n={len(ae)}")
        print(f"  e2e   mean={st.mean(ae)*1e3:.0f} ms  "
              f"p95={sorted(ae)[int(0.95*len(ae))]*1e3:.0f} ms")
        print(f"  infer mean={st.mean(ai)*1e3:.0f} ms   "
              f"UL+net mean={st.mean(ul)*1e3:.0f} ms  "
              f"(UL share {100*st.mean(ul)/st.mean(ae):.1f}%)")


if __name__ == "__main__":
    rate = float(sys.argv[1])
    warm = float(sys.argv[2])
    for f in sys.argv[3:]:
        analyze(f, rate, warm)
