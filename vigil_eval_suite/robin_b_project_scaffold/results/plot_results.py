"""
results/plot_results.py
========================
Publication-quality figures for the VIGIL fleet eval.

Fig 1: Accumulation Curve — VIGIL composite score + EmoBank rows vs episode
Fig 2: Detection Heatmap  — first episode detected per system per failure class
Fig 3: False Positive Rate bar chart

Run after eval_fleet.py:
    python results/plot_results.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

RESULTS = Path(__file__).parent.resolve()
FIGS    = RESULTS

SYSTEMS = ["wink", "agentspec", "arm", "vigil"]
CLASSES = ["structural", "drift", "novel"]

SYSTEM_COLORS = {
    "vigil":     "#2563EB",   # blue  — hero system
    "wink":      "#16A34A",   # green
    "agentspec": "#D97706",   # amber
    "arm":       "#9333EA",   # purple
}

plt.rcParams.update({
    "font.family":    "DejaVu Sans",
    "font.size":      11,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
    "figure.dpi":     150,
})


# ─── Fig 1: Accumulation Curve ───────────────────────────────────────────────

def plot_accumulation_curve(curve_path: Path = RESULTS / "accumulation_curve.csv"):
    if not curve_path.exists():
        print(f"[skip] {curve_path} not found")
        return

    episodes, composites, emo_rows, line_counts = [], [], [], []
    with open(curve_path) as f:
        for row in csv.DictReader(f):
            episodes.append(int(row["episode"]))
            composites.append(float(row["mean_composite"]))
            emo_rows.append(float(row["mean_emo_rows"]))
            line_counts.append(float(row["mean_line_count"]))

    fig, ax1 = plt.subplots(figsize=(7, 4.5))
    ax2 = ax1.twinx()

    l1, = ax1.plot(episodes, composites, "o-",
                   color=SYSTEM_COLORS["vigil"], linewidth=2.5,
                   markersize=7, label="Composite score (left)")
    l2, = ax2.plot(episodes, emo_rows, "s--",
                   color="#6B7280", linewidth=1.8,
                   markersize=5, alpha=0.75, label="EmoBank rows (right)")

    ax1.set_xlabel("Episode")
    ax1.set_ylabel("Mean composite score", color=SYSTEM_COLORS["vigil"])
    ax2.set_ylabel("Mean EmoBank rows accumulated", color="#6B7280")
    ax1.set_xticks(episodes)
    ax1.set_ylim(0, 1.0)

    ax1.tick_params(axis="y", colors=SYSTEM_COLORS["vigil"])
    ax2.tick_params(axis="y", colors="#6B7280")

    lines = [l1, l2]
    ax1.legend(lines, [l.get_label() for l in lines], loc="lower right", fontsize=9)

    plt.title("Fig 1  VIGIL Accumulation Curve\n"
              "Composite score and EmoBank rows grow together across episodes",
              fontsize=11, pad=12)
    plt.tight_layout()

    out = FIGS / "fig1_accumulation_curve.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out}")


# ─── Fig 2: Detection Heatmap ─────────────────────────────────────────────────

def plot_detection_heatmap(det_path: Path = RESULTS / "detection_table.csv"):
    if not det_path.exists():
        print(f"[skip] {det_path} not found")
        return

    # Build matrix: rows=failure_class, cols=system
    # cell = first episode detected (or 8 = "never" for display)
    NEVER = 8
    data = {(s, c): NEVER for s in SYSTEMS for c in CLASSES}
    with open(det_path) as f:
        for row in csv.DictReader(f):
            s = row["system"]
            c = row["failure_class"]
            ep = row["first_episode_detected"]
            data[(s, c)] = int(ep) if ep and ep != "None" else NEVER

    matrix = np.array([[data[(s, c)] for s in SYSTEMS] for c in CLASSES], dtype=float)

    # Color: lower (earlier detection) = better = darker blue; NEVER = gray
    cmap = plt.cm.Blues_r.copy()
    cmap.set_over("#E5E7EB")   # gray for "never"

    fig, ax = plt.subplots(figsize=(7, 3.8))
    im = ax.imshow(matrix, cmap=cmap, vmin=1, vmax=7, aspect="auto")

    ax.set_xticks(range(len(SYSTEMS)))
    ax.set_xticklabels([s.upper() for s in SYSTEMS], fontsize=10)
    ax.set_yticks(range(len(CLASSES)))
    ax.set_yticklabels([c.capitalize() for c in CLASSES], fontsize=10)

    # Annotate cells
    for i, c in enumerate(CLASSES):
        for j, s in enumerate(SYSTEMS):
            val = data[(s, c)]
            txt = str(val) if val < NEVER else "—"
            col = "white" if val <= 4 else "#111827"
            ax.text(j, i, f"ep{txt}" if val < NEVER else "never",
                    ha="center", va="center", fontsize=9, color=col, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Episode first detected (lower = better)", fontsize=9)
    cbar.set_ticks([1, 3, 5, 7])

    plt.title("Fig 2  Detection Table\nFirst episode each system detected each failure class",
              fontsize=11, pad=12)
    plt.tight_layout()

    out = FIGS / "fig2_detection_heatmap.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out}")


# ─── Fig 3: False Positive Rate ──────────────────────────────────────────────

def plot_false_positive(fp_path: Path = RESULTS / "false_positive_rate.csv"):
    if not fp_path.exists():
        print(f"[skip] {fp_path} not found")
        return

    systems, rates = [], []
    with open(fp_path) as f:
        for row in csv.DictReader(f):
            systems.append(row["system"].upper())
            rates.append(float(row["fp_rate"]))

    colors = [SYSTEM_COLORS.get(s.lower(), "#9CA3AF") for s in systems]

    fig, ax = plt.subplots(figsize=(6, 3.5))
    bars = ax.bar(systems, rates, color=colors, width=0.55, edgecolor="white", linewidth=1.2)

    for bar, rate in zip(bars, rates):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.01,
                f"{rate:.1%}", ha="center", va="bottom", fontsize=10)

    ax.set_ylabel("False positive rate")
    ax.set_ylim(0, max(rates or [0.1]) + 0.15)
    ax.axhline(0, color="#D1D5DB", linewidth=0.8)

    plt.title("Fig 3  False Positive Rate on Clean Agents\n"
              "Fraction of healthy agents incorrectly flagged by each system",
              fontsize=11, pad=12)
    plt.tight_layout()

    out = FIGS / "fig3_false_positive_rate.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out}")


# ─── Fig 4: Fleet health over time ───────────────────────────────────────────

def plot_fleet_health(metrics_path: Path = None):
    # Find any metrics JSON
    if metrics_path is None:
        candidates = list(RESULTS.glob("metrics_seed*.json"))
        if not candidates:
            print("[skip] no metrics_seed*.json found")
            return
        metrics_path = sorted(candidates)[0]

    with open(metrics_path) as f:
        metrics = json.load(f)

    fh = metrics.get("fleet_health", [])
    if not fh:
        print("[skip] no fleet_health data")
        return

    episodes = [r["episode"] for r in fh]
    health   = [r["health_score"] for r in fh]

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.fill_between(episodes, health, alpha=0.15, color=SYSTEM_COLORS["vigil"])
    ax.plot(episodes, health, "o-", color=SYSTEM_COLORS["vigil"],
            linewidth=2.5, markersize=7)

    ax.set_xlabel("Episode")
    ax.set_ylabel("Fleet health score\n(fraction of agents with 0 thorns)")
    ax.set_xticks(episodes)
    ax.set_ylim(0, 1.05)
    ax.axhline(1.0, color="#D1D5DB", linewidth=0.8, linestyle="--")

    plt.title("Fig 4  VIGIL Fleet Health Over Time\n"
              "Fraction of non-clean agents with zero high-intensity thorns",
              fontsize=11, pad=12)
    plt.tight_layout()

    out = FIGS / "fig4_fleet_health.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Generating VIGIL fleet eval figures...")
    plot_accumulation_curve()
    plot_detection_heatmap()
    plot_false_positive()
    plot_fleet_health()
    print("Done.")


if __name__ == "__main__":
    main()
