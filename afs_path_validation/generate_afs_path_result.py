"""Reproduce the synthetic AFS-C uplink-payload comparison.

This script isolates the path-level experiment that originally appeared in
``paper/gen_supplementary.py::fig_afs_comparison``.  It intentionally models
serialized application payload only.  It does not model latency, radio access,
or measured traffic.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SCRIPT_PATH = Path(__file__).resolve()
OUTPUT_DIR = SCRIPT_PATH.parent
PROJECT_ROOT = SCRIPT_PATH.parents[2]
FIGURE_DIR = PROJECT_ROOT / "paper" / "figures"
DESIGN_REFERENCE = PROJECT_ROOT / "paper" / "gen_supplementary.py"

CSV_PATH = OUTPUT_DIR / "afs_path_aggregate.csv"
JSON_PATH = OUTPUT_DIR / "afs_path_aggregate.json"
MANIFEST_PATH = OUTPUT_DIR / "afs_path_manifest.json"
PDF_PATH = FIGURE_DIR / "fig_afs_traffic.pdf"
PNG_PATH = FIGURE_DIR / "fig_afs_traffic.png"

SEED = 42
DEPTHS = (2, 3, 4, 5, 6, 8, 10)
PATHS_PER_DEPTH = 10_000
BYTES_PER_TOKEN = 4.0
LOGNORMAL_LOG_MEAN = 5.0
LOGNORMAL_LOG_STD = 1.5
TOKEN_CAP = 2_048.0
INCREMENT_LOW = 25.0
INCREMENT_HIGH = 75.0


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sample_digest(values: np.ndarray) -> str:
    """Hash raw float64 samples without persisting a bulky path-level table."""

    canonical = np.asarray(values, dtype="<f8")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


def summarize(values_bytes: np.ndarray, prefix: str) -> dict[str, float]:
    """Summarize one payload series in kibibytes."""

    values_kib = values_bytes / 1024.0
    mean = float(np.mean(values_kib))
    std = float(np.std(values_kib, ddof=1))
    stderr = std / np.sqrt(values_kib.size)
    return {
        f"{prefix}_mean_kib": mean,
        f"{prefix}_std_kib": std,
        f"{prefix}_stderr_kib": float(stderr),
        f"{prefix}_ci95_low_kib": float(mean - 1.96 * stderr),
        f"{prefix}_ci95_high_kib": float(mean + 1.96 * stderr),
        f"{prefix}_p25_kib": float(np.percentile(values_kib, 25)),
        f"{prefix}_median_kib": float(np.percentile(values_kib, 50)),
        f"{prefix}_p75_kib": float(np.percentile(values_kib, 75)),
    }


def run_experiment() -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Run the historical synthetic design with its original random stream."""

    rng = np.random.RandomState(SEED)
    rows: list[dict[str, Any]] = []
    raw_digests: dict[str, dict[str, str]] = {}

    for depth in DEPTHS:
        full_history = np.empty(PATHS_PER_DEPTH, dtype=np.float64)
        afs_c_incremental = np.empty(PATHS_PER_DEPTH, dtype=np.float64)

        for trial in range(PATHS_PER_DEPTH):
            initial_tokens = min(
                rng.lognormal(LOGNORMAL_LOG_MEAN, LOGNORMAL_LOG_STD), TOKEN_CAP
            )
            stage_outputs = np.minimum(
                rng.lognormal(LOGNORMAL_LOG_MEAN, LOGNORMAL_LOG_STD, depth),
                TOKEN_CAP,
            )

            cumulative_inputs = [initial_tokens]
            for stage in range(1, depth):
                cumulative_inputs.append(
                    initial_tokens + sum(stage_outputs[:stage])
                )

            full_history[trial] = BYTES_PER_TOKEN * sum(cumulative_inputs)
            afs_c_incremental[trial] = (
                BYTES_PER_TOKEN * cumulative_inputs[0]
                + sum(
                    BYTES_PER_TOKEN
                    * rng.uniform(INCREMENT_LOW, INCREMENT_HIGH)
                    for _ in range(depth - 1)
                )
            )

        full_summary = summarize(full_history, "full_history")
        afs_summary = summarize(afs_c_incremental, "afs_c_incremental")
        full_mean = full_summary["full_history_mean_kib"]
        afs_mean = afs_summary["afs_c_incremental_mean_kib"]
        ratio = afs_mean / full_mean

        row: dict[str, Any] = {
            "depth": depth,
            "paths": PATHS_PER_DEPTH,
            **full_summary,
            **afs_summary,
            "ratio_of_means": ratio,
            "reduction_percent": 100.0 * (1.0 - ratio),
        }
        rows.append(row)
        raw_digests[str(depth)] = {
            "full_history_float64_sha256": sample_digest(full_history),
            "afs_c_incremental_float64_sha256": sample_digest(afs_c_incremental),
        }

    return rows, raw_digests


def verify_reference_point(rows: list[dict[str, Any]]) -> None:
    """Fail loudly if a code change breaks the historical depth-eight result."""

    target = next(row for row in rows if row["depth"] == 8)
    actual = (
        round(target["full_history_mean_kib"], 1),
        round(target["afs_c_incremental_mean_kib"], 1),
        round(target["reduction_percent"], 1),
    )
    expected = (49.9, 2.7, 94.5)
    if actual != expected:
        raise RuntimeError(
            f"depth-eight acceptance check failed: {actual} != {expected}"
        )


def write_aggregate_csv(rows: list[dict[str, Any]]) -> None:
    """Write the machine-readable aggregate table."""

    with CSV_PATH.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def experiment_metadata() -> dict[str, Any]:
    """Describe the synthetic design without implying a measured result."""

    return {
        "truth_type": "synthetic Monte Carlo path model",
        "scope": "serialized application-layer uplink payload only",
        "exclusions": [
            "latency",
            "radio channel",
            "protocol headers",
            "retransmissions",
            "compute service",
            "measured or operational traffic",
        ],
        "random_stream": {
            "generator": "numpy.random.RandomState",
            "seed": SEED,
            "ordering": "one stream reused sequentially across ascending depths",
        },
        "depths": list(DEPTHS),
        "paths_per_depth": PATHS_PER_DEPTH,
        "bytes_per_token": BYTES_PER_TOKEN,
        "initial_and_stage_output_tokens": {
            "distribution": "capped lognormal",
            "log_mean": LOGNORMAL_LOG_MEAN,
            "log_standard_deviation": LOGNORMAL_LOG_STD,
            "cap_tokens": TOKEN_CAP,
        },
        "conservative_replay_baseline": (
            "each stage sends the initial prompt plus accumulated outputs; "
            "later instruction bytes are omitted"
        ),
        "afs_c_incremental": {
            "stage_1": "send the initial input",
            "later_stages": "send an independent uniform token increment",
            "increment_tokens_low": INCREMENT_LOW,
            "increment_tokens_high": INCREMENT_HIGH,
        },
        "comparison_metric": (
            "ratio of the two sample means at each depth"
        ),
    }


def write_aggregate_json(rows: list[dict[str, Any]]) -> None:
    """Write metadata and aggregate rows as JSON."""

    payload = {
        "schema_version": 1,
        "experiment": experiment_metadata(),
        "rows": rows,
    }
    JSON_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def draw_figure(rows: list[dict[str, Any]]) -> None:
    """Draw a single-column IEEE-ready comparison with no latency claim."""

    depths = np.array([row["depth"] for row in rows])
    full_means = np.array([row["full_history_mean_kib"] for row in rows])
    afs_means = np.array([row["afs_c_incremental_mean_kib"] for row in rows])
    target = next(row for row in rows if row["depth"] == 8)

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8.0,
            "axes.labelsize": 8.0,
            "xtick.labelsize": 7.2,
            "ytick.labelsize": 7.2,
            "legend.fontsize": 7.0,
            "axes.linewidth": 0.7,
            "lines.linewidth": 1.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=(3.48, 2.42))
    full_color = "#C44E00"
    afs_color = "#0072B2"

    ax.plot(
        depths,
        full_means,
        marker="o",
        markersize=4.2,
        color=full_color,
        markerfacecolor="white",
        markeredgewidth=1.0,
        label="Conservative replay baseline",
        zorder=3,
    )
    ax.plot(
        depths,
        afs_means,
        marker="s",
        markersize=4.0,
        color=afs_color,
        markerfacecolor=afs_color,
        label="AFS-C incremental uplink",
        zorder=3,
    )

    ax.set_yscale("log")
    ax.set_xlim(1.7, 10.3)
    ax.set_ylim(0.85, 130)
    ax.set_xticks(depths)
    ax.set_xlabel("Pipeline depth $d$")
    ax.set_ylabel("Mean uplink payload per path (KiB)")
    ax.grid(axis="y", which="major", color="#D0D0D0", linewidth=0.55)
    ax.grid(axis="y", which="minor", color="#E8E8E8", linewidth=0.35)
    ax.tick_params(direction="out", length=2.5, width=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.axvline(8, color="#777777", linewidth=0.7, linestyle=(0, (2, 2)), zorder=1)
    ax.scatter(
        [8, 8],
        [target["full_history_mean_kib"], target["afs_c_incremental_mean_kib"]],
        s=21,
        facecolors="white",
        edgecolors=[full_color, afs_color],
        linewidths=1.1,
        zorder=4,
    )

    ax.annotate(
        f'{target["full_history_mean_kib"]:.1f} KiB',
        xy=(8, target["full_history_mean_kib"]),
        xytext=(8, -9),
        textcoords="offset points",
        ha="left",
        va="top",
        color="#333333",
        fontsize=7.0,
    )
    ax.annotate(
        f'{target["afs_c_incremental_mean_kib"]:.1f} KiB',
        xy=(8, target["afs_c_incremental_mean_kib"]),
        xytext=(-7, -8),
        textcoords="offset points",
        ha="right",
        va="top",
        color="#333333",
        fontsize=7.0,
    )
    ax.text(
        8.22,
        9.5,
        f'{target["reduction_percent"]:.1f}% lower\n(mean ratio)',
        ha="left",
        va="center",
        fontsize=6.8,
        color="#333333",
    )

    ax.legend(
        loc="upper left",
        frameon=False,
        handlelength=2.0,
        borderaxespad=0.25,
        labelspacing=0.35,
    )
    fig.tight_layout(pad=0.45)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PDF_PATH, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(PNG_PATH, dpi=600, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def write_manifest(
    rows: list[dict[str, Any]], raw_digests: dict[str, dict[str, str]]
) -> None:
    """Record parameters, versions, source lineage, and artifact hashes."""

    target = next(row for row in rows if row["depth"] == 8)
    artifacts = [CSV_PATH, JSON_PATH, PDF_PATH, PNG_PATH]
    manifest = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "truth_type": "synthetic Monte Carlo path model",
        "experiment": experiment_metadata(),
        "acceptance_check_depth_8": {
            "full_history_mean_kib": target["full_history_mean_kib"],
            "afs_c_incremental_mean_kib": target["afs_c_incremental_mean_kib"],
            "reduction_percent_ratio_of_means": target["reduction_percent"],
            "expected_rounded_values": {
                "full_history_mean_kib": 49.9,
                "afs_c_incremental_mean_kib": 2.7,
                "reduction_percent_ratio_of_means": 94.5,
            },
        },
        "lineage": {
            "design_reference": {
                "path": DESIGN_REFERENCE.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": sha256_file(DESIGN_REFERENCE),
                "function": "fig_afs_comparison",
            },
            "generator": {
                "path": SCRIPT_PATH.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": sha256_file(SCRIPT_PATH),
            },
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "platform": platform.platform(),
        },
        "raw_sample_digests": raw_digests,
        "artifacts": {
            path.relative_to(PROJECT_ROOT).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in artifacts
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Generate the aggregate artifacts and publication figure."""

    rows, raw_digests = run_experiment()
    verify_reference_point(rows)
    write_aggregate_csv(rows)
    write_aggregate_json(rows)
    draw_figure(rows)
    write_manifest(rows, raw_digests)

    target = next(row for row in rows if row["depth"] == 8)
    print(
        "d=8: "
        f'conservative-baseline={target["full_history_mean_kib"]:.3f} KiB, '
        f'AFS-C={target["afs_c_incremental_mean_kib"]:.3f} KiB, '
        f'reduction={target["reduction_percent"]:.3f}% (ratio of means)'
    )
    print(f"wrote {CSV_PATH}")
    print(f"wrote {JSON_PATH}")
    print(f"wrote {MANIFEST_PATH}")
    print(f"wrote {PDF_PATH}")
    print(f"wrote {PNG_PATH}")


if __name__ == "__main__":
    main()
