"""``pdf.remove-pages``: delete the selected pages (spec 6.1, plan task 20).

The other pages keep their order. Removing every page is refused. Form fields on kept pages and
the bookmarks to kept pages stay; a bookmark to a removed page is dropped unless bookmarks under
it are kept. The Info dictionary, XMP metadata and page labels are not carried over (as in
``pdf.merge``). Full pikepdf rewrite.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from filex.registry import Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import RequiredPages, label, open_pdf, parse_ranges, select, write_pages


class Options(ToolOptions):
    pages: RequiredPages = Field(title="Pages to remove")


@tool(
    id="pdf.remove-pages",
    title="Remove PDF pages",
    summary="Delete the pages you pick and keep the rest.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[Example(inputs=["pdf/text_10p.pdf"], options={"pages": "2, 5-7"})],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["delete", "drop", "discard", "erase", "cut", "omit"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    with open_pdf(ctx, src, what) as pdf:
        count = len(pdf.pages)
        removed = {i for r in select(parse_ranges(opts.pages), count, what) for i in r}
        keep = [i for i in range(count) if i not in removed]
        if not keep:
            raise StepError(
                "INVALID_OPTIONS", f"{what} would have no pages left. Keep at least one page."
            )
        ctx.progress(0.5, f"Copying {len(keep)} pages")
        return [write_pages(pdf, keep, ctx.out_dir / "pages-removed.pdf", what)]
