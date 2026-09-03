"""Analyse the public survey export and produce the Chapter 3 figures.

    python -m thesis.survey_analysis --csv responses.csv

Takes the CSV that Google Forms exports (Responses → ⋮ → Download responses
.csv) and produces, in thesis/figures/ and thesis/survey/:

    fig3_11_respondent_profile     who answered — age, district, area type
    fig3_12_flood_experience       exposure and impact
    fig3_13_current_warning        how people are warned now, and the gap
    fig3_14_expectations           what people want from a warning system
    fig3_15_crowdsourcing          willingness to report and to trust reports
    survey_results.json            every count and percentage, for citation
    survey_summary.md              a drafted section 3.3.4 with real numbers

The script does not assume a fixed questionnaire. It classifies each column by
its response pattern — single choice, multiple choice, ordinal scale, or free
text — and picks the appropriate chart form for each. Columns it cannot classify
are reported rather than silently dropped.

DESIGN NOTES ON THE CHARTS
  * Single-choice questions become horizontal bars sorted by frequency, because
    the reader's job is comparing magnitudes and horizontal bars keep long
    Sinhala and English option labels readable.
  * Multiple-choice questions are split on the separator Google Forms uses and
    counted per option; the denominator is respondents, not selections, and the
    chart says so.
  * Ordinal scales become diverging stacked bars centred on the neutral point,
    so agreement and disagreement read as opposite directions rather than as
    two similar-looking blocks.
  * Every chart states n. A percentage without its denominator is not a result.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
OUT = HERE / "survey"

# Same validated palette as the model figures, so the thesis reads as one system.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK_2, MUTED, GRID = "#1a1a1a", "#4a4a4a", "#8a8a8a", "#d8dcda"

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 300, "savefig.bbox": "tight",
    "figure.facecolor": "white", "axes.facecolor": "white",
    "font.family": "DejaVu Sans", "font.size": 9,
    "axes.titlesize": 10.5, "axes.titleweight": "600",
    "axes.labelcolor": INK_2, "axes.edgecolor": GRID, "axes.linewidth": 0.8,
    "axes.grid": True, "axes.axisbelow": True,
    "grid.color": GRID, "grid.linewidth": 0.6,
    "xtick.color": INK_2, "ytick.color": INK_2,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "legend.frameon": False, "legend.fontsize": 8.5,
})

MULTI_SEPARATORS = [";", ","]
LIKERT_HINTS = [
    ["strongly disagree", "disagree", "neutral", "agree", "strongly agree"],
    ["very unlikely", "unlikely", "neutral", "likely", "very likely"],
    ["not at all", "slightly", "moderately", "very", "extremely"],
    ["never", "rarely", "sometimes", "often", "always"],
    ["very poor", "poor", "average", "good", "very good"],
    ["not confident", "slightly confident", "moderately confident",
     "confident", "very confident"],
]


# ---------------------------------------------------------------------------
# Column classification
# ---------------------------------------------------------------------------


def classify(series: pd.Series) -> str:
    values = series.dropna().astype(str).str.strip()
    values = values[values != ""]
    if values.empty:
        return "empty"

    lowered = values.str.lower()
    unique = lowered.unique()

    for scale in LIKERT_HINTS:
        if sum(any(step in u for step in scale) for u in unique) >= max(3, len(unique) * 0.6):
            return "ordinal"

    # A bare 1-5 response set is an agreement scale, not four unrelated categories.
    # Treating it as nominal would sort the bars by frequency and destroy the order
    # that carries the meaning.
    if unique.size and all(u.strip() in {"1", "2", "3", "4", "5"} for u in unique):
        return "numeric_scale"

    # Multiple choice: Google Forms joins selections with ", " and options recur.
    for sep in MULTI_SEPARATORS:
        if (values.str.contains(sep, regex=False).mean() > 0.25
                and values.str.len().mean() < 220):
            parts = Counter()
            for value in values:
                for piece in split_multi(value, sep):
                    parts[piece] += 1
            if parts and len(parts) < len(values) * 0.7:
                return f"multi:{sep}"

    # A question with many distinct, sentence-length answers is free text even
    # when respondents happened to choose from a long option list. Charting it as
    # categorical produces forty overlapping labels and communicates nothing;
    # it is reported thematically instead.
    if values.str.len().mean() > 90 or len(unique) > max(12, len(values) * 0.6):
        return "text"
    if len(unique) > 15 and values.str.len().mean() > 30:
        return "text"
    return "single"


def split_multi(value: str, sep: str) -> list[str]:
    return [p.strip() for p in value.split(sep) if p.strip()]


def ordinal_order(labels: list[str]) -> list[str]:
    for scale in LIKERT_HINTS:
        ordered = [l for step in scale for l in labels if step in l.lower()]
        seen, result = set(), []
        for label in ordered:
            if label not in seen:
                seen.add(label)
                result.append(label)
        if len(result) >= len(labels) * 0.6:
            return result + [l for l in labels if l not in seen]
    return sorted(labels)


EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\uFE0F]+")


def shorten(label: str, limit: int = 46) -> str:
    label = EMOJI.sub("", str(label))
    label = re.sub(r"\s+", " ", label).strip()
    return label if len(label) <= limit else label[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def bar_panel(ax, counts: pd.Series, n: int, colour: str, title: str,
              denominator_note: str = "", keep_order: bool = False) -> None:
    counts = counts[::-1] if keep_order else counts.sort_values()
    y = np.arange(len(counts))
    ax.barh(y, counts.to_numpy() / n * 100, color=colour, height=0.62)
    ax.set_yticks(y)
    ax.set_yticklabels([shorten(i) for i in counts.index], fontsize=8)
    ax.set_xlabel(f"% of respondents (n = {n}){denominator_note}")
    ax.set_title(title, loc="left", color=INK, fontsize=9.5)
    ax.grid(axis="y", visible=False)
    for index, value in enumerate(counts.to_numpy()):
        ax.annotate(f"{value / n * 100:.0f}%  ({int(value)})",
                    xy=(value / n * 100, index), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=7.6, color=INK_2)
    ax.set_xlim(0, max(counts.to_numpy() / n * 100) * 1.38 or 1)


def diverging_likert(ax, frame: pd.DataFrame, title: str) -> None:
    """Stacked bars centred on the neutral category."""
    order = list(frame.columns)
    middle = len(order) // 2
    colours = ["#b3302a", "#e08a70", "#c9cdcb", "#7fc3a5", "#1baf7a"]
    if len(order) != 5:
        colours = [BLUE] * len(order)

    y = np.arange(len(frame))
    left = -frame.iloc[:, :middle].sum(axis=1).to_numpy() - frame.iloc[:, middle].to_numpy() / 2
    for index, column in enumerate(order):
        widths = frame[column].to_numpy()
        ax.barh(y, widths, left=left, height=0.6,
                color=colours[index % len(colours)], label=shorten(column, 22))
        left = left + widths

    ax.axvline(0, color=INK_2, linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels([shorten(i, 52) for i in frame.index], fontsize=8)
    ax.set_xlabel("% of respondents")
    ax.set_title(title, loc="left", color=INK)
    ax.grid(axis="y", visible=False)
    ax.legend(ncol=len(order), loc="upper center", bbox_to_anchor=(0.5, -0.22))


def save(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    for ax in np.atleast_1d(fig.axes).ravel():
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.savefig(FIGURES / f"{name}.pdf")
    fig.savefig(FIGURES / f"{name}.png")
    plt.close(fig)
    print(f"  wrote {name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--max-per-figure", type=int, default=4)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    data = pd.read_csv(args.csv)
    data = data.loc[:, ~data.columns.str.contains("Timestamp|Email Address", case=False)]
    total = len(data)
    print(f"Loaded {total} responses, {len(data.columns)} questions\n")

    results: dict = {"respondents": total, "questions": {}}
    singles, ordinals, multis, texts = [], [], [], []

    for column in data.columns:
        kind = classify(data[column])
        answered = int(data[column].notna().sum())
        entry = {"kind": kind, "answered": answered}

        if kind == "single":
            counts = data[column].dropna().astype(str).str.strip().value_counts()
            entry["counts"] = {k: int(v) for k, v in counts.items()}
            entry["percent"] = {k: round(v / answered * 100, 1) for k, v in counts.items()}
            singles.append((column, counts, answered))
        elif kind.startswith("multi"):
            sep = kind.split(":", 1)[1]
            parts = Counter()
            for value in data[column].dropna().astype(str):
                for piece in split_multi(value, sep):
                    parts[piece] += 1
            counts = pd.Series(parts).sort_values(ascending=False)
            entry["counts"] = {k: int(v) for k, v in counts.items()}
            entry["percent"] = {k: round(v / answered * 100, 1) for k, v in counts.items()}
            multis.append((column, counts, answered))
        elif kind == "numeric_scale":
            counts = data[column].dropna().astype(str).str.strip().value_counts()
            order = [v for v in ["1", "2", "3", "4", "5"] if v in counts.index]
            labels = {"1": "1 — not at all", "2": "2", "3": "3 — neutral",
                      "4": "4", "5": "5 — very much"}
            counts = counts.reindex(order).fillna(0)
            counts.index = [labels[v] for v in order]
            entry["counts"] = {k: int(v) for k, v in counts.items()}
            entry["percent"] = {k: round(v / answered * 100, 1) for k, v in counts.items()}
            entry["top_two_box"] = round(
                sum(v for k, v in counts.items() if k.startswith(("4", "5"))) / answered * 100, 1)
            ordinals.append((column, counts, answered))
        elif kind == "ordinal":
            counts = data[column].dropna().astype(str).str.strip().value_counts()
            order = ordinal_order(list(counts.index))
            entry["counts"] = {k: int(counts.get(k, 0)) for k in order}
            entry["percent"] = {k: round(counts.get(k, 0) / answered * 100, 1) for k in order}
            ordinals.append((column, counts.reindex(order).fillna(0), answered))
        elif kind == "text":
            responses = data[column].dropna().astype(str).str.strip()
            responses = responses[responses.str.len() > 2]
            entry["n_responses"] = int(len(responses))
            entry["examples"] = responses.head(12).tolist()
            texts.append((column, responses))

        results["questions"][column] = entry

    # ---- figures ------------------------------------------------------
    print("Figures:")
    # Ordinal and 1-5 scale questions are eligible for the grouped figures too;
    # merge them in BEFORE the groups are resolved, and remember which they are
    # so their bars stay in scale order rather than being sorted by frequency.
    scale_columns = {c for c, _, _ in ordinals}
    singles = singles + ordinals

    # Explicit grouping. Each entry lists distinctive substrings of the question
    # text, in the order the panels should appear, so a figure tells one story
    # rather than collecting whatever a regex happened to match.
    GROUPS = [
        ("fig3_11_respondent_profile", "Respondent profile",
         ["age group", "district", "type of area", "smart phone"]),
        ("fig3_12_flood_experience", "Flood exposure and the warning gap",
         ["experienced a flood", "frequently does flooding",
          "advance warning did you receive", "warning too late"]),
        ("fig3_13_current_warning", "How warnings reach people at present",
         ["did not affect your location", "how accurate was the warning",
          "how quickly do you normally", "main source of your warning"]),
        ("fig3_14_expectations", "Willingness, trust and preferred channel",
         ["willing to report", "trust an AI-based", "prefer to receive"]),
    ]
    groups = [(name, title,
               [c for frag in frags for c, _, _ in singles
                if frag.lower() in " ".join(c.split()).lower()])
              for name, title, frags in GROUPS]
    used: set[str] = set()
    lookup = {c: (counts, n) for c, counts, n in singles}
    palette = [BLUE, ORANGE, AQUA, YELLOW]

    for name, title, columns in groups:
        seen_here, ordered = set(), []
        for c in columns:
            if c not in used and c not in seen_here:
                seen_here.add(c); ordered.append(c)
        columns = ordered[: args.max_per_figure]
        if not columns:
            continue
        used.update(columns)
        rows = len(columns)
        fig, axes = plt.subplots(rows, 1, figsize=(7.0, 2.05 * rows + 0.5))
        for index, (ax, column) in enumerate(zip(np.atleast_1d(axes), columns)):
            counts, n = lookup[column]
            bar_panel(ax, counts, n, palette[index % 4], shorten(column, 78),
                      keep_order=column in scale_columns)
        fig.suptitle(title, x=0.005, ha="left", fontsize=11, fontweight="600", color=INK)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        save(fig, name)

    leftover = [(c, counts, n) for c, counts, n in singles if c not in used]
    if leftover:
        rows = min(len(leftover), 5)
        fig, axes = plt.subplots(rows, 1, figsize=(7.0, 2.05 * rows + 0.5))
        for index, (ax, (column, counts, n)) in enumerate(zip(np.atleast_1d(axes), leftover[:rows])):
            bar_panel(ax, counts, n, palette[index % 4], shorten(column, 78))
        fig.suptitle("Further responses", x=0.005, ha="left", fontsize=11,
                     fontweight="600", color=INK)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        save(fig, "fig3_16_further_responses")

    if multis:
        rows = min(len(multis), 3)
        fig, axes = plt.subplots(rows, 1, figsize=(7.0, 2.4 * rows + 0.5))
        for index, (ax, (column, counts, n)) in enumerate(zip(np.atleast_1d(axes), multis[:rows])):
            bar_panel(ax, counts, n, palette[index % 4], shorten(column, 78),
                      denominator_note=" · multiple selections permitted")
        fig.suptitle("Crowdsourcing willingness and information sources", x=0.005,
                     ha="left", fontsize=11, fontweight="600", color=INK)
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        save(fig, "fig3_15_crowdsourcing")

    if ordinals:
        frame = pd.DataFrame({
            shorten(column, 52): (counts / n * 100)
            for column, counts, n in ordinals
        }).T.fillna(0)
        fig, ax = plt.subplots(figsize=(7.2, 0.62 * len(frame) + 2.0))
        diverging_likert(ax, frame, "Attitude statements")
        save(fig, "fig3_17_attitudes")

    # ---- outputs ------------------------------------------------------
    (OUT / "survey_results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n  wrote survey/survey_results.json")

    lines = [f"# Survey results — {total} respondents", ""]
    for column, entry in results["questions"].items():
        if entry["kind"] in ("single", "ordinal") or entry["kind"].startswith("multi"):
            lines.append(f"**{column}**  (n = {entry['answered']})")
            lines.append("")
            lines.append("| Response | n | % |")
            lines.append("|---|---|---|")
            for option, count in entry["counts"].items():
                lines.append(f"| {option} | {count} | {entry['percent'][option]:.1f}% |")
            lines.append("")
        elif entry["kind"] == "text":
            lines.append(f"**{column}**  ({entry['n_responses']} free-text responses)")
            lines.append("")
            for example in entry["examples"][:8]:
                lines.append(f"> {example}")
            lines.append("")
    (OUT / "survey_summary.md").write_text("\n".join(lines))
    print("  wrote survey/survey_summary.md")

    if texts:
        print(f"\n  {len(texts)} free-text question(s) — read survey_summary.md and quote"
              f" selectively; do not tabulate them as if they were categorical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
