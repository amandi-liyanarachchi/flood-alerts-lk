"""Assemble Chapter 4 into submission formats with its figures placed.

    python -m thesis.build_chapter4

Produces, in thesis/submission/:
    CHAPTER_4.md     the chapter with figures placed inline and captioned
    CHAPTER_4.docx   Word, for pasting into a Word thesis template
    CHAPTER_4.tex    LaTeX body
    CHAPTER_4.pdf    rendered, so the placement can be checked before pasting

The source chapter marks each figure with a blockquote line beginning
`> **FIGURE 4.n** —`. This script replaces that marker with the image and a
numbered caption, so the numbering in the prose, the caption and the filename
cannot drift apart. A figure with no file yet (4.5, the mobile screens, which
must be captured on the emulator) is left as a visible placeholder rather than
being silently dropped.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FIGURES = HERE / "figures"
OUT = HERE / "submission"

# number -> (file stem or None, caption)
CAPTIONS: dict[str, tuple[str | None, str]] = {
    "4.1": (
        "fig4_01_module_map",
        "Module map. Modules are grouped by what may depend on what: nothing in a "
        "lower band imports from a higher one. The edge band is everything the "
        "outside world touches, the domain band holds the decisions the research "
        "is about, and the platform band is shared by both. `ml/`, `scripts/` and "
        "`tests/` are not part of the running service.",
    ),
    "4.2": (
        "fig4_02_dashboard",
        "The operations dashboard, against the seeded demonstration database "
        "(§4.14.2): 18 participants, 2,592 location pings, 54 crowd answers and "
        "seven Kelani stations. The map panel is unavailable because the mapping "
        "library is served from a content delivery network that was unreachable "
        "when this was captured — which is precisely the failure discussed in "
        "§4.8, and every other panel is unaffected.",
    ),
    "4.3": (
        "fig4_03_proposal_evidence",
        "An alert proposal awaiting authorisation. The operator sees the severity, "
        "the score, the region and the reasons in plain language, together with "
        "the complete evidence object, and may edit the message text before "
        "approving. No path publishes an alert without this step.",
    ),
    "4.4": (
        "fig4_04_gauges_table",
        "The gauge table. Nagalagam Street is annotated `ft→m`, recording that its "
        "published values were converted at ingest, and its state reads `MAJOR` "
        "while every other station reads `NORMAL` — the seeded flood scenario. "
        "This panel renders whether or not the mapping library is available.",
    ),
    "4.5": (
        None,
        "Mobile client screens. **To be captured on the Android emulator against a "
        "locally running server.** Recommended: the login screen, the home screen "
        "showing an active alert, and the flood-answer prompt.",
    ),
    "4.6": (
        "fig4_06_deployment",
        "Deployment topology. The same codebase supports both configurations; the "
        "only differences are the database URL and the process manager. Dashed "
        "borders mark components the service runs without — push delivery and "
        "upstream ingest degrade rather than fail — which is what makes the "
        "laptop configuration demonstrable with no network at all.",
    ),
}

MARKER = re.compile(r"^> \*\*FIGURE (4\.\d+)\*\*.*?(?=\n\n)", re.MULTILINE | re.DOTALL)


def place_figures(text: str) -> tuple[str, list[str]]:
    placed: list[str] = []

    def sub(match: re.Match[str]) -> str:
        number = match.group(1)
        stem, caption = CAPTIONS[number]
        if stem is None:
            placed.append(f"{number} (placeholder — no file)")
            return (
                f"> **Figure {number} — to be inserted.** {caption}"
            )
        png = FIGURES / f"{stem}.png"
        if not png.exists():
            raise FileNotFoundError(png)
        placed.append(f"{number} -> {png.name}")
        return f"![]({png})\n\n**Figure {number}.** {caption}"

    return MARKER.sub(sub, text), placed


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    source = (HERE / "CHAPTER_4.md").read_text()
    combined, placed = place_figures(source)

    missing = set(CAPTIONS) - {p.split()[0] for p in placed}
    if missing:
        raise SystemExit(f"figure markers not found in the chapter: {sorted(missing)}")

    md_path = OUT / "CHAPTER_4.md"
    md_path.write_text(combined)
    for line in placed:
        print(f"  figure {line}")
    print(f"  wrote {md_path.name}")

    common = [
        "pandoc", str(md_path),
        "--from", "markdown+pipe_tables+tex_math_dollars",
        "--resource-path", str(FIGURES),
    ]

    subprocess.run(common + ["-o", str(OUT / "CHAPTER_4.docx")], check=True)
    print("  wrote CHAPTER_4.docx")

    subprocess.run(common + ["--to", "latex", "-o", str(OUT / "CHAPTER_4.tex")], check=True)
    print("  wrote CHAPTER_4.tex")

    try:
        subprocess.run(
            common + ["--pdf-engine", "pdflatex", "-V", "geometry:margin=1in",
                      "-o", str(OUT / "CHAPTER_4.pdf")],
            check=True, capture_output=True, timeout=420,
        )
        print("  wrote CHAPTER_4.pdf")
    except subprocess.CalledProcessError as exc:
        print(f"  PDF failed:\n{exc.stderr.decode()[-2000:]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  PDF skipped ({type(exc).__name__}) -- the docx and tex are the deliverables")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
