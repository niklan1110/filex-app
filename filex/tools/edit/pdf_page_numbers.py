"""``pdf.page-numbers``: print a page number on all or selected pages (spec 6.6, plan task 29).

Numbering counts the numbered pages: the first numbered page shows ``start``, the next one
``start + 1`` and so on, and ``{total}`` is the last number printed. "Skip the first page" leaves
a cover page unnumbered, so with the defaults a 10-page file reads "1" to "9" from page 2. The
template takes ``{n}`` and ``{total}``, e.g. ``Page {n} of {total}``; nothing else in braces.
Numbers are drawn with reportlab and stamped with pikepdf over the content (see ``_overlay``),
upright and placed as the reader sees each page, whatever its rotation. Everything else in the
file is kept. Full pikepdf rewrite.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field

from filex.registry import Example, Files, StepContext, ToolOptions, tool
from filex.tools._pdf import Pages, label, open_pdf, parse_ranges, save, select
from filex.tools.edit._overlay import (
    MM,
    POSITION_TITLES,
    Position,
    centre,
    draw_text,
    font_for,
    stamp,
    text_box,
)

_FIELD = re.compile(r"\{(n|total)\}")


def _check_template(text: str) -> str:
    rest = _FIELD.sub("", text)
    if "{n}" not in text:
        raise ValueError("The template needs {n} where the number goes.")
    if "{" in rest or "}" in rest:
        raise ValueError("Use only {n} and {total} in braces.")
    return text


class Options(ToolOptions):
    template: Annotated[str, AfterValidator(_check_template)] = Field(
        "{n}", title="Text ({n} = number, {total} = last number)", max_length=100
    )
    position: Position = Field(
        "bottom-center", title="Position", json_schema_extra={"x-enum-titles": POSITION_TITLES}
    )
    start: int = Field(1, title="First number", ge=0, le=1_000_000)
    skip_first: bool = Field(False, title="Skip the first page (cover)")
    font_size: int = Field(11, title="Font size (pt)", ge=5, le=72)
    margin_mm: int = Field(10, title="Distance from the edge (mm)", ge=0, le=100)
    pages: Pages = Field("", title="Pages to number (blank for all)")


@tool(
    id="pdf.page-numbers",
    title="Add page numbers",
    summary="Print page numbers like “Page 3 of 12” on every page or the ones you pick.",
    category="edit",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf", "reportlab"],
    examples=[
        Example(
            inputs=["pdf/text_10p.pdf"],
            options={"template": "Page {n} of {total}", "skipFirst": True},
        )
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES", "UNSUPPORTED_TEXT"],
    keywords=["numbering", "paginate", "pagination", "footer", "folio"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    font = font_for(_FIELD.sub("0123456789", opts.template), "page number text")
    margin = opts.margin_mm * MM
    with open_pdf(ctx, src, what) as pdf:
        count = len(pdf.pages)
        chosen = sorted({i for r in select(parse_ranges(opts.pages), count, what) for i in r})
        numbered = [i for i in chosen or range(count) if not (opts.skip_first and i == 0)]
        total = opts.start + len(numbered) - 1

        def drawer(text: str):
            w, h = text_box(text, font, opts.font_size)

            def draw(c, width: float, height: float) -> None:
                cx, cy = centre(w, h, opts.position, width, height, margin)
                draw_text(c, text, font, opts.font_size, cx, cy, 0)

            return draw

        jobs = []
        for k, i in enumerate(numbered):
            text = _FIELD.sub(
                lambda m, n=opts.start + k: str(n if m[1] == "n" else total), opts.template
            )
            jobs.append((i, text, drawer(text)))
        ctx.progress(0.3, f"Numbering {len(jobs)} pages")
        with stamp(pdf, jobs):
            ctx.progress(0.7, "Saving")
            return [save(pdf, ctx.out_dir / "numbered.pdf", what)]
