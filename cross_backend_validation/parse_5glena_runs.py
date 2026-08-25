from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path

from scipy import stats


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "results" / "5glena" / "raw"
OUT_CSV = ROOT / "results" / "5glena" / "summary.csv"
OUT_JSON = ROOT / "results" / "5glena" / "summary.json"
PAIRED_CSV = ROOT / "results" / "5glena" / "paired_differences.csv"
PAIRED_JSON = ROOT / "results" / "5glena" / "paired_summary.json"


def scalar(text: str, label: str) -> float | None:
    match = re.search(
        rf"^\s*{re.escape(label)}:\s*([-+]?(?:nan|inf|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?))",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    if not match or "nan" in match.group(1).lower() or "inf" in match.group(1).lower():
        return None
    return float(match.group(1))


def parse_log(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="strict")
    tag_match = re.fullmatch(r"(.+)_seed(\d{4})", path.stem)
    if not tag_match:
        raise ValueError(f"Unexpected log name: {path.name}")

    flows = [
        (float(t), int(rx), int(tx))
        for t, rx, tx in re.findall(
            r"thr value is:([-+0-9.eE]+), and rxBytes:(\d+), and tx bytes:(\d+)",
            text,
        )
    ]
    zero_only = re.findall(r"thr value:0(?:\s|$)", text)
    if flows:
        rx_bytes = sum(row[1] for row in flows)
        tx_bytes = sum(row[2] for row in flows)
        flow_count = len(flows)
    else:
        rx_bytes = 0
        tx_bytes = 0
        flow_count = len(zero_only)

    zero_delivery = tx_bytes == 0 or rx_bytes == 0
    row = {
        "pattern": tag_match.group(1),
        "seed": int(tag_match.group(2)),
        "flow_count": flow_count,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "delivered_byte_fraction": (rx_bytes / tx_bytes) if tx_bytes else 0.0,
        "mean_upt_mbps": scalar(text, "Mean user perceived throughput"),
        "p95_upt_mbps": scalar(text, "95tile UPT"),
        "mean_delay_ms": scalar(text, "Mean delay"),
        "p95_delay_ms": scalar(text, "95tile delay"),
        "median_delay_ms": scalar(text, "Median delay"),
        "p05_delay_ms": scalar(text, "5tile delay"),
        "zero_delivery": zero_delivery,
        "source_log": str(path.relative_to(ROOT)),
    }
    if zero_delivery:
        for key in ("mean_delay_ms", "p95_delay_ms", "median_delay_ms", "p05_delay_ms"):
            row[key] = None
    return row


def main() -> None:
    rows = [parse_log(path) for path in sorted(RAW.glob("*.log"))]
    if not rows:
        raise SystemExit(f"No raw logs found under {RAW}")

    fields = list(rows[0])
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    OUT_JSON.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    by_key = {(str(row["pattern"]), int(row["seed"])): row for row in rows}
    paired: list[dict[str, float | int]] = []
    seeds = sorted(
        seed
        for pattern, seed in by_key
        if pattern == "ul4_spread" and ("ul11_spread", seed) in by_key
    )
    for seed in seeds:
        baseline = by_key[("ul4_spread", seed)]
        adapted = by_key[("ul11_spread", seed)]
        b_delivery = float(baseline["delivered_byte_fraction"])
        a_delivery = float(adapted["delivered_byte_fraction"])
        b_upt = float(baseline["mean_upt_mbps"])
        a_upt = float(adapted["mean_upt_mbps"])
        b_mean_delay = float(baseline["mean_delay_ms"])
        a_mean_delay = float(adapted["mean_delay_ms"])
        b_p95_delay = float(baseline["p95_delay_ms"])
        a_p95_delay = float(adapted["p95_delay_ms"])
        paired.append(
            {
                "seed": seed,
                "delta_delivered_byte_fraction": a_delivery - b_delivery,
                "delivered_byte_gain_pct": 100.0 * (a_delivery / b_delivery - 1.0),
                "delta_mean_upt_mbps": a_upt - b_upt,
                "mean_upt_gain_pct": 100.0 * (a_upt / b_upt - 1.0),
                "delta_mean_delay_ms": a_mean_delay - b_mean_delay,
                "mean_delay_reduction_pct": 100.0 * (1.0 - a_mean_delay / b_mean_delay),
                "delta_p95_delay_ms": a_p95_delay - b_p95_delay,
                "p95_delay_reduction_pct": 100.0 * (1.0 - a_p95_delay / b_p95_delay),
            }
        )

    if paired:
        with PAIRED_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
            writer.writeheader()
            writer.writerows(paired)
        summary: dict[str, object] = {"paired_seed_count": len(paired), "metrics": {}}
        for metric in list(paired[0])[1:]:
            values = [float(row[metric]) for row in paired]
            mean = sum(values) / len(values)
            sd = math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
            half = float(stats.t.ppf(0.975, len(values) - 1) * sd / math.sqrt(len(values)))
            summary["metrics"][metric] = {
                "mean": mean,
                "ci95_low": mean - half,
                "ci95_high": mean + half,
            }
        PAIRED_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Parsed {len(rows)} runs -> {OUT_CSV}")


if __name__ == "__main__":
    main()
