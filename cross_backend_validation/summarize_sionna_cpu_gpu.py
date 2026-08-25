#!/usr/bin/env python3
"""Compare the fixed 1000-TB Sionna CPU/GPU execution pair."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SIONNA = ROOT / "results" / "sionna"


def one_row(directory: str) -> tuple[dict[str, str], dict]:
    with (SIONNA / directory / "summary.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"Expected one summary row in {directory}")
    metadata = json.loads(
        (SIONNA / directory / "metadata.json").read_text(encoding="utf-8")
    )
    return rows[0], metadata


def main() -> None:
    cpu, cpu_meta = one_row("mcs_13_cpu_1000tb")
    gpu, gpu_meta = one_row("mcs_13_gpu_1000tb")
    invariant_fields = (
        "snr_db_per_re",
        "num_seeds",
        "num_transport_blocks",
        "transport_block_bits",
    )
    if any(cpu[field] != gpu[field] for field in invariant_fields):
        raise ValueError("CPU and GPU experiment coordinates differ")

    cpu_runtime = float(cpu["runtime_seconds"])
    gpu_runtime = float(gpu["runtime_seconds"])
    trials = int(cpu["num_transport_blocks"])
    bits = trials * int(cpu["transport_block_bits"])
    payload = {
        "truth_label": "Sionna implementation reproducibility microbenchmark; not a radio-performance claim",
        "configuration": {
            "mcs_index": 13,
            "snr_db_per_re": float(cpu["snr_db_per_re"]),
            "seed": 1,
            "transport_blocks": trials,
            "transport_block_bits": int(cpu["transport_block_bits"]),
        },
        "cpu": {
            "device": cpu_meta["device"],
            "block_errors": int(cpu["block_errors"]),
            "bit_errors": int(cpu["bit_errors"]),
            "runtime_seconds": cpu_runtime,
        },
        "gpu": {
            "device": gpu_meta["device"],
            "block_errors": int(gpu["block_errors"]),
            "bit_errors": int(gpu["bit_errors"]),
            "runtime_seconds": gpu_runtime,
        },
        "comparison": {
            "block_error_counts_identical": cpu["block_errors"] == gpu["block_errors"],
            "absolute_bit_error_count_difference": abs(
                int(cpu["bit_errors"]) - int(gpu["bit_errors"])
            ),
            "absolute_ber_difference": abs(
                int(cpu["bit_errors"]) - int(gpu["bit_errors"])
            )
            / bits,
            "gpu_speedup_over_cpu": cpu_runtime / gpu_runtime,
        },
        "boundary": "GPU uses PTX JIT because TensorFlow 2.21 has no native sm_120 kernel image; small cold runs are slower than CPU, while this 1000-TB batch is faster.",
    }
    out = SIONNA / "cpu_gpu_consistency.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if not payload["comparison"]["block_error_counts_identical"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
