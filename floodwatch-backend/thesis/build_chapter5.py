"""Assemble Chapter 5 into submission formats with its figures placed.

    python -m thesis.build_chapter5

Same contract as build_chapter4.py: the chapter marks each figure with a
blockquote line beginning `> **FIGURE 5.n** —`, which this script replaces with
the image and a numbered caption. Figures 5.1-5.9 come from the experiment run
(`python -m ml.run_experiment`); Figure 5.10 comes from a live measurement
against a running server (`python -m scripts.measure_latency`, then
`python -m thesis.make_latency_figure`).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
OUT = HERE / "submission"

CAPTIONS: dict[str, tuple[str, str]] = {
    "5.1": (
        "fig04_pr_curves",
        "Precision–recall curves on the held-out test split at the six-hour "
        "horizon. The dotted line is the random-classifier baseline, equal to the "
        "positive-class rate of 1.46%. Both learned models approximately double "
        "the average precision of the stronger baseline.",
    ),
    "5.2": (
        "fig05_roc",
        "ROC curves for the same four systems. Read against Figure 5.1, this is "
        "the argument for the metric choice: the rainfall threshold reaches "
        "ROC-AUC 0.866 while nearly two-thirds of its warnings are wrong. Under "
        "1.46% prevalence, ROC-AUC is dominated by the negative class and "
        "flatters weak models.",
    ),
    "5.3": (
        "fig06_calibration",
        "Reliability diagram: predicted probability against observed frequency, "
        "in deciles, after isotonic calibration fitted on the validation split "
        "only. Proximity to the diagonal is what allows the operator interface to "
        "present a score as an interpretable quantity rather than a raw number.",
    ),
    "5.4": (
        "fig11_confusion",
        "Confusion matrix for the crowd-augmented model at its operating point, "
        "chosen to maximise F1 on validation and applied unchanged to test. The "
        "104 false positives should be read against M1's 143 — the crowd's "
        "measured contribution is this reduction, not additional detections.",
    ),
    "5.5": (
        "fig08_ablation",
        "Ablation by feature group, mean ± standard deviation over three learner "
        "seeds. Rainfall carries the result. Upstream features contribute nothing "
        "measurable, which the travel-time diagnostic in §5.11.1 explains. "
        "Removing the temporal group *improves* performance — a mild overfitting "
        "result reported rather than suppressed.",
    ),
    "5.6": (
        "fig07_importance",
        "Permutation importance on the test split for the crowd-augmented model. "
        "The antecedent precipitation index ranks first, which is "
        "hydrologically sensible and was not imposed. Two crowd features appear "
        "in the top eight, above every river feature except headroom.",
    ),
    "5.7": (
        "fig10_crowd_sensitivity",
        "Crowdsourcing gain in average precision across panel size and reporter "
        "detection rate, with the physical baseline held constant by "
        "construction. Panel size dominates reporter reliability throughout the "
        "swept range — the most directly actionable result in the chapter.",
    ),
    "5.8": (
        "fig09_lead_time",
        "Lead-time distribution by model and flood mechanism, measured per "
        "episode with a continuous-warning requirement. The learned models detect "
        "four to six times as many episodes as the baselines, at roughly one hour "
        "of median warning. The baselines' longer apparent leads come from "
        "detecting only the large, slow, river-driven events.",
    ),
    "5.9": (
        "fig12_spatial_holdout",
        "Average precision when each basin is held out entirely from training, "
        "against a model trained on all three and evaluated on identical rows. "
        "Degradation of 0.001–0.045 indicates the model relies on transferable "
        "hydrological relationships rather than on memorised individual gauges.",
    ),
    "5.10": (
        "fig5_10_latency",
        "Warning pipeline latency by stage, 30 repetitions against a running "
        "server, on a log axis. Bars are medians, pale extensions reach the 90th "
        "percentile. The two dashed references are not software: the operator's "
        "decision and the median lead time exceed the entire machine path by "
        "three to four orders of magnitude, which is what the measurement is "
        "actually for.",
    ),
}

MARKER = re.compile(r"^> \*\*FIGURE (5\.\d+)\*\*.*?(?=\n\n)", re.MULTILINE | re.DOTALL)


def place_figures(text: str) -> tuple[str, list[str]]:
    placed: list[str] = []

    def sub(match: re.Match[str]) -> str:
        number = match.group(1)
        stem, caption = CAPTIONS[number]
        png = FIGURES / f"{stem}.png"
        if not png.exists():
            raise FileNotFoundError(png)
        placed.append(f"{number} -> {png.name}")
        return f"![]({png})\n\n**Figure {number}.** {caption}"

    return MARKER.sub(sub, text), placed


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    source = (HERE / "CHAPTER_5.md").read_text()
    combined, placed = place_figures(source)

    missing = set(CAPTIONS) - {p.split()[0] for p in placed}
    if missing:
        raise SystemExit(f"figure markers not found in the chapter: {sorted(missing)}")

    md_path = OUT / "CHAPTER_5.md"
    md_path.write_text(combined)
    for line in placed:
        print(f"  figure {line}")
    print(f"  wrote {md_path.name}")

    common = [
        "pandoc", str(md_path),
        "--from", "markdown+pipe_tables+tex_math_dollars",
        "--resource-path", str(FIGURES),
    ]

    subprocess.run(common + ["-o", str(OUT / "CHAPTER_5.docx")], check=True)
    print("  wrote CHAPTER_5.docx")

    subprocess.run(common + ["--to", "latex", "-o", str(OUT / "CHAPTER_5.tex")], check=True)
    print("  wrote CHAPTER_5.tex")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
