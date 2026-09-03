"""Assemble the three chapters into a submission-ready document.

    python -m thesis.assemble_submission

Produces, in thesis/submission/:
    SUBMISSION.md      the three chapters combined, with figures placed inline
    SUBMISSION.docx    Word, for pasting into a Word thesis template
    SUBMISSION.tex     LaTeX body, for \\input into a LaTeX thesis
    SUBMISSION.pdf     rendered, so you can check it before pasting

Figures are inserted immediately after the paragraph that first refers to them,
with numbered captions. Chapter text refers to "Figure N", which maps to
figures/figNN_*.pdf — so the numbering in the prose, the caption and the filename
all agree, and stay agreeing if the experiment is re-run.

Renumbering into your own thesis: the figures are numbered 1-12 within this
chapter. If your document numbers figures continuously, search and replace
"Figure " with "Figure 4." (or whichever chapter number) in both the prose and
the captions — they use the same string.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
OUT = HERE / "submission"

# Chapter prose says "Figure N"; this maps N to its file stem and caption.
CAPTIONS: dict[int, tuple[str, str]] = {
    1: ("fig01_hydrograph",
        "Rainfall and river stage during a flood event. Upper panel: hourly rainfall. "
        "Lower panel: river stage against the station's published alert, minor flood and "
        "major flood levels, with individual gauge readings marked and the flood window "
        "shaded. Rainfall is shown in a separate panel rather than on a secondary axis."),
    2: ("fig02_seasonality",
        "Mean monthly rainfall by basin, showing the bimodal monsoon cycle. The two "
        "south-western basins peak during the south-west monsoon (May-September); the "
        "north-eastern basin peaks during the north-east monsoon (December-February)."),
    3: ("fig03_class_balance",
        "Positive-class prevalence for the four candidate prediction targets. All are "
        "severely imbalanced, which is why average precision rather than accuracy or "
        "ROC-AUC is used as the primary evaluation metric."),
    4: ("fig04_pr_curves",
        "Precision-recall curves on the held-out test split at the six-hour horizon. The "
        "dotted line marks the random-classifier baseline, equal to the positive-class "
        "rate. Both learned models approximately double the average precision of the "
        "stronger baseline."),
    5: ("fig05_roc",
        "ROC curves for the same four systems. Comparison with Figure 4 illustrates why "
        "average precision is the primary metric: under severe class imbalance ROC-AUC is "
        "dominated by the large negative class and flatters weak models."),
    6: ("fig06_calibration",
        "Reliability diagram: predicted probability against observed frequency, in "
        "deciles, after isotonic calibration fitted on the validation split. Proximity to "
        "the diagonal indicates that a score of 0.7 corresponds to flooding in "
        "approximately 70% of such cases."),
    7: ("fig07_importance",
        "Permutation feature importance for the crowd-augmented model on the test split: "
        "the drop in average precision when each feature is randomly permuted, five "
        "repeats. Crowdsourced features are shown in a contrasting colour."),
    8: ("fig08_ablation",
        "Average precision when each feature group is removed, mean of three learner seeds "
        "with standard-deviation error bars. Removing the temporal group improves "
        "performance, indicating that seasonal features do not transfer across a temporal "
        "split."),
    9: ("fig09_lead_time",
        "Distribution of warning lead time before flood onset, per episode, for both "
        "learned models. Lead time requires the warning to have been continuously active "
        "up to onset."),
    10: ("fig10_crowd_sensitivity",
         "Gain in average precision from the crowdsourced layer as a function of "
         "participants per region and reporter detection rate. The physical world is held "
         "identical across all cells, so every difference is attributable to the crowd."),
    11: ("fig11_confusion",
         "Confusion matrix for the crowd-augmented model at its operating point, chosen to "
         "maximise F1 on the validation split and applied unchanged to test. Counts and "
         "row-normalised percentages."),
    12: ("fig12_spatial_holdout",
         "Average precision when each basin is held out entirely from training, against a "
         "model trained on all three, evaluated on identical rows. Modest degradation "
         "indicates reliance on transferable hydrological relationships rather than "
         "memorisation of individual gauges."),
}

ARCHITECTURE_CAPTION = (
    "System architecture. Physical observations and crowdsourced reports are fused into a "
    "calibrated risk score per region cell. Every path from a model score to a user's "
    "device passes through a human authorisation step; no alert is published "
    "automatically. The dashed return path shows that the mobile client is simultaneously "
    "a sensor and a recipient."
)


def place_figures(text: str) -> str:
    """Insert each figure after the paragraph that first mentions it."""
    paragraphs = text.split("\n\n")
    placed: set[int] = set()
    out: list[str] = []

    for paragraph in paragraphs:
        out.append(paragraph)
        for number in sorted(CAPTIONS):
            if number in placed:
                continue
            if re.search(rf"\*\*Figure {number}\*\*", paragraph):
                stem, caption = CAPTIONS[number]
                out.append(f"![]({FIGURES / (stem + '.png')})")
                out.append(f"**Figure {number}.** {caption}")
                placed.add(number)

    missing = set(CAPTIONS) - placed
    if missing:
        print(f"  note: figures with no in-text reference, appended at the end: {sorted(missing)}")
        for number in sorted(missing):
            stem, caption = CAPTIONS[number]
            out.append(f"![]({FIGURES / (stem + '.png')})")
            out.append(f"**Figure {number}.** {caption}")

    return "\n\n".join(out)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    methodology = (HERE / "CHAPTER_METHODOLOGY.md").read_text()
    results = (HERE / "CHAPTER_RESULTS.md").read_text()
    discussion = (HERE / "CHAPTER_DISCUSSION.md").read_text()

    # The architecture figure belongs with the system design, so it leads.
    architecture = (
        "# System architecture\n\n"
        f"![]({FIGURES / 'fig00_architecture.png'})\n\n"
        f"**Figure A.** {ARCHITECTURE_CAPTION}\n\n"
    )

    combined = architecture + place_figures(
        methodology + "\n\n" + results + "\n\n" + discussion
    )

    # Demote every heading one level so the chapters nest under a thesis chapter.
    combined = re.sub(r"^(#{1,5}) ", r"#\1 ", combined, flags=re.MULTILINE)

    md_path = OUT / "SUBMISSION.md"
    md_path.write_text(combined)
    print(f"  wrote {md_path.name}")

    common = [
        "pandoc", str(md_path),
        "--from", "markdown+pipe_tables+tex_math_dollars",
        "--resource-path", str(FIGURES),
    ]

    subprocess.run(common + ["-o", str(OUT / "SUBMISSION.docx")], check=True)
    print("  wrote SUBMISSION.docx")

    subprocess.run(common + ["--to", "latex", "-o", str(OUT / "SUBMISSION.tex")], check=True)
    print("  wrote SUBMISSION.tex")

    try:
        subprocess.run(
            common + ["--pdf-engine", "pdflatex", "-V", "geometry:margin=1in",
                      "-o", str(OUT / "SUBMISSION.pdf")],
            check=True, capture_output=True, timeout=300,
        )
        print("  wrote SUBMISSION.pdf")
    except Exception as exc:  # noqa: BLE001
        print(f"  PDF skipped ({type(exc).__name__}) -- the docx and tex are the deliverables")

    # Vector figures alongside, for a LaTeX build that wants them.
    vector = OUT / "figures_pdf"
    vector.mkdir(exist_ok=True)
    for pdf in FIGURES.glob("*.pdf"):
        shutil.copy(pdf, vector / pdf.name)
    print(f"  copied {len(list(vector.glob('*.pdf')))} vector figures to figures_pdf/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
