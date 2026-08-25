#!/usr/bin/env python3
"""Extract conservative protocol milestones from local srsRAN/Open5GS logs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


def present(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def event_time(text: str, marker: str):
    match = re.search(
        rf"^(\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}\.\d+) .*{marker}",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return datetime.fromisoformat(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gnb-log", type=Path, required=True)
    parser.add_argument("--ue-log", type=Path, required=True)
    parser.add_argument("--ue-exit-status", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gnb = args.gnb_log.read_text(encoding="utf-8", errors="replace")
    ue = args.ue_log.read_text(encoding="utf-8", errors="replace")
    registration_request = event_time(ue, r"Sending Registration Request")
    registration_accept = event_time(ue, r"Handling Registration Accept")
    pdu_success = event_time(ue, r"PDU Session Establishment successful")
    ip_match = re.search(
        r"PDU Session Establishment successful\. IP: ([0-9.]+)", ue
    )
    limit_match = re.search(r"upper-bound on the number of UEs.*?is (\d+)", gnb)
    duplex_match = re.search(r"duplex=(fdd|tdd)", ue, flags=re.IGNORECASE)
    summary = {
        "truth_label": "emulated protocol stack over ZMQ virtual RF",
        "over_the_air": False,
        "ue_exit_status": args.ue_exit_status,
        "gnb_started": "==== gNB started ===" in gnb,
        "ng_setup_request": "NGSetupRequest" in gnb,
        "ng_setup_response": "NGSetupResponse" in gnb,
        "amf_connected": "Connected to AMF" in gnb,
        "zmq_tx_bound": "Binding to address tcp://*:2000" in gnb,
        "zmq_rx_connected": "Connecting to address tcp://localhost:2001" in gnb,
        "ue_detected_cell": present(
            ue,
            [r"found.*cell", r"cell.*found", r"cell search.*success", r"camped"],
        ),
        "ue_rrc_connected": present(
            ue,
            [r"rrc connected", r"rrc connection.*complete", r"connected state"],
        ),
        "ue_registered": present(
            ue,
            [r"registration.*accept", r"network registration.*success", r"attach.*accept"],
        ),
        "pdu_session_established": present(
            ue,
            [
                r"pdu session.*accept",
                r"pdu session.*established",
                r"pdu session establishment successful",
                r"network attach successful",
            ],
        ),
        "assigned_ipv4": ip_match.group(1) if ip_match else None,
        "duplex_mode": duplex_match.group(1).upper() if duplex_match else None,
        "reported_cell_ue_upper_bound": int(limit_match.group(1)) if limit_match else None,
        "registration_request_to_accept_ms": (
            (registration_accept - registration_request).total_seconds() * 1000.0
            if registration_request and registration_accept
            else None
        ),
        "registration_request_to_pdu_session_ms": (
            (pdu_success - registration_request).total_seconds() * 1000.0
            if registration_request and pdu_success
            else None
        ),
        "failure_markers": sorted(
            {
                line.strip()
                for line in (gnb + "\n" + ue).splitlines()
                if re.search(
                    r"error|failed|assertion|unsupported|registration reject",
                    line,
                    flags=re.IGNORECASE,
                )
            }
        )[:30],
        "gnb_log": str(args.gnb_log),
        "ue_log": str(args.ue_log),
    }
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
