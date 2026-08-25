#!/usr/bin/env python3
"""Machine-check Contract S/E evidence from the saved protocol-stack logs.

The checks deliberately use both endpoints when a protocol exchange exposes
them.  They report only virtual-RF/software-channel evidence and never infer
over-the-air behavior.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def read(relative: str) -> str:
    path = RESULTS / relative
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def contains(text: str, *needles: str) -> bool:
    folded = text.casefold()
    return all(needle.casefold() in folded for needle in needles)


def ping_ok(relative: str) -> bool:
    text = read(relative)
    match = re.search(
        r"(\d+) packets transmitted,\s*(\d+) received,\s*([0-9.]+)% packet loss",
        text,
    )
    return bool(match and int(match.group(1)) >= 4 and match.group(1) == match.group(2))


def ue_ip(*texts: str) -> str | None:
    for text in texts:
        matches = re.findall(
            r"(?:UE IPv4:|IP:\s*|ue_ip=|uesimtun0,\s*)(10\.45\.\d+\.\d+)", text
        )
        if matches:
            return matches[-1]
    return None


def iperf_sender_mbps(text: str) -> float | None:
    matches = re.findall(
        r"\d+(?:\.\d+)?\s+MBytes\s+([0-9.]+)\s+Mbits/sec\s+\d+\s+sender",
        text,
    )
    return float(matches[-1]) if matches else None


def stage_summary(stages: dict[str, bool]) -> dict[str, object]:
    return {
        "stages": stages,
        "passed_stage_count": sum(stages.values()),
        "required_stage_count": len(stages),
        "full_pass": all(stages.values()),
    }


def srsran_flexric() -> dict[str, object]:
    gnb = read("flexric_rc_sa/gnb.log")
    ue = read("flexric_rc_sa/srsue.log")
    network = read("flexric_rc_sa/network_state.log")
    kpm = read("flexric_rc_sa/xapp_kpm_live.log")
    rc = read("flexric_rc_sa/xapp_rc_slice.log")
    iperf = read("flexric_rc_sa/iperf_uplink.log")

    s = stage_summary(
        {
            "ng_setup": contains(gnb, "NGSetupResponse"),
            "cell_acquisition": contains(ue, "SIB1 acquired successfully"),
            "rrc_connection": contains(ue, "Finished Connection Setup successfully"),
            "authentication_and_registration_accept": contains(ue, "Handling Registration Accept"),
            "pdu_session": contains(ue, "PDU Session Establishment successful"),
            "assigned_ue_address": ue_ip(ue, network) is not None,
            "uplink_user_plane": ping_ok("flexric_rc_sa/ping_uplink.log"),
            "downlink_user_plane": ping_ok("flexric_rc_sa/ping_downlink.log"),
        }
    )
    indications = len(re.findall(r"KPM ind_msg", kpm))
    prb_ul = [int(value) for value in re.findall(r"RRU\.PrbTotUl\s*=\s*(\d+)", kpm)]
    e = stage_summary(
        {
            "sctp_and_e2_setup": contains(gnb, "E2 Setup procedure successful"),
            "kpm_subscription_response": contains(kpm, "SUBSCRIPTION RESPONSE rx"),
            "decoded_kpm_indication": indications > 0,
            "nonzero_load_visible_in_kpm": bool(prb_ul and max(prb_ul) > 0),
            "rc_request_at_xapp": contains(rc, "CONTROL-REQUEST tx"),
            "rc_request_at_ran": contains(gnb, "Received RIC Control Request"),
            "rc_ack_at_ran": contains(gnb, "Sending E2 RIC Control Acknowledge"),
            "rc_ack_at_xapp": contains(rc, "CONTROL ACK rx"),
        }
    )
    return {
        "software": "srsRAN Project 25.10 + srsUE + Open5GS 2.8.0 + FlexRIC",
        "truth_label": "protocol implementation over ZMQ virtual RF; not OTA",
        "ue_ip": ue_ip(ue, network),
        "contract_s": s,
        "contract_e": e,
        "observations": {
            "kpm_indication_count": indications,
            "maximum_rru_prb_tot_ul_percent": max(prb_ul) if prb_ul else None,
            "iperf3_uplink_sender_mbps": iperf_sender_mbps(iperf),
            "rc_style": 2,
            "rc_action": 6,
        },
        "compatibility_repairs": [
            "FlexRIC KPM sample guards unsupported/no-value records",
            "FlexRIC RC decoder ignores an advertised optional call-process-ID capability that its model does not consume",
        ],
        "boundary": "RRU/PRB KPM reflects load; the exposed DRB throughput fields remained unavailable/zero and are not used as zero-throughput evidence.",
    }


def ueransim() -> dict[str, object]:
    gnb = read("ueransim_sa/gnb.log")
    ue = read("ueransim_sa/ue.log")
    network = read("ueransim_sa/network_state.log")
    s = stage_summary(
        {
            "ng_setup": contains(gnb, "NG Setup procedure is successful"),
            "cell_acquisition": contains(ue, "Selected cell"),
            "rrc_connection": contains(ue, "RRC connection established"),
            "authentication_and_registration_accept": contains(ue, "Registration accept received"),
            "pdu_session": contains(ue, "PDU Session establishment is successful"),
            "assigned_ue_address": ue_ip(ue, network) is not None,
            "uplink_user_plane": ping_ok("ueransim_sa/ping_uplink.log"),
            "downlink_user_plane": ping_ok("ueransim_sa/ping_downlink.log"),
        }
    )
    return {
        "software": "UERANSIM 3.3.0 + Open5GS 2.8.0",
        "truth_label": "abstract 5G RAN/UE protocol emulator; not an NR PHY and not OTA",
        "ue_ip": ue_ip(ue, network),
        "contract_s": s,
        "strength": "fastest reproducible SA control/user-plane smoke test with a native per-session namespace",
        "boundary": "does not validate waveform, coding, scheduler, K2, beamforming, or radio propagation",
    }


def oai() -> dict[str, object]:
    gnb = read("oai_open5gs_sa/gnb.log")
    ue = read("oai_open5gs_sa/nrue.log")
    network = read("oai_open5gs_sa/network_state.log")
    s = stage_summary(
        {
            "ng_setup": contains(gnb, "Received NGSetupResponse from AMF"),
            "cell_acquisition": contains(ue, "SIB1 decoded"),
            "rrc_connection": contains(ue, "State = NR_RRC_CONNECTED"),
            "authentication_and_registration_accept": contains(ue, "Received Registration Accept"),
            "pdu_session": contains(ue, "Received PDU Session Establishment Accept"),
            "assigned_ue_address": ue_ip(ue, network) is not None,
            "uplink_user_plane": ping_ok("oai_open5gs_sa/ping_uplink.log"),
            "downlink_user_plane": ping_ok("oai_open5gs_sa/ping_downlink.log"),
        }
    )
    return {
        "software": "OpenAirInterface develop 70508eb + Open5GS 2.8.0",
        "truth_label": "protocol/PHY implementation over RFsimulator; not OTA",
        "ue_ip": ue_ip(ue, network),
        "contract_s": s,
        "strength": "deepest RRC/NAS/PDCP/RLC/MAC/PHY visibility among the three SA backends",
        "boundary": "highest startup/resource cost and a software channel rather than measured RF",
    }


def oransc() -> dict[str, object]:
    summary = json.loads(read("oransc_m_ric/summary.json"))
    observed = summary["observed"]
    return {
        "software": "O-RAN SC M near-RT RIC platform + official ns-O-RAN scenario-zero",
        "truth_label": summary["truth_type"] + "; not OTA",
        "contract_e_level": observed["contract_e_level"],
        "setup_pass": all(
            observed[key] == 5
            for key in (
                "sctp_connections_established",
                "e2_setup_requests_sent",
                "e2_setup_requests_received",
                "e2_setup_requests_decoded",
                "e2_setup_responses_seen_by_simulator",
                "setup_response_success_decoded_by_simulator",
            )
        )
        and observed["process_exit_status"] == 0,
        "compatibility_repairs": summary["compatibility_repairs"],
        "boundary": summary["claim_boundary"],
    }


def main() -> None:
    backends = {
        "srsran_flexric": srsran_flexric(),
        "ueransim": ueransim(),
        "oai": oai(),
        "oransc_m": oransc(),
    }
    required = {
        "srsran_contract_s": bool(backends["srsran_flexric"]["contract_s"]["full_pass"]),
        "srsran_flexric_contract_e": bool(backends["srsran_flexric"]["contract_e"]["full_pass"]),
        "ueransim_contract_s": bool(backends["ueransim"]["contract_s"]["full_pass"]),
        "oai_contract_s": bool(backends["oai"]["contract_s"]["full_pass"]),
        "oransc_m_setup": bool(backends["oransc_m"]["setup_pass"]),
    }
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "contract_source": "CROSS_STACK_CONTRACT_20260718.md",
        "all_required_checks_pass": all(required.values()),
        "required_checks": required,
        "backends": backends,
    }
    out_json = RESULTS / "protocol_contract_summary.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with (RESULTS / "protocol_contract_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["check", "pass"])
        writer.writeheader()
        writer.writerows({"check": key, "pass": value} for key, value in required.items())

    for key, value in required.items():
        print(f"{key}: {'PASS' if value else 'FAIL'}")
    print(f"all_required_checks_pass={all(required.values())}")
    if not all(required.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
