from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MATLAB_CI = (
    ROOT.parent
    / "matlab_nr_validation"
    / "results"
    / "aggregates"
    / "umi-static-shadow-v2"
    / "formal"
    / "paired_ci95.csv"
)
LENA_CI = ROOT / "results" / "5glena" / "paired_summary.json"
OUT = ROOT / "results" / "cross_backend_effects.csv"


def read_matlab() -> list[dict[str, str]]:
    with MATLAB_CI.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def pick(rows: list[dict[str, str]], load: int, metric: str) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if int(row["background_ues"]) == load and row["metric"] == metric
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one MATLAB row for load={load}, metric={metric}")
    return matches[0]


def main() -> None:
    matlab = read_matlab()
    lena = json.loads(LENA_CI.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []

    for background_ues in (200, 230):
        completion = pick(matlab, background_ues, "delta_cohort_completion_fraction_packets")
        mean_delay = pick(matlab, background_ues, "cohort_completed_mean_latency_reduction_pct")
        p95_delay = pick(matlab, background_ues, "cohort_completed_p95_latency_reduction_pct")
        rows.append(
            {
                "backend": "MATLAB R2026a abstract PHY",
                "population": f"{background_ues + 8} UEs (single cell)",
                "baseline_pattern": "15 or 8 DL + 1 special + contiguous 4 or 11 UL slots",
                "delivery_endpoint": "finite-drain accepted packet-cohort completion",
                "delivery_delta": float(completion["mean_effect"]),
                "delivery_delta_ci95_low": float(completion["ci95_low"]),
                "delivery_delta_ci95_high": float(completion["ci95_high"]),
                "mean_delay_reduction_pct": float(mean_delay["mean_effect"]),
                "mean_delay_reduction_ci95_low": float(mean_delay["ci95_low"]),
                "mean_delay_reduction_ci95_high": float(mean_delay["ci95_high"]),
                "p95_delay_reduction_pct": float(p95_delay["mean_effect"]),
                "p95_delay_reduction_ci95_low": float(p95_delay["ci95_low"]),
                "p95_delay_reduction_ci95_high": float(p95_delay["ci95_high"]),
                "interpretation_boundary": "single-cell burst traffic; no explicit K2/control feasibility",
            }
        )

    metrics = lena["metrics"]
    delivery = metrics["delta_delivered_byte_fraction"]
    mean_delay = metrics["mean_delay_reduction_pct"]
    p95_delay = metrics["p95_delay_reduction_pct"]
    rows.append(
        {
            "backend": "5G-LENA nr v5.0",
            "population": "6 UEs (3 sectors, 2 per sector)",
            "baseline_pattern": "spread 4 versus spread 11 UL slots; one flexible slot",
            "delivery_endpoint": "receiver-window delivered byte fraction under calibration CBR",
            "delivery_delta": delivery["mean"],
            "delivery_delta_ci95_low": delivery["ci95_low"],
            "delivery_delta_ci95_high": delivery["ci95_high"],
            "mean_delay_reduction_pct": mean_delay["mean"],
            "mean_delay_reduction_ci95_low": mean_delay["ci95_low"],
            "mean_delay_reduction_ci95_high": mean_delay["ci95_high"],
            "p95_delay_reduction_pct": p95_delay["mean"],
            "p95_delay_reduction_ci95_low": p95_delay["ci95_low"],
            "p95_delay_reduction_ci95_high": p95_delay["ci95_high"],
            "interpretation_boundary": "explicit K2/control timing and sector geometry; different traffic endpoint",
        }
    )

    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
