#!/usr/bin/env python3
"""Summarize protocol-level O-RAN SC interoperability runs.

The parser keeps protocol evidence separate from radio-performance results.  It
does not infer over-the-air behavior, scheduler gain, or closed-loop stability.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("/home/hxu/ran-sims/oran-evidence/minimal_ric_20260717"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.evidence_root
    kpm_gnb = read(root / "srsran_kpm_load_gnb.log")
    kpm_ue = read(root / "srsran_kpm_load_ue.log")
    kpm_xapp = read(root / "xapp_kpm_load.log")
    rc_gnb = read(root / "srsran_rc_gnb.log")
    rc_ue = read(root / "srsran_rc_ue.log")
    rc_xapp = read(root / "xapp_rc.log")
    nsoran = read(root / "nsoran_scenario_zero.log")
    e2term = read(root / "ric_e2term.log")

    function_matches = re.findall(
        r"Added supported RAN function with id (\d+) and OID ([0-9.]+)",
        kpm_gnb,
    )
    max_ratios = [
        int(value)
        for value in re.findall(r"Max PRB Policy Ratio:\s*(\d+)", rc_gnb)
    ]
    kpm_values = re.findall(
        r"--Metric:\s*([^,]+(?:\.[^,]+)*), Value:\s*(\[[^\]]*\])",
        kpm_xapp,
    )
    result = {
        "truth_type": "protocol-level virtual-RF interoperability",
        "over_the_air": False,
        "minimal_oran_sc_ric": {
            "srsran_e2_setup_success": "E2 Setup procedure successful." in kpm_gnb,
            "ran_functions": [
                {"id": int(function_id), "oid": oid}
                for function_id, oid in function_matches
            ],
            "kpm_subscription_success": "Successfully subscribed" in kpm_xapp,
            "kpm_indication_count": count(r"^RIC Indication Received", kpm_xapp),
            "kpm_metric_values": sorted(
                {f"{metric.strip()}={value}" for metric, value in kpm_values}
            ),
            "kpm_nonzero_value_seen": any(
                value not in {"[0]", "[0.0]"} for _, value in kpm_values
            ),
            "ue_rrc_connected": "RRC Connected" in kpm_ue,
            "ue_authentication_success": "Network authentication successful" in kpm_ue,
            "ue_pdu_session_success": "PDU Session Establishment successful" in kpm_ue,
            "rc_requests_decoded": count(r"Received RIC Control Request", rc_gnb),
            "rc_acknowledgements_at_gnb": count(
                r"Sending E2 RIC Control Acknowledge", rc_gnb
            ),
            "rc_acknowledgements_at_xapp": count(
                r"^Received RIC_CONTROL_ACK", rc_xapp
            ),
            "rc_max_prb_ratios": max_ratios,
            "rc_run_ue_pdu_session_success": (
                "PDU Session Establishment successful" in rc_ue
            ),
        },
        "official_ns_o_ran_against_i_release_ric": {
            "sctp_connections": count(r"\[SCTP\] Connection established", nsoran),
            "e2_setup_requests_sent": count(r"Sent E2-SETUP-REQUEST", nsoran),
            "e2_setup_response_seen": "E2-SETUP-RESPONSE" in nsoran,
            "ric_decode_errors": count(r"Error 2 Decoding .* E2AP PDU", e2term),
            "interpretation": (
                "The official M-release simulator opened SCTP associations and sent "
                "E2 setup requests, but the I-release RIC could not decode them.  This "
                "is a release-interoperability boundary, not a radio failure."
            ),
        },
        "claim_boundary": (
            "The run supports E2 setup, KPM subscription and indication delivery, "
            "and E2SM-RC request/acknowledgement behavior over virtual RF.  It does "
            "not support an over-the-air, control-performance, or stability claim."
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
