#!/usr/bin/env python3
"""Plot the measured service-coordinate calibration used by the AK paper."""

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update(
    {
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "text.usetex": True,
        "font.family": "serif",
        "font.serif": ["Times"],
        "text.latex.preamble": r"\usepackage{newtxtext}",
    }
)

HERE = os.path.dirname(os.path.abspath(__file__))
FIGDIR = os.path.normpath(os.path.join(HERE, "..", "..", "paper", "figures"))
PLABEL = {"751.63M": 0.75, "2.0B": 2.0, "4.0B": 4.0, "8.2B": 8.2, "14.8B": 14.8}

with open(os.path.join(HERE, "per_model.json"), encoding="utf-8") as handle:
    per_model = json.load(handle)

points = sorted(
    (
        PLABEL[value["param_label"]],
        value["alpha_ns_per_tok"] / 1e6,
        value["rho_ns_per_tok"] / 1e6,
    )
    for value in per_model.values()
)
model_size, prompt_slope, generation_slope = (np.asarray(x) for x in zip(*points))

fig, ax = plt.subplots(figsize=(3.45, 2.35), constrained_layout=True)
ax.loglog(
    model_size,
    prompt_slope,
    "o-",
    color="#0072B2",
    linewidth=1.6,
    markersize=4.8,
    label="Prompt processing",
)
ax.loglog(
    model_size,
    generation_slope,
    "s-",
    color="#D55E00",
    linewidth=1.6,
    markersize=4.5,
    label="Token generation",
)
ax.set_xlabel("model size (billion parameters)")
ax.set_ylabel("measured time per token (ms)")
ax.set_xlim(0.65, 17.0)
ax.set_xticks(model_size)
ax.set_xticklabels(["0.75", "2", "4", "8.2", "14.8"])
ax.grid(True, which="both", linestyle=":", linewidth=0.6, alpha=0.55)
ax.legend(fontsize=7.1, loc="upper left", frameon=False)
ax.tick_params(labelsize=7.5)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

output = os.path.join(FIGDIR, "fig_profile_calibration.pdf")
fig.savefig(output, bbox_inches="tight")
fig.savefig(output.replace(".pdf", ".png"), dpi=350, bbox_inches="tight")
print("wrote", output)

