"""Summarize the closed-loop gate run (CLOSED_LOOP_CARD.md endpoints)."""
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "results_closed_loop_20260803"

EXPECTED = {"P1": {"decline"}, "P2": {"raise", "at-target"}, "P3": {"release"}}


def main():
    ticks = [json.loads(l) for l in
             (OUT / "gate_ticks.jsonl").read_text().splitlines() if l.strip()]
    kpm = (OUT / "kpm_live.log").read_text(errors="replace")
    n_ind = len(re.findall(r"PrbTotUl", kpm))

    controls = [t for t in ticks if t.get("control")]
    summary = {
        "ticks": len(ticks),
        "kpm_indications": n_ind,
        "controls_sent": len(controls),
        "controls_acked": sum(1 for t in controls if t["control"]["acked"]),
        "control_events": [
            {"phase": t["phase"], "decision": t["decision"],
             "quota": t["control"]["quota"],
             "acked": t["control"]["acked"],
             "ack_latency_s": t["control"]["latency_s"]} for t in controls],
        "phases": {},
    }
    for ph in ("P1", "P2", "P3"):
        rows = [t for t in ticks if t["phase"] == ph]
        fresh = [t for t in rows if not t["prb_stale"] and t["gpu_util"] is not None]
        decisions = {}
        for t in rows:
            key = t["decision"] if t["decision"] != "hold" else t["reason"]
            decisions[key] = decisions.get(key, 0) + 1
        scored = [t for t in fresh
                  if t["decision"] in ("raise", "release", "decline")
                  or t["reason"] in ("at-target",)]
        ok = sum(1 for t in scored
                 if (t["decision"] in EXPECTED[ph]) or (t["reason"] in EXPECTED[ph]))
        gus = [t["gpu_util"] for t in fresh]
        prbs = [t["prb_tot_ul"] for t in fresh if t["prb_tot_ul"] is not None]
        dl = [t["decide_ms"] for t in rows if not t.get("control")]
        summary["phases"][ph] = {
            "ticks": len(rows), "fresh_telemetry_ticks": len(fresh),
            "decisions": decisions,
            "scored_ticks": len(scored), "scored_matching_ground_truth": ok,
            "gpu_util_range": [min(gus), max(gus)] if gus else None,
            "prb_range": [min(prbs), max(prbs)] if prbs else None,
            "decide_ms_median": sorted(dl)[len(dl) // 2] if dl else None,
        }
    (OUT / "closed_loop_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    ok_all = (summary["controls_sent"] == summary["controls_acked"] == 2)
    print("SUCCESS-CRITERION:", "PASS" if ok_all else "CHECK", file=sys.stderr)


if __name__ == "__main__":
    main()
