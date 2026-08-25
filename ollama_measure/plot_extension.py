#!/usr/bin/env python3
"""Phase-structured quantities figure: prefill/decode asymmetry + batching mu(n)."""
import json, os
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
FIGDIR = os.path.normpath(os.path.join(HERE, "..", "..", "paper", "figures"))
PLABEL = {"0.6b": 0.75, "1.7b": 2.0, "4b": 4.0, "8b": 8.2, "14b": 14.8}

ph = json.load(open(os.path.join(HERE, "phase_probe.json")))
tiers = sorted(ph.keys(), key=lambda m: PLABEL[m.split(":")[1]])
P = [PLABEL[m.split(":")[1]] for m in tiers]
pf_tps = [ph[m]["prefill_tok_per_s"] for m in tiers]
dc_tps = [ph[m]["decode_tok_per_s"] for m in tiers]
pf_e = [ph[m].get("prefill_energy_J_per_tok") for m in tiers]
dc_e = [ph[m].get("decode_energy_J_per_tok") for m in tiers]

fig, ax = plt.subplots(1, 3, figsize=(10.2, 2.9))

# (a) prefill vs decode throughput
ax[0].loglog(P, pf_tps, "o-", color="#1f77b4", label="prefill (compute-bound)")
ax[0].loglog(P, dc_tps, "s-", color="#d62728", label="decode (mem-bound)")
ax[0].set_xlabel("model size $P$ (B)"); ax[0].set_ylabel("throughput (tok/s)")
ax[0].set_title("(a) prefill vs decode throughput\n(34--55$\\times$ gap)", fontsize=8)
ax[0].legend(fontsize=7); ax[0].grid(True, which="both", ls=":", alpha=0.5)

# (b) energy per token prefill vs decode
ax[1].loglog(P, dc_e, "s-", color="#d62728", label="decode")
ax[1].loglog(P, pf_e, "o-", color="#1f77b4", label="prefill")
ax[1].set_xlabel("model size $P$ (B)"); ax[1].set_ylabel("energy (J / token)")
ratio = [d/p for d, p in zip(dc_e, pf_e)]
ax[1].set_title(f"(b) energy/token asymmetry\n(decode/prefill {min(ratio):.0f}--{max(ratio):.0f}$\\times$, grows with $P$)", fontsize=8)
ax[1].legend(fontsize=7); ax[1].grid(True, which="both", ls=":", alpha=0.5)

# (c) batching: aggregate decode throughput vs concurrency (honest negative result)
mp = os.path.join(HERE, "batching_mu.json")
if os.path.exists(mp):
    j = json.load(open(mp)); tps = j["tps_agg"]
    ns = sorted(int(k) for k in tps)
    vals = [tps[str(n)] for n in ns]
    ax[2].plot(ns, vals, "^-", color="#2ca02c")
    ax[2].axhline(vals[0], ls="--", color="gray", alpha=0.6, label="$n{=}1$ baseline")
    ax[2].set_xscale("log", base=2); ax[2].set_xticks(ns); ax[2].set_xticklabels(ns)
    ax[2].set_xlabel("concurrency $n$"); ax[2].set_ylabel("aggregate decode (tok/s)")
    g = j.get("throughput_gain_at_max")
    ax[2].set_title(f"(c) no iteration-level batching on Ollama\n(aggregate flat, gain {g}$\\times$ at $n${{=}}{ns[-1]}; $\\mu{{\\approx}}1$)", fontsize=8)
    ax[2].legend(fontsize=7); ax[2].grid(True, ls=":", alpha=0.5); ax[2].set_ylim(0, max(vals)*1.3)
else:
    ax[2].text(0.5, 0.5, "mu(n) pending", ha="center", va="center"); ax[2].axis("off")

fig.tight_layout()
out = os.path.join(FIGDIR, "fig_phase_quantities.pdf")
fig.savefig(out, bbox_inches="tight"); fig.savefig(out.replace(".pdf", ".png"), dpi=150, bbox_inches="tight")
print("wrote", out)
