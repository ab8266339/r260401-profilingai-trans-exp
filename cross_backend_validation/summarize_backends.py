#!/usr/bin/env python3
"""Build the compact, truth-labelled cross-backend evidence matrix."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
MATLAB_LINK = (
    ROOT.parent
    / "matlab_nr_validation"
    / "pusch_link_validation"
    / "results"
    / "formal"
    / "summary.csv"
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def csv_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    system = csv_rows(RESULTS / "cross_backend_effects.csv")
    matlab_rows = [row for row in system if row["backend"].startswith("MATLAB")]
    lena = next(row for row in system if row["backend"].startswith("5G-LENA"))
    sionna = next(
        row
        for row in csv_rows(RESULTS / "sionna" / "mcs_13" / "summary.csv")
        if float(row["snr_db_per_re"]) == 9.0
    )
    sionna_exact = csv_rows(
        RESULTS / "sionna" / "mcs_13_matlab_snr_grid" / "summary.csv"
    )
    sionna_cpu_gpu = load_json(RESULTS / "sionna" / "cpu_gpu_consistency.json")
    matlab_link = next(
        row
        for row in csv_rows(MATLAB_LINK)
        if int(row["mcs_index"]) == 13 and float(row["snr_db"]) == 15.0
    )
    srs_tdd = load_json(RESULTS / "srsran_e2e" / "summary.json")
    protocol = load_json(RESULTS / "protocol_contract_summary.json")
    srs = protocol["backends"]["srsran_flexric"]
    ueransim = protocol["backends"]["ueransim"]
    oai = protocol["backends"]["oai"]
    oran_m = load_json(RESULTS / "oransc_m_ric" / "summary.json")
    nori = load_json(RESULTS / "nori_ns3_oransc_m" / "summary.json")
    nist = load_json(RESULTS / "nist_ns3_oran" / "summary.json")
    integer = load_json(
        ROOT.parent / "tier_b_simpy" / "results_integer_180_260" / "summary.json"
    )
    matlab_delivery = [float(row["delivery_delta"]) for row in matlab_rows]
    matlab_mean = [float(row["mean_delay_reduction_pct"]) for row in matlab_rows]
    matlab_p95 = [float(row["p95_delay_reduction_pct"]) for row in matlab_rows]
    exact_tb = sum(int(row["num_transport_blocks"]) for row in sionna_exact)
    exact_errors = sum(int(row["block_errors"]) for row in sionna_exact)

    rows = [
        {
            "layer": "system",
            "backend": "MATLAB R2026a",
            "status": "validated within the frozen abstract model",
            "result": (
                f"208 and 238 UEs: completion delta {min(matlab_delivery):.3f}--"
                f"{max(matlab_delivery):.3f}; mean reduction {min(matlab_mean):.1f}--"
                f"{max(matlab_mean):.1f}%; p95 reduction {min(matlab_p95):.1f}--"
                f"{max(matlab_p95):.1f}%"
            ),
            "paper_use": "main paired mMAS result",
            "boundary": "static UMi large-scale channel and abstract PHY; no explicit K2/DCI feasibility",
            "source": "results/cross_backend_effects.csv",
        },
        {
            "layer": "system",
            "backend": "5G-LENA nr v5.0",
            "status": "delivery/mean direction replicated; tail not resolved",
            "result": (
                f"5 paired seeds: delivery delta {float(lena['delivery_delta']):.3f}; "
                f"mean/p95 reductions {float(lena['mean_delay_reduction_pct']):.1f}%/"
                f"{float(lena['p95_delay_reduction_pct']):.1f}%"
            ),
            "paper_use": "independent NR timing robustness anchor",
            "boundary": "6 CBR flows in 3 sectors; p95 CI crosses zero; complete TDD/K2 pattern matters",
            "source": "results/5glena/paired_summary.json",
        },
        {
            "layer": "system",
            "backend": "SimPy all-integer control",
            "status": "compute-bottleneck boundary",
            "result": (
                "180--260 devices by one; median full-minus-baseline mean/p95 "
                f"changes {integer['grid_level']['median_delta_mean_ms']:+.1f}/"
                f"{integer['grid_level']['median_delta_p95_ms']:+.1f} ms; "
                f"median baseline GPU-queue share "
                f"{100*integer['grid_level']['median_baseline_gpu_queue_share']:.1f}%"
            ),
            "paper_use": "main bottleneck-boundary result",
            "boundary": "abstract discrete-event model; no NR/E2 execution or measured accelerator",
            "source": "../tier_b_simpy/results_integer_180_260/summary.json",
        },
        {
            "layer": "link",
            "backend": "Sionna 1.2.2",
            "status": "configuration/trend cross-check; absolute SNR disagrees",
            "result": (
                f"MCS13/14088-bit TB: BLER {float(sionna['initial_bler']):.3f} at 9 dB "
                f"versus MATLAB {float(matlab_link['initial_bler']):.3f} at 15 dB; "
                f"the exact MATLAB 12--17 dB grid produced {exact_errors}/{exact_tb} Sionna TB errors; "
                f"CPU/GPU block errors both {sionna_cpu_gpu['cpu']['block_errors']} and "
                f"1000-TB GPU speedup {sionna_cpu_gpu['comparison']['gpu_speedup_over_cpu']:.2f}x"
            ),
            "paper_use": "receiver/noise-model sensitivity only",
            "boundary": "normalized frequency-domain TDL-C with LS/LMMSE and no HARQ; do not merge SNR axes",
            "source": "results/sionna/mcs_13*/summary.csv",
        },
        {
            "layer": "SA protocol + O-RAN control",
            "backend": "srsRAN+srsUE+Open5GS+FlexRIC",
            "status": "Contract S and Contract E full pass",
            "result": (
                f"UE {srs['ue_ip']}; bidirectional user plane; "
                f"iperf3 UL {srs['observations']['iperf3_uplink_sender_mbps']:.1f} Mbit/s; "
                f"{srs['observations']['kpm_indication_count']} KPM indications with "
                f"UL PRB {srs['observations']['maximum_rru_prb_tot_ul_percent']}%; RC Style 2/Action 6 ACK"
            ),
            "paper_use": "main protocol/E2 feasibility evidence",
            "boundary": "compatibility-patched FlexRIC, ZMQ virtual RF, one UE; DRB throughput KPM unavailable",
            "source": "results/protocol_contract_summary.json",
        },
        {
            "layer": "SA protocol",
            "backend": "UERANSIM 3.3.0 + Open5GS",
            "status": "Contract S full pass",
            "result": f"UE {ueransim['ue_ip']}; NG/RRC/registration/PDU session and bidirectional user plane passed",
            "paper_use": "fast independent 5GC/session replication",
            "boundary": "abstract RAN/UE protocol emulator; no NR waveform or scheduler",
            "source": "results/protocol_contract_summary.json",
        },
        {
            "layer": "SA RAN protocol/PHY",
            "backend": "OpenAirInterface RFsimulator + Open5GS",
            "status": "Contract S full pass",
            "result": f"UE {oai['ue_ip']}; n78/30 kHz RFsim NG/RRC/registration/PDU session and bidirectional user plane passed",
            "paper_use": "deep independent SA protocol/PHY replication",
            "boundary": "RFsim software channel, one UE, highest startup/resource cost; not OTA",
            "source": "results/protocol_contract_summary.json",
        },
        {
            "layer": "O-RAN platform",
            "backend": "official ns-O-RAN + O-RAN SC M",
            "status": "compatibility-patched E2 Setup pass",
            "result": (
                f"{oran_m['observed']['sctp_connections_established']}/5 SCTP associations and "
                f"{oran_m['observed']['setup_response_success_decoded_by_simulator']}/5 successful Setup responses"
            ),
            "paper_use": "near-RT RIC platform-assembly evidence",
            "boundary": "two explicit service/RMR routing repairs; Setup only, no KPM/control/user plane",
            "source": "results/oransc_m_ric/summary.json",
        },
        {
            "layer": "RAN compatibility",
            "backend": "srsRAN+srsUE n78 TDD",
            "status": "negative compatibility control",
            "result": "gNB/AMF NG setup passed; installed srsUE rejected 30 kHz SCS during cell selection",
            "paper_use": "documents why OAI/5G-LENA carry the n78 TDD role",
            "boundary": f"configuration-specific gNB cell cap {srs_tdd['reported_cell_ue_upper_bound']} UEs",
            "source": "results/srsran_e2e/summary.json",
        },
        {
            "layer": "O-RAN control",
            "backend": "NORI ns-3.42 + O-RAN SC M",
            "status": "compatibility-patched E2 Setup pass",
            "result": "SCTP and successful E2 Setup response passed; RAN function IDs 200/300 accepted",
            "paper_use": "secondary compatibility control",
            "boundary": nori["claim_boundary"],
            "source": "results/nori_ns3_oransc_m/summary.json",
        },
        {
            "layer": "O-RAN control model",
            "backend": "NIST ns3-oran on ns-3.42",
            "status": "abstract RIC examples passed",
            "result": (
                f"{nist['observed']['commands_exit_zero']}/"
                f"{nist['observed']['commands_run']} repository/keep-alive examples exited zero"
            ),
            "paper_use": "model-semantics control",
            "boundary": "in-process abstract RIC; no SCTP/ASN.1 E2AP, NR, 5GC, or OTA",
            "source": "results/nist_ns3_oran/summary.json",
        },
    ]

    csv_path = RESULTS / "backend_evidence_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (RESULTS / "backend_evidence_matrix.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    print(f"Wrote {csv_path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
