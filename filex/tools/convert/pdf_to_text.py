"""``pdf.to-text``: extract a PDF's text layer as UTF-8 plain text (spec 6, plan task 28).

pypdfium2 (PDFium) reads the text in reading order. Pages end with a line break and are separated
by a form feed (``\\f``), like Poppler's ``pdftotext``. With ``layout`` on, each text run is put
at its column on the page (estimated from the average character width) and wide vertical gaps
become blank lines, so columns and tables stay roughly aligned.

A PDF with no text at all (a scan) still gives a file of empty pages, with a ``NO_TEXT``
warning that suggests running OCR first.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from pydantic import Field

from filex.registry import Edge, Example, Files, StepContext, ToolOptions, tool
from filex.tools._pdf import label, open_pdfium


class Options(ToolOptions):
    layout: bool = Field(False, title="Keep the page layout")


@tool(
    id="pdf.to-text",
    title="PDF to text",
    summary="Pull the text out of a PDF into a plain text file, optionally keeping the layout.",
    category="convert",
    group="core",
    converts=[Edge("pdf", "txt", lossy=True)],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pypdfium2"],
    examples=[Example(inputs=["pdf/text_1p.pdf"], options={"layout": True}, target="txt")],
    warnings=["NO_TEXT"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["txt", "extract", "copy", "words", "plain"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    pdf = open_pdfium(ctx, src, what)
    try:
        texts = []
        for i in range(len(pdf)):
            ctx.progress(i / len(pdf), f"Reading page {i + 1}")
            page = pdf[i]
            textpage = page.get_textpage()
            text = _layout(textpage) if opts.layout else textpage.get_text_range()
            textpage.close()
            page.close()
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            texts.append("\n".join(line.rstrip() for line in lines).strip("\n") + "\n")
    finally:
        pdf.close()
    if not any(t.strip() for t in texts):
        ctx.warn(
            "NO_TEXT",
            f"{what} has no text layer; it looks like a scan. Run OCR on it first, then "
            "extract the text.",
        )
    target = ctx.out_dir / "text.txt"
    target.write_text("\f".join(texts), encoding="utf-8", newline="\n")
    return [target]


def _layout(textpage: Any) -> str:
    """Text runs placed on a character grid by their position on the page."""
    runs = []
    for i in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(i)
        text = textpage.get_text_bounded(left, bottom, right, top).replace("\r", "").strip("\n")
        if text.strip():
            runs.append((top, bottom, left, right, text))
    if not runs:
        return ""
    char_w = statistics.median((r[3] - r[2]) / max(len(r[4]), 1) for r in runs) or 1.0
    line_h = statistics.median(r[0] - r[1] for r in runs) or 1.0
    x0 = min(r[2] for r in runs)
    lines: list[list[tuple[float, str]]] = []
    tops: list[float] = []
    for top, _bottom, left, _right, text in sorted(runs, key=lambda r: (-r[0], r[2])):
        if tops and abs(tops[-1] - top) < line_h / 2:
            lines[-1].append((left, text))
            continue
        if tops and tops[-1] - top > line_h * 1.8:
            lines.append([])  # a blank line for a wide gap
            tops.append(top)
        lines.append([(left, text)])
        tops.append(top)
    out = []
    for line in lines:
        row = ""
        for left, text in sorted(line):
            col = round((left - x0) / char_w)
            row += " " * max(col - len(row), 1 if row else 0) + text
        out.append(row)
    return "\n".join(out)
