#!/usr/bin/env python3
"""Extract conservative milestones from the OAI RFsimulator DORA smoke."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def any_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def oai_time(text: str, marker: str):
    match = re.search(
        rf"^(\d+\.\d+) .*{marker}", text, flags=re.IGNORECASE | re.MULTILINE
    )
    return float(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gnb-log", type=Path, required=True)
    parser.add_argument("--ue-log", type=Path, required=True)
    parser.add_argument("--ue-exit-status", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gnb = args.gnb_log.read_text(encoding="utf-8", errors="replace")
    ue = args.ue_log.read_text(encoding="utf-8", errors="replace")
    both = gnb + "\n" + ue
    gnb_ra_start = oai_time(gnb, r"Initiating RA procedure with preamble")
    gnb_ra_success = oai_time(gnb, r"CFRA procedure succeeded")
    ue_prach = oai_time(ue, r"placing PRACH")
    ue_ra_success = oai_time(ue, r"RA procedure succeeded")
    rnti_match = re.search(r"CFRA process for UE RNTI ([0-9a-f]+)", gnb, re.I)
    ul_rows = re.findall(
        r"ulsch_rounds (\d+)/(\d+)/(\d+)/(\d+), ulsch_errors (\d+)", gnb
    )
    summary = {
        "truth_label": "emulated OAI RAN over RFsimulator",
        "over_the_air": False,
        "core_network": "none (--noS1 DORA smoke)",
        "ue_exit_status": args.ue_exit_status,
        "gnb_initialized": any_pattern(
            gnb, [r"gNB.*started", r"Waiting for.*connection", r"rfsimulator"]
        ),
        "nrue_initialized": any_pattern(
            ue, [r"Initializing MAC", r"Starting NR UE soft modem", r"rfsimulator"]
        ),
        "ssb_synchronized": any_pattern(
            ue, [r"sync.*success", r"PBCH.*decoded", r"Synchronized"]
        ),
        "random_access_started": any_pattern(
            both, [r"RA procedure", r"Random Access", r"preamble.*transmit", r"Msg1"]
        ),
        "random_access_completed": any_pattern(
            both, [r"RA.*success", r"Random Access.*complete", r"Msg4.*received"]
        ),
        "band": "n78",
        "subcarrier_spacing_khz": 30,
        "carrier_resource_blocks": 106,
        "duplex_mode": "TDD",
        "tdd_period_slots": 10,
        "full_dl_slots_per_period": 7,
        "flexible_slots_per_period": 1,
        "full_ul_slots_per_period": 2,
        "temporary_rnti": rnti_match.group(1) if rnti_match else None,
        "gnb_ra_start_to_success_ms": (
            (gnb_ra_success - gnb_ra_start) * 1000.0
            if gnb_ra_start is not None and gnb_ra_success is not None
            else None
        ),
        "ue_prach_to_ra_success_ms": (
            (ue_ra_success - ue_prach) * 1000.0
            if ue_prach is not None and ue_ra_success is not None
            else None
        ),
        "last_ulsch_rounds": (
            [int(value) for value in ul_rows[-1][:4]] if ul_rows else None
        ),
        "last_ulsch_errors": int(ul_rows[-1][4]) if ul_rows else None,
        "failure_markers": sorted(
            {
                line.strip()
                for line in both.splitlines()
                if re.search(
                    r"\]\s+E\s|assertion|\bfatal\b|\bfailed to\b",
                    line,
                    flags=re.IGNORECASE,
                )
            }
        )[:40],
        "gnb_log": str(args.gnb_log),
        "ue_log": str(args.ue_log),
    }
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
