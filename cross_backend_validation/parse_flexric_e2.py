#!/usr/bin/env python3
"""Extract E2 and KPM subscription milestones conservatively."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def found(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def read_clean(path: Path) -> str:
    """Read a log while removing sparse-file NUL padding from repeated runs."""
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ric-log", type=Path, required=True)
    parser.add_argument("--gnb-log", type=Path, required=True)
    parser.add_argument("--xapp-log", type=Path, required=True)
    parser.add_argument("--xapp-exit-status", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ric = read_clean(args.ric_log)
    gnb = read_clean(args.gnb_log)
    xapp = read_clean(args.xapp_log)
    both = ric + "\n" + gnb + "\n" + xapp
    summary = {
        "truth_label": "emulated O-RAN E2 control plane",
        "over_the_air": False,
        "xapp_exit_status": args.xapp_exit_status,
        "ric_initialized": found(ric, [r"nearRT-RIC.*Initializing", r"Loading SM"]),
        "kpm_service_model_loaded": "ORAN-E2SM-KPM" in ric,
        "rc_service_model_loaded": "ORAN-E2SM-RC" in ric,
        "e2_setup_request": found(
            both, [r"E2 Setup Request", r"E2SetupRequest", r"E2 SETUP-REQUEST"]
        ),
        "e2_setup_response": found(both, [r"E2 Setup Response", r"E2SetupResponse"]),
        "e2_node_connected": found(
            both, [r"E2 Node.*connected", r"Added E2 Node", r"E2 setup.*success"]
        ),
        "kpm_xapp_initialized": found(xapp, [r"xApp.*Initializing", r"KPM"]),
        "xapp_e42_connected": found(ric, [r"E42 SETUP-REQUEST"]),
        "kpm_subscription_request": found(
            both, [r"Subscription Request", r"RICsubscriptionRequest"]
        ),
        "kpm_subscription_response": found(
            both, [r"Subscription Response", r"RICsubscriptionResponse"]
        ),
        "kpm_indication_emitted_by_e2_node": found(
            gnb, [r"Sending E2 indication"]
        ),
        "kpm_indication_received_by_xapp": found(
            xapp,
            [
                r"KPM ind_msg latency",
                r"KPM.*indication",
                r"RIC Indication",
                r"meas.*value",
            ],
        ),
        "xapp_crashed": args.xapp_exit_status in (134, 139),
        "compatibility_guard_exercised": found(
            xapp,
            [
                r"Skipping unsupported KPM report style",
                r"Skipping KPM measurement without a numeric value",
            ],
        ),
        "failure_markers": sorted(
            {
                line.strip()
                for line in both.splitlines()
                if re.search(r"assertion|\bfatal\b|\bfailed\b|\berror\b", line, re.I)
            }
        )[:40],
        "ric_log": str(args.ric_log),
        "gnb_log": str(args.gnb_log),
        "xapp_log": str(args.xapp_log),
    }
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
