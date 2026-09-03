"""Draw the pipeline latency figure from a measured run.

    python -m thesis.make_latency_figure

Reads thesis/latency.json, written by scripts/measure_latency.py, and draws the
five stages on a log axis alongside the two terms that dominate them and are not
software: the operator's decision and the median lead time. Plotting the
software stages alone would invite the reader to conclude that 127 ms is the
number that matters, which is the opposite of what the measurement shows.

Uses the same palette and styling as ml/figures.py so the chapter's figures read
as one set.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ml.figures import AQUA, BLUE, GRID, INK, INK_2, MUTED, _finish

HERE = Path(__file__).parent


def main() -> int:
    data = json.loads((HERE / "latency.json").read_text())
    stages = data["stages"]

    labels = {
        "store": "Store reading",
        "score": "Score all regions",
        "read_queue": "Operator loads queue",
        "authorise": "Authorise and publish",
        "deliver": "Client reads alert",
    }
    order = ["store", "score", "read_queue", "authorise", "deliver"]
    rows = {r["stage"]: r for r in stages}
    names = [labels[s] for s in order if s in rows]
    medians = np.array([rows[s]["median_ms"] for s in order if s in rows])
    p90s = np.array([rows[s]["p90_ms"] for s in order if s in rows])

    fig, ax = plt.subplots(figsize=(7.0, 3.9))
    y = np.arange(len(names))[::-1]

    # The slowest stage is the one the reader should look at.
    slowest = medians.argmax()
    colours = [AQUA if i == slowest else BLUE for i in range(len(names))]

    ax.barh(y, medians, height=0.55, color=colours, zorder=3)
    ax.barh(y, p90s - medians, left=medians, height=0.55,
            color=colours, alpha=0.32, zorder=3)

    for i, (m, p) in enumerate(zip(medians, p90s)):
        ax.annotate(f"{m:.1f} ms   (p90 {p:.1f})", xy=(p, y[i]), xytext=(6, 0),
                    textcoords="offset points", va="center",
                    fontsize=8, color=INK_2)

    # The two terms that dwarf every bar above.
    for x, style, label in ((30_000, "--", "operator decision  (~30 s)"),
                            (3_600_000, ":", "median lead time  (1 h)")):
        ax.axvline(x, color=MUTED, linewidth=1.0, linestyle=style, zorder=2)
        ax.annotate(label, xy=(x, 0.985), xycoords=("data", "axes fraction"),
                    xytext=(-5, 0), textcoords="offset points",
                    rotation=90, ha="right", va="top",
                    fontsize=8, color=MUTED)

    ax.set_xscale("log")
    ax.set_xlim(1, 2.0e7)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Elapsed time (ms, log scale) — bar is median, pale extension is p90")
    ax.set_title(
        f"Warning pipeline latency by stage (n = {data['repeat']} per stage)",
        loc="left", color=INK)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)

    total = data["sum_of_stage_medians_ms"]
    ax.annotate(
        f"Sum of stage medians: {total:.0f} ms. The two dashed references are not "
        f"software\nand exceed the whole machine path by three to four orders of "
        f"magnitude.",
        xy=(0, -0.30), xycoords="axes fraction", fontsize=8, color=MUTED, va="top")

    path = _finish(fig, ax, HERE / "figures", "fig5_10_latency")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
