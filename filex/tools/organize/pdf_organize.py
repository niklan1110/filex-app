"""``pdf.organize``: reorder, turn, delete and duplicate pages in one job (spec 6.1, plan task 21).

The options are a page plan, the list the UI's page grid produces: one entry per output page,
in output order, naming a source page (from 1) and an optional clockwise turn. A page may be
listed more than once (each copy is its own page and can be turned on its own); a page that is
not listed is left out. The plan is checked against the real page count when the step runs.
The turn adds to the page's existing ``/Rotate``, as in ``pdf.rotate``. Form fields and the
bookmarks to kept pages come along (a bookmark goes to the first copy); the Info dictionary,
XMP metadata and page labels are not carried over (as in ``pdf.extract-pages``). Full pikepdf
rewrite without encryption.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.registry import MAX_LIST_ITEMS, Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, open_pdf, write_pages


class PageStep(ToolOptions):
    page: int = Field(title="Page number", ge=1, le=100_000)
    rotate: Literal[0, 90, 180, 270] = Field(
        0,
        title="Turn clockwise by (degrees)",
        json_schema_extra={"x-enum-titles": ["No turn", "90°", "180°", "270°"]},
    )


class Options(ToolOptions):
    pages: list[PageStep] = Field(
        title="Pages in their new order",
        description="One entry per page of the result. Pages left out are deleted; a page "
        "listed twice is duplicated.",
        min_length=1,
        max_length=MAX_LIST_ITEMS,
        json_schema_extra={"x-widget": "page-grid"},
    )


@tool(
    id="pdf.organize",
    title="Organize PDF pages",
    summary="Reorder, turn, delete and duplicate pages, all in one go.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=1, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[
        Example(
            inputs=["pdf/text_10p.pdf"],
            options={"pages": [{"page": 3, "rotate": 90}, {"page": 1}, {"page": 1}]},
        )
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["reorder", "rearrange", "arrange", "sort", "move", "duplicate", "delete", "pages"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    with open_pdf(ctx, src, what) as pdf:
        count = len(pdf.pages)
        missing = next((s.page for s in opts.pages if s.page > count), None)
        if missing is not None:
            pages = "1 page" if count == 1 else f"{count} pages"
            raise StepError(
                "INVALID_OPTIONS", f"{what} has {pages}, so page {missing} does not exist."
            )
        if len(opts.pages) > ctx.max_pages:
            raise StepError(
                "INVALID_OPTIONS",
                f"The result would have {len(opts.pages)} pages; the limit is {ctx.max_pages}.",
            )
        ctx.progress(0.5, f"Arranging {len(opts.pages)} pages")
        return [
            write_pages(
                pdf,
                [s.page - 1 for s in opts.pages],
                ctx.out_dir / "organized.pdf",
                what,
                turns=[s.rotate for s in opts.pages],
            )
        ]
