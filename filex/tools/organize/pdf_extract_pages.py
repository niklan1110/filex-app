"""``pdf.extract-pages``: keep only the selected pages (spec 6.1, plan task 20).

Pages stay in document order and each appears once, whatever order the ranges are written in
(reordering is ``pdf.organize``). Form fields and the bookmarks to kept pages come along; a
bookmark to a dropped page is dropped unless bookmarks under it are kept. The Info dictionary,
XMP metadata and page labels are not carried over (as in ``pdf.merge``). Full pikepdf rewrite.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from filex.registry import Example, Files, StepContext, ToolOptions, tool
from filex.tools._pdf import RequiredPages, label, open_pdf, parse_ranges, select, write_pages


class Options(ToolOptions):
    pages: RequiredPages = Field(title="Pages to keep")


@tool(
    id="pdf.extract-pages",
    title="Extract PDF pages",
    summary="Make a PDF that has only the pages you pick.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[Example(inputs=["pdf/text_10p.pdf"], options={"pages": "1-3, 8-"})],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["select", "pick", "keep", "pull", "subset", "save-pages"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    with open_pdf(ctx, src, what) as pdf:
        chosen = select(parse_ranges(opts.pages), len(pdf.pages), what)
        keep = sorted({i for r in chosen for i in r})
        ctx.progress(0.5, f"Copying {len(keep)} pages")
        return [write_pages(pdf, keep, ctx.out_dir / "extracted.pdf", what)]
