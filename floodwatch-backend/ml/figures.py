"""Thesis figures.

Every figure is written to both PDF (vector, for the thesis) and PNG (for slides
and quick inspection). Nothing here invents data: each function takes the results
of an actual experiment run and draws them.

Design constraints, applied consistently:
  * A four-hue categorical palette, fixed order, validated for colour-vision
    deficiency separation. Series identity is carried by colour AND line style,
    so every figure survives greyscale printing -- which a thesis will get.
  * One y-axis per plot. Never two scales on one figure.
  * Recessive grid and axes; the data is the darkest thing on the page.
  * A legend whenever more than one series is present.
  * Captions live in the thesis text, not burned into the image.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import PercentFormatter

# Validated categorical palette (CVD adjacent-pair dE 9.1 protan, 22.9 normal).
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
SERIES = [BLUE, ORANGE, AQUA, YELLOW]
DASHES = ["-", "--", "-.", ":"]

INK = "#1a1a1a"
INK_2 = "#4a4a4a"
MUTED = "#8a8a8a"
GRID = "#d8dcda"
SURFACE = "#ffffff"

# Status colours, reserved -- never reused as a series hue.
STATUS_ALERT = "#b07d12"
STATUS_MINOR = "#c4642a"
STATUS_MAJOR = "#b3302a"

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 10.5,
    "axes.titleweight": "600",
    "axes.labelsize": 9,
    "axes.labelcolor": INK_2,
    "axes.edgecolor": GRID,
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "grid.alpha": 0.9,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.frameon": False,
    "legend.fontsize": 8.5,
    "lines.linewidth": 1.8,
    "lines.solid_capstyle": "round",
})


def _finish(fig, ax_or_axes, out: Path, name: str) -> Path:
    for ax in np.atleast_1d(ax_or_axes).ravel():
        if hasattr(ax, "spines"):
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
    out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{name}.pdf"
    fig.savefig(pdf)
    fig.savefig(out / f"{name}.png")
    plt.close(fig)
    return pdf


# ---------------------------------------------------------------------------
# Figure 1 -- an example hydrograph with thresholds and the label window
# ---------------------------------------------------------------------------


def fig_hydrograph(data: pd.DataFrame, station: str, centre: pd.Timestamp,
                   out: Path, window_h: int = 120, name: str = "fig01_hydrograph") -> Path:
    frame = data[(data["station"] == station)
                 & (data["hour"] >= centre - pd.Timedelta(hours=window_h // 2))
                 & (data["hour"] <= centre + pd.Timedelta(hours=window_h // 2))].copy()

    fig, (ax_rain, ax) = plt.subplots(
        2, 1, figsize=(7.2, 4.6), sharex=True,
        gridspec_kw={"height_ratios": [1, 2.6], "hspace": 0.12},
    )

    # Rainfall as a separate panel above -- NOT a second y-axis on the same plot.
    ax_rain.bar(frame["hour"], frame["station_rain_mm"], width=0.04,
                color=BLUE, alpha=0.75, linewidth=0)
    ax_rain.set_ylabel("Rainfall\n(mm/h)")
    ax_rain.invert_yaxis()
    ax_rain.grid(axis="x", visible=False)

    ax.plot(frame["hour"], frame["true_level_m"], color=INK, linewidth=1.9,
            label="Water level", zorder=3)
    observed = frame[frame["water_level_m"].notna()]
    ax.plot(observed["hour"], observed["water_level_m"], linestyle="none", marker="o",
            markersize=2.2, color=ORANGE, alpha=0.8, label="Gauge reading", zorder=4)

    thresholds = [
        ("Alert", frame["alert_level_m"].iloc[0], STATUS_ALERT),
        ("Minor flood", frame["minor_flood_level_m"].iloc[0], STATUS_MINOR),
        ("Major flood", frame["major_flood_level_m"].iloc[0], STATUS_MAJOR),
    ]
    for label, level, colour in thresholds:
        ax.axhline(level, color=colour, linewidth=1.1, linestyle="--", alpha=0.9, zorder=2)
        ax.annotate(label, xy=(frame["hour"].iloc[-1], level), xytext=(4, 1),
                    textcoords="offset points", color=colour, fontsize=7.5,
                    va="bottom", ha="left", fontweight="600")

    flooding = frame["true_level_m"] >= frame["minor_flood_level_m"]
    if flooding.any():
        ax.fill_between(frame["hour"], ax.get_ylim()[0], ax.get_ylim()[1],
                        where=flooding, color=STATUS_MINOR, alpha=0.08, zorder=1)
        onset = frame.loc[flooding.idxmax(), "hour"]
        ax.axvline(onset, color=STATUS_MAJOR, linewidth=1.0, alpha=0.6, zorder=2)
        ax.annotate("flood onset", xy=(onset, ax.get_ylim()[0]), xytext=(5, 6),
                    textcoords="offset points", ha="left", va="bottom",
                    fontsize=7.5, fontweight="600", color=STATUS_MAJOR)

    ax.set_ylabel("Water level (m)")
    ax.set_xlabel("")
    ax.legend(loc="upper left", ncol=2)
    ax_rain.set_title(f"Rainfall and river stage at {station}", loc="left", color=INK)
    fig.autofmt_xdate(rotation=0, ha="center")
    return _finish(fig, [ax_rain, ax], out, name)


# ---------------------------------------------------------------------------
# Figure 2 -- seasonality
# ---------------------------------------------------------------------------


def fig_seasonality(data: pd.DataFrame, out: Path, name: str = "fig02_seasonality") -> Path:
    frame = data.copy()
    frame["month"] = frame["hour"].dt.month
    monthly = (frame.groupby(["basin", "month"])["station_rain_mm"]
               .sum().reset_index())
    years = frame["hour"].dt.year.nunique()
    monthly["mm_per_month"] = monthly["station_rain_mm"] / (years * 4)  # 4 stations per basin

    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    for index, (basin, group) in enumerate(monthly.groupby("basin")):
        ax.plot(group["month"], group["mm_per_month"], color=SERIES[index],
                linestyle=DASHES[index], marker="o", markersize=4, label=basin)

    ax.axvspan(4.5, 9.5, color=BLUE, alpha=0.06, zorder=0)
    ax.axvspan(11.5, 12.5, color=ORANGE, alpha=0.06, zorder=0)
    ax.axvspan(0.5, 2.5, color=ORANGE, alpha=0.06, zorder=0)
    ax.annotate("south-west monsoon", xy=(7, ax.get_ylim()[1] * 0.94),
                ha="center", fontsize=7.5, color=INK_2)
    ax.annotate("north-east", xy=(1.5, ax.get_ylim()[1] * 0.94),
                ha="center", fontsize=7.5, color=INK_2)

    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.set_ylabel("Mean monthly rainfall (mm)")
    ax.set_title("Seasonal rainfall by basin", loc="left", color=INK)
    ax.legend(loc="upper left", ncol=3)
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 3 -- class balance
# ---------------------------------------------------------------------------


def fig_class_balance(summary: pd.DataFrame, out: Path,
                      name: str = "fig03_class_balance") -> Path:
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    y = np.arange(len(summary))
    ax.barh(y, summary["positive_rate"] * 100, color=BLUE, height=0.55)
    ax.set_yticks(y)
    ax.set_yticklabels(summary["label"])
    ax.invert_yaxis()
    ax.set_xlabel("Positive class (% of station-hours)")
    ax.set_title("Class balance by target", loc="left", color=INK)
    ax.grid(axis="y", visible=False)

    for index, row in enumerate(summary.itertuples()):
        ax.annotate(f"{row.positive_rate * 100:.2f}%  ({row.positives:,} of {row.rows:,})",
                    xy=(row.positive_rate * 100, index), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8, color=INK_2)
    ax.set_xlim(0, max(summary["positive_rate"] * 100) * 1.9)
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 4 -- precision-recall
# ---------------------------------------------------------------------------


def fig_pr_curves(curves: dict[str, tuple[np.ndarray, np.ndarray]], base_rate: float,
                  scores: dict[str, float], out: Path, name: str = "fig04_pr_curves") -> Path:
    fig, ax = plt.subplots(figsize=(5.4, 4.2))
    for index, (label, (recall, precision)) in enumerate(curves.items()):
        ax.plot(recall, precision, color=SERIES[index % 4], linestyle=DASHES[index % 4],
                label=f"{label}  (AP {scores[label]:.3f})")
    ax.axhline(base_rate, color=MUTED, linewidth=1.0, linestyle=(0, (1, 3)))
    ax.annotate(f"random classifier ({base_rate:.2%})", xy=(0.02, base_rate),
                xytext=(0, 5), textcoords="offset points", ha="left",
                fontsize=7.5, color=MUTED)
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_title("Precision-recall, 6-hour horizon (test split)", loc="left", color=INK)
    ax.legend(loc="upper right")
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 5 -- ROC
# ---------------------------------------------------------------------------


def fig_roc_curves(curves: dict[str, tuple[np.ndarray, np.ndarray]],
                   scores: dict[str, float], out: Path, name: str = "fig05_roc") -> Path:
    fig, ax = plt.subplots(figsize=(5.4, 4.2))
    ax.plot([0, 1], [0, 1], color=MUTED, linewidth=1.0, linestyle=(0, (1, 3)))
    for index, (label, (fpr, tpr)) in enumerate(curves.items()):
        ax.plot(fpr, tpr, color=SERIES[index % 4], linestyle=DASHES[index % 4],
                label=f"{label}  (AUC {scores[label]:.3f})")
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_title("ROC, 6-hour horizon (test split)", loc="left", color=INK)
    ax.legend(loc="lower right")
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 6 -- calibration
# ---------------------------------------------------------------------------


def fig_calibration(bins: dict[str, pd.DataFrame], out: Path,
                    name: str = "fig06_calibration") -> Path:
    fig, ax = plt.subplots(figsize=(5.0, 4.2))
    ax.plot([0, 1], [0, 1], color=MUTED, linewidth=1.0, linestyle=(0, (1, 3)),
            label="perfect calibration")
    for index, (label, frame) in enumerate(bins.items()):
        ax.plot(frame["predicted"], frame["observed"], color=SERIES[index % 4],
                linestyle=DASHES[index % 4], marker="o", markersize=4, label=label)
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_title("Reliability diagram (test split)", loc="left", color=INK)
    ax.legend(loc="upper left")
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 7 -- permutation importance
# ---------------------------------------------------------------------------


def fig_importance(importance: pd.DataFrame, out: Path, top: int = 18,
                   name: str = "fig07_importance") -> Path:
    frame = importance.head(top).iloc[::-1]
    colours = [ORANGE if group == "crowd" else BLUE for group in frame["group"]]

    fig, ax = plt.subplots(figsize=(6.6, 5.0))
    y = np.arange(len(frame))
    ax.barh(y, frame["importance"], xerr=frame["std"], color=colours, height=0.6,
            error_kw={"ecolor": MUTED, "elinewidth": 0.8, "capsize": 2})
    ax.set_yticks(y)
    ax.set_yticklabels(frame["feature"], fontsize=8)
    ax.set_xlabel("Drop in average precision when permuted")
    ax.set_title("Permutation importance, M2 (test split)", loc="left", color=INK)
    ax.grid(axis="y", visible=False)

    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE),
               plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
    ax.legend(handles, ["Physical features", "Crowdsourced features"], loc="lower right")
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 8 -- ablation
# ---------------------------------------------------------------------------


def fig_ablation(ablation: pd.DataFrame, out: Path, name: str = "fig08_ablation") -> Path:
    frame = ablation.sort_values("pr_auc")
    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    y = np.arange(len(frame))
    full = frame["pr_auc"].max()
    colours = [AQUA if abs(v - full) < 1e-9 else BLUE for v in frame["pr_auc"]]
    ax.barh(y, frame["pr_auc"], xerr=frame.get("pr_auc_sd"), color=colours, height=0.6,
            error_kw={"ecolor": MUTED, "elinewidth": 0.9, "capsize": 2.5})
    ax.set_yticks(y)
    ax.set_yticklabels(frame["configuration"], fontsize=8.5)
    ax.set_xlabel("Average precision (test split)")
    ax.set_title("Ablation: contribution of each feature group", loc="left", color=INK)
    ax.grid(axis="y", visible=False)
    for index, row in enumerate(frame.itertuples()):
        ax.annotate(f"{row.pr_auc:.3f}", xy=(row.pr_auc, index), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=8, color=INK_2)
    ax.set_xlim(0, frame["pr_auc"].max() * 1.22)
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 9 -- lead time
# ---------------------------------------------------------------------------


def fig_lead_time(tables: dict[str, pd.DataFrame], out: Path,
                  name: str = "fig09_lead_time") -> Path:
    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    width = 0.8 / max(1, len(tables))
    bins = np.arange(0, 25, 2)

    for index, (label, table) in enumerate(tables.items()):
        detected = table[table["detected"]]["lead_time_h"]
        counts, _ = np.histogram(detected, bins=bins)
        centres = bins[:-1] + 1
        ax.bar(centres + (index - (len(tables) - 1) / 2) * width * 2,
               counts, width=width * 2 - 0.12, color=SERIES[index % 4], label=label)

    ax.set_xlabel("Lead time before flood onset (hours)")
    ax.set_ylabel("Flood episodes")
    ax.set_title("Distribution of warning lead time (test split)", loc="left", color=INK)
    ax.legend(loc="upper right")
    ax.set_xticks(bins)
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 10 -- crowd sensitivity
# ---------------------------------------------------------------------------


def fig_crowd_sensitivity(grid: pd.DataFrame, out: Path,
                          name: str = "fig10_crowd_sensitivity") -> Path:
    """Sequential ramp: one hue, light to dark, because this encodes magnitude."""
    pivot = grid.pivot(index="detection_rate", columns="panel_size", values="delta_pr_auc")

    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "aqua", ["#f2f8f6", "#a8ddcb", "#4fc09a", AQUA, "#0c6c4c"]
    )
    limit = float(np.nanmax(np.abs(pivot.to_numpy())))
    image = ax.imshow(pivot.to_numpy(), cmap=cmap, aspect="auto",
                      origin="lower", vmin=0, vmax=limit)

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f"{v:.0%}" for v in pivot.index])
    ax.set_xlabel("Participants per region")
    ax.set_ylabel("Reporter detection rate")
    ax.set_title("Gain in average precision from the crowd layer", loc="left", color=INK)
    ax.grid(visible=False)

    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            value = pivot.to_numpy()[i, j]
            if np.isnan(value):
                continue
            ax.annotate(f"{value:+.3f}", xy=(j, i), ha="center", va="center",
                        fontsize=8, color="white" if value > limit * 0.55 else INK)

    bar = fig.colorbar(image, ax=ax, pad=0.02)
    bar.outline.set_visible(False)
    bar.set_label("Δ average precision vs physical-only", fontsize=8)
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 11 -- confusion matrix
# ---------------------------------------------------------------------------


def fig_confusion(metrics: dict, out: Path, title: str,
                  name: str = "fig11_confusion") -> Path:
    matrix = np.array([[metrics["tn"], metrics["fp"]],
                       [metrics["fn"], metrics["tp"]]], dtype=float)
    normalised = matrix / matrix.sum(axis=1, keepdims=True)

    fig, ax = plt.subplots(figsize=(4.4, 3.6))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list(
        "blue", ["#f4f8fd", "#bcd8f4", "#6aa8e4", BLUE, "#1a4f8f"]
    )
    ax.imshow(normalised, cmap=cmap, vmin=0, vmax=1)

    for i in range(2):
        for j in range(2):
            ax.annotate(f"{int(matrix[i, j]):,}\n{normalised[i, j]:.1%}",
                        xy=(j, i), ha="center", va="center", fontsize=9.5,
                        color="white" if normalised[i, j] > 0.55 else INK)

    ax.set_xticks([0, 1]); ax.set_xticklabels(["No warning", "Warning"])
    ax.set_yticks([0, 1]); ax.set_yticklabels(["No flood", "Flood"])
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
    ax.set_title(title, loc="left", color=INK)
    ax.grid(visible=False)
    return _finish(fig, ax, out, name)


# ---------------------------------------------------------------------------
# Figure 12 -- generalisation to an unseen basin
# ---------------------------------------------------------------------------


def fig_spatial_holdout(results: pd.DataFrame, out: Path,
                        name: str = "fig12_spatial_holdout") -> Path:
    fig, ax = plt.subplots(figsize=(6.2, 3.2))
    x = np.arange(len(results))
    width = 0.36
    ax.bar(x - width / 2, results["within"], width, color=BLUE, label="Temporal split (seen basin)")
    ax.bar(x + width / 2, results["holdout"], width, color=ORANGE, label="Leave-one-basin-out")
    ax.set_xticks(x)
    ax.set_xticklabels(results["basin"])
    ax.set_ylabel("Average precision")
    ax.set_title("Generalisation to an unseen river basin", loc="left", color=INK)
    ax.legend(loc="upper right")
    ax.grid(axis="x", visible=False)
    for index, row in enumerate(results.itertuples()):
        ax.annotate(f"{row.within:.3f}", xy=(index - width / 2, row.within), xytext=(0, 3),
                    textcoords="offset points", ha="center", fontsize=7.5, color=INK_2)
        ax.annotate(f"{row.holdout:.3f}", xy=(index + width / 2, row.holdout), xytext=(0, 3),
                    textcoords="offset points", ha="center", fontsize=7.5, color=INK_2)
    return _finish(fig, ax, out, name)
