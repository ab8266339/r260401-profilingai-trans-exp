#!/usr/bin/env python3
"""Plot measured token-to-service and architecture-derived state slopes."""
import json
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
    pm = json.load(handle)
P, A, R, G = [], [], [], []
for v in pm.values():
    P.append(PLABEL[v["param_label"]])
    A.append(v["alpha_ns_per_tok"]); R.append(v["rho_ns_per_tok"]); G.append(v["gamma_bytes_per_tok"])
P, A, R, G = (np.array(x, float) for x in (P, A, R, G))
o = np.argsort(P)
P, A, R, G = P[o], A[o], R[o], G[o]

# Separate the two truth types so no measured curve shares an axis with a
# quantity derived from model architecture.
fig, (a0, a1) = plt.subplots(
    2,
    1,
    figsize=(3.45, 4.45),
    sharex=True,
    constrained_layout=True,
    gridspec_kw={"height_ratios": [1.15, 0.85]},
)

a0.loglog(
    P,
    np.asarray(A) / 1e6,
    "o-",
    color="#0072B2",
    linewidth=1.5,
    markersize=4.5,
    label="Prompt processing",
)
a0.loglog(
    P,
    np.asarray(R) / 1e6,
    "s-",
    color="#D55E00",
    linewidth=1.5,
    markersize=4.2,
    label="Token generation",
)
a0.set_ylabel("time per token (ms)")
a0.set_title("(a) Measured wall-clock slopes", loc="left", fontsize=8.8, weight="bold")
a0.grid(True, which="both", linestyle=":", linewidth=0.6, alpha=0.55)
a0.legend(fontsize=7.1, loc="upper left", frameon=False)

a1.semilogx(
    P,
    np.asarray(G) / 1024,
    "D-",
    color="#009E73",
    linewidth=1.5,
    markersize=4.2,
)
a1.set_title("(b) Architecture-derived retained state", loc="left", fontsize=8.8, weight="bold")
a1.set_xlabel("model size (billion parameters)")
a1.set_ylabel("state per token (KB)")
a1.set_ylim(104, 168)
a1.set_yticks([112, 128, 144, 160])
a1.grid(True, which="both", linestyle=":", linewidth=0.6, alpha=0.55)

a1.set_xlim(0.65, 17.0)
a1.set_xticks(P)
a1.set_xticklabels(["0.75", "2", "4", "8.2", "14.8"])
for axis in (a0, a1):
    axis.tick_params(labelsize=7.5)
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)

out = os.path.join(FIGDIR, "fig_measured_coupling.pdf")
fig.savefig(out, bbox_inches="tight")
fig.savefig(out.replace(".pdf", ".png"), dpi=300, bbox_inches="tight")
print("wrote", out)
