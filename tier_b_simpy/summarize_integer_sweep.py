"""Summarize the frozen 180--260 paired integer sweep without point picking."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "results_integer_180_260" / "tier_b_results.json"
OUT = ROOT / "results_integer_180_260"
EXPECTED_N = list(range(180, 261))
EXPECTED_SEEDS = list(range(5))
T95_DF4 = 2.7764451051977987


def ci95(values: list[float]) -> tuple[float, float, float]:
    center = mean(values)
    half = T95_DF4 * stdev(values) / math.sqrt(len(values))
    return center, center - half, center + half


def sign_class(metric: str, low: float, high: float) -> str:
    if metric in {"delta_mean_ms", "delta_p95_ms"}:
        if high < 0:
            return "improvement"
        if low > 0:
            return "degradation"
    else:
        if low > 0:
            return "improvement"
        if high < 0:
            return "degradation"
    return "unresolved"


def main() -> None:
    rows = json.loads(RAW.read_text(encoding="utf-8"))
    by_key: dict[tuple[int, int, str], dict] = {}
    for row in rows:
        key = (int(row["N"]), int(row["seed"]), str(row["mode"]))
        if key in by_key:
            raise ValueError(f"duplicate row: {key}")
        by_key[key] = row

    expected = {
        (n, seed, mode)
        for n in EXPECTED_N
        for seed in EXPECTED_SEEDS
        for mode in ("baseline", "full")
    }
    missing = sorted(expected - set(by_key))
    extra = sorted(set(by_key) - expected)
    if missing or extra:
        raise ValueError(f"grid mismatch: missing={missing[:5]}, extra={extra[:5]}")

    paired_rows: list[dict] = []
    per_n: dict[int, list[dict]] = defaultdict(list)
    for n in EXPECTED_N:
        for seed in EXPECTED_SEEDS:
            base = by_key[(n, seed, "baseline")]
            full = by_key[(n, seed, "full")]
            if int(base["count"]) <= 0 or int(full["count"]) <= 0:
                raise ValueError(f"empty endpoint at N={n}, seed={seed}")
            row = {
                "N": n,
                "seed": seed,
                "delta_mean_ms": float(full["mean_total_ms"]) - float(base["mean_total_ms"]),
                "delta_p95_ms": float(full["p95_ms"]) - float(base["p95_ms"]),
                "delta_sla_pp": 100.0 * (float(full["sla_rate"]) - float(base["sla_rate"])),
                "baseline_gpu_queue_share": float(base["mean_gpu_queue_ms"]) / float(base["mean_total_ms"]),
                "full_gpu_queue_share": float(full["mean_gpu_queue_ms"]) / float(full["mean_total_ms"]),
                "baseline_ul_share": float(base["mean_ul_ms"]) / float(base["mean_total_ms"]),
                "full_ul_share": float(full["mean_ul_ms"]) / float(full["mean_total_ms"]),
                "baseline_count": int(base["count"]),
                "full_count": int(full["count"]),
                "full_final_tdd_ratio": float(full["final_tdd_ratio"]),
                "xapp_decisions": int(full["xapp_decisions"]),
                "rapp_decisions": int(full["rapp_decisions"]),
            }
            paired_rows.append(row)
            per_n[n].append(row)

    point_rows: list[dict] = []
    metrics = ("delta_mean_ms", "delta_p95_ms", "delta_sla_pp")
    for n in EXPECTED_N:
        point: dict[str, float | int | str] = {"N": n}
        for metric in metrics:
            center, low, high = ci95([float(row[metric]) for row in per_n[n]])
            point[f"{metric}_mean"] = center
            point[f"{metric}_ci95_low"] = low
            point[f"{metric}_ci95_high"] = high
            point[f"{metric}_class"] = sign_class(metric, low, high)
        point["baseline_gpu_queue_share_mean"] = mean(
            float(row["baseline_gpu_queue_share"]) for row in per_n[n]
        )
        point["full_gpu_queue_share_mean"] = mean(
            float(row["full_gpu_queue_share"]) for row in per_n[n]
        )
        point["baseline_ul_share_mean"] = mean(
            float(row["baseline_ul_share"]) for row in per_n[n]
        )
        point["full_ul_share_mean"] = mean(
            float(row["full_ul_share"]) for row in per_n[n]
        )
        point["full_final_tdd_ratio_min"] = min(
            float(row["full_final_tdd_ratio"]) for row in per_n[n]
        )
        point["full_final_tdd_ratio_max"] = max(
            float(row["full_final_tdd_ratio"]) for row in per_n[n]
        )
        point_rows.append(point)

    classifications = {
        metric: {
            label: sum(row[f"{metric}_class"] == label for row in point_rows)
            for label in ("improvement", "degradation", "unresolved")
        }
        for metric in metrics
    }
    summary = {
        "design": {
            "device_counts": [EXPECTED_N[0], EXPECTED_N[-1]],
            "step": 1,
            "num_device_counts": len(EXPECTED_N),
            "seeds": EXPECTED_SEEDS,
            "arms": ["baseline", "full"],
            "warmup_s": 10,
            "observation_s": 60,
            "truth_type": "abstract SimPy discrete-event model",
        },
        "grid_level": {
            "median_delta_mean_ms": median(float(row["delta_mean_ms_mean"]) for row in point_rows),
            "median_delta_p95_ms": median(float(row["delta_p95_ms_mean"]) for row in point_rows),
            "median_delta_sla_pp": median(float(row["delta_sla_pp_mean"]) for row in point_rows),
            "median_baseline_gpu_queue_share": median(
                float(row["baseline_gpu_queue_share_mean"]) for row in point_rows
            ),
            "median_full_gpu_queue_share": median(
                float(row["full_gpu_queue_share_mean"]) for row in point_rows
            ),
            "median_baseline_ul_share": median(
                float(row["baseline_ul_share_mean"]) for row in point_rows
            ),
            "median_full_ul_share": median(
                float(row["full_ul_share_mean"]) for row in point_rows
            ),
            "full_final_tdd_ratio_min": min(
                float(row["full_final_tdd_ratio_min"]) for row in point_rows
            ),
            "full_final_tdd_ratio_max": max(
                float(row["full_final_tdd_ratio_max"]) for row in point_rows
            ),
            "point_classifications": classifications,
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("paired_rows.csv", paired_rows), ("point_summary.csv", point_rows)):
        with (OUT / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
