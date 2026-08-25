#!/usr/bin/env python3
"""Small, auditable Sionna PUSCH cross-check for the MATLAB link anchor.

This is a PHY-only experiment.  It estimates first-transmission TB BLER and BER
under a normalized TDL-C channel; it does not simulate HARQ, queues, TDD control,
or a population of UEs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import tensorflow as tf
import sionna
from sionna.phy import config
from sionna.phy.channel import OFDMChannel
from sionna.phy.channel.tr38901 import TDL
from sionna.phy.nr import PUSCHConfig, PUSCHReceiver, PUSCHTransmitter


DEFAULT_SNRS = (4.0, 6.0, 8.0)
DEFAULT_SEEDS = (1, 2, 3)


def build_chain(mcs_index: int):
    pusch = PUSCHConfig()
    pusch.carrier.n_cell_id = 1
    pusch.carrier.subcarrier_spacing = 30
    pusch.carrier.n_size_grid = 51
    pusch.n_size_bwp = 51
    pusch.num_layers = 1
    pusch.num_antenna_ports = 1
    pusch.mapping_type = "A"
    pusch.symbol_allocation = [0, 14]
    pusch.precoding = "non-codebook"
    pusch.transform_precoding = False
    pusch.n_rnti = 1
    pusch.tb.mcs_table = 1
    pusch.tb.mcs_index = mcs_index
    pusch.dmrs.config_type = 1
    pusch.dmrs.type_a_position = 2
    pusch.dmrs.additional_position = 1
    pusch.dmrs.length = 1
    pusch.dmrs.num_cdm_groups_without_data = 2
    pusch.check_config()

    transmitter = PUSCHTransmitter(
        pusch, return_bits=True, output_domain="freq", precision="single"
    )
    receiver = PUSCHReceiver(
        transmitter,
        return_tb_crc_status=True,
        input_domain="freq",
        precision="single",
    )

    # 30 Hz maximum Doppler at 3.5 GHz corresponds to about 2.57 m/s.
    speed_mps = 30.0 * 299_792_458.0 / 3.5e9
    tdl = TDL(
        model="C",
        delay_spread=300e-9,
        carrier_frequency=3.5e9,
        min_speed=speed_mps,
        max_speed=speed_mps,
        num_rx_ant=1,
        num_tx_ant=1,
        precision="single",
    )
    channel = OFDMChannel(
        channel_model=tdl,
        resource_grid=transmitter.resource_grid,
        normalize_channel=True,
        return_channel=False,
        precision="single",
    )
    return pusch, transmitter, receiver, channel


def run_point(transmitter, receiver, channel, snr_db: float, seed: int, num_tbs: int):
    # Sionna's global seed drives its TensorFlow/NumPy/Python RNG streams.
    composite_seed = 260_401_000 + 1_000 * seed + int(round(10 * snr_db))
    config.seed = composite_seed
    tf.random.set_seed(composite_seed)
    np.random.seed(composite_seed % (2**32 - 1))

    no = tf.constant(10.0 ** (-snr_db / 10.0), tf.float32)
    started = time.perf_counter()
    x, bits = transmitter(num_tbs)
    y = channel(x, no)
    bits_hat, crc_ok = receiver(y, no)
    runtime_seconds = time.perf_counter() - started

    bits_np = np.asarray(bits.numpy(), dtype=np.int8)
    bits_hat_np = np.asarray(bits_hat.numpy(), dtype=np.int8)
    crc_np = np.asarray(crc_ok.numpy(), dtype=bool).reshape(-1)
    bit_errors = int(np.count_nonzero(bits_np != bits_hat_np))
    total_bits = int(bits_np.size)
    block_errors = int(np.count_nonzero(~crc_np))
    return {
        "snr_db_per_re": snr_db,
        "seed": seed,
        "num_transport_blocks": num_tbs,
        "transport_block_bits": int(bits_np.shape[-1]),
        "block_errors": block_errors,
        "initial_bler": block_errors / num_tbs,
        "bit_errors": bit_errors,
        "ber": bit_errors / total_bits,
        "runtime_seconds": runtime_seconds,
    }


def wilson95(errors: int, trials: int):
    if trials == 0:
        return (math.nan, math.nan)
    z = 1.959963984540054
    p = errors / trials
    den = 1.0 + z * z / trials
    center = (p + z * z / (2.0 * trials)) / den
    half = z * math.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials**2)) / den
    return center - half, center + half


def aggregate(rows):
    out = []
    for snr_db in sorted({row["snr_db_per_re"] for row in rows}):
        selected = [row for row in rows if row["snr_db_per_re"] == snr_db]
        errors = sum(row["block_errors"] for row in selected)
        trials = sum(row["num_transport_blocks"] for row in selected)
        bit_errors = sum(row["bit_errors"] for row in selected)
        total_bits = sum(
            row["num_transport_blocks"] * row["transport_block_bits"] for row in selected
        )
        low, high = wilson95(errors, trials)
        out.append(
            {
                "snr_db_per_re": snr_db,
                "num_seeds": len(selected),
                "num_transport_blocks": trials,
                "transport_block_bits": selected[0]["transport_block_bits"],
                "block_errors": errors,
                "initial_bler": errors / trials,
                "initial_bler_wilson95_low": low,
                "initial_bler_wilson95_high": high,
                "bit_errors": bit_errors,
                "ber": bit_errors / total_bits,
                "runtime_seconds": sum(row["runtime_seconds"] for row in selected),
            }
        )
    return out


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcs", type=int, default=4)
    parser.add_argument("--snr", type=float, nargs="+", default=list(DEFAULT_SNRS))
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--tbs-per-seed", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    if args.output is None:
        args.output = (
            Path(__file__).resolve().parent
            / "results"
            / "sionna"
            / f"mcs_{args.mcs}"
        )

    pusch, transmitter, receiver, channel = build_chain(args.mcs)
    rows = []
    for snr_db in args.snr:
        for seed in args.seeds:
            row = run_point(
                transmitter, receiver, channel, snr_db, seed, args.tbs_per_seed
            )
            rows.append(row)
            print(
                f"MCS={args.mcs} SNR={snr_db:+.1f} seed={seed} "
                f"BLER={row['initial_bler']:.4f} BER={row['ber']:.3e} "
                f"runtime={row['runtime_seconds']:.1f}s",
                flush=True,
            )

    summary = aggregate(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "per_seed.csv", rows)
    write_csv(args.output / "summary.csv", summary)
    physical_gpus = tf.config.list_physical_devices("GPU")
    if physical_gpus:
        gpu_details = tf.config.experimental.get_device_details(physical_gpus[0])
        device = gpu_details.get("device_name", physical_gpus[0].name)
    else:
        device = "CPU"
    metadata = {
        "truth_label": "simulated, link-level PHY",
        "claim_scope": "first-transmission PUSCH BLER/BER only",
        "sionna_version": sionna.__version__,
        "tensorflow_version": tf.__version__,
        "python_version": platform.python_version(),
        "device": device,
        "tensorflow_physical_devices": [item.name for item in tf.config.list_physical_devices()],
        "carrier_frequency_hz": 3.5e9,
        "nominal_bandwidth_hz": 20e6,
        "subcarrier_spacing_khz": 30,
        "n_size_grid": 51,
        "mcs_table": 1,
        "mcs_index": args.mcs,
        "modulation_order": int(pusch.tb.num_bits_per_symbol),
        "target_coderate": float(pusch.tb.target_coderate),
        "channel_profile": "TDL-C",
        "delay_spread_ns": 300,
        "maximum_doppler_hz": 30,
        "channel_normalization": "unit average energy per resource element",
        "noise_definition": "SNR per resource element; No=10^(-SNR_dB/10)",
        "receiver": "PUSCH LS channel estimation + default LMMSE detector + TB decoder",
        "harq": "not simulated",
        "snr_db": args.snr,
        "seeds": args.seeds,
        "transport_blocks_per_seed": args.tbs_per_seed,
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
