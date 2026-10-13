"""``pdf.rotate``: turn all or selected pages clockwise (spec 6.1, plan task 20).

The angle is added to each page's existing ``/Rotate`` (inherited values included), so a page
already shown sideways turns from where it is. Only the page's display rotation changes; the
content is not re-drawn. Everything else (metadata, bookmarks, form fields, layers) is kept,
since rotating does not change what the document contains. The output is a full pikepdf
rewrite without encryption; owner-only restrictions are removed with a warning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.registry import Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import Pages, label, open_pdf, parse_ranges, save, select


class Options(ToolOptions):
    angle: Literal[90, 180, 270] = Field(90, title="Turn clockwise by (degrees)")
    pages: Pages = Field("", title="Pages to turn (blank for all)")


@tool(
    id="pdf.rotate",
    title="Rotate PDF pages",
    summary="Turn all pages, or only the ones you pick, by 90, 180 or 270 degrees.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[Example(inputs=["pdf/mixed_pages.pdf"], options={"angle": 90, "pages": "1-2"})],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["turn", "orientation", "landscape", "portrait", "flip", "sideways"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import pikepdf

    what = label(ctx)
    with open_pdf(ctx, src, what) as pdf:
        count = len(pdf.pages)
        ranges = parse_ranges(opts.pages)
        chosen = sorted({i for r in select(ranges, count, what) for i in r}) or range(count)
        try:
            for i in chosen:
                pdf.pages[i].rotate(opts.angle, relative=True)
        except pikepdf.PdfError as e:
            raise StepError("INVALID_PDF", f"{what} is damaged and cannot be read.") from e
        ctx.progress(0.5, "Saving")
        return [save(pdf, ctx.out_dir / "rotated.pdf", what)]
