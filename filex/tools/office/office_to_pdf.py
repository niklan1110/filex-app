"""``office.to-pdf``: Word, Excel, PowerPoint and OpenDocument files to PDF (spec 6, plan task 34).

One LibreOffice run per file (``filex.tools.office._lo``). Options: PDF/A-1b, 2b or 3b, tagged
PDF for screen readers, and a page range in the usual syntax (``1-3, 5, 8-``), applied by
LibreOffice in the order written; pages past the end are skipped, and a range that leaves no
page is an error. For spreadsheets the pages are the printed pages. The output's pages are
counted: more than ``FILEX_MAX_PAGES`` (a small sheet with a huge print range) is TOO_MANY_PAGES
and the file is deleted. A heavy step (the ``office`` group takes the heavy-step slot).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from filex.registry import (
    Edge,
    Example,
    Files,
    Limits,
    StepContext,
    StepError,
    ToolOptions,
    tool,
)
from filex.tools._pdf import Pages, label, parse_ranges
from filex.tools.office import _lo

TIMEOUT_S = 300
_PDFA = {"1b": 1, "2b": 2, "3b": 3}  # LibreOffice SelectPdfVersion


class Options(ToolOptions):
    pdfa: Literal["off", "1b", "2b", "3b"] = Field(
        "off",
        title="PDF/A (archiving)",
        json_schema_extra={"x-enum-titles": ["Off", "PDF/A-1b", "PDF/A-2b", "PDF/A-3b"]},
    )
    tagged: bool = Field(False, title="Tagged PDF (accessibility)")
    pages: Pages = Field("", title="Pages (blank for all)")


def export_options(opts: Options) -> dict[str, Any]:
    """LibreOffice PDF export filter options for ``opts``; defaults are left out."""
    out: dict[str, Any] = {}
    if opts.pdfa != "off":
        out["SelectPdfVersion"] = _PDFA[opts.pdfa]
    if opts.tagged:
        out["UseTaggedPDF"] = True
    if ranges := parse_ranges(opts.pages):
        out["PageRange"] = ",".join(str(a) if a == b else f"{a}-{b or ''}" for _, a, b in ranges)
    return out


@tool(
    id="office.to-pdf",
    title="Office to PDF",
    summary="Turn Word, Excel, PowerPoint and OpenDocument files into PDF.",
    category="convert",
    group="office",
    converts=[Edge(f, "pdf") for f in _lo.OFFICE if f not in ("txt", "csv")],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["libreoffice"],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=1536),
    examples=[Example(inputs=["office/report.docx"], options={"tagged": True}, target="pdf")],
    errors=["TOO_MANY_PAGES"],
    keywords=["word", "excel", "powerpoint", "docx", "xlsx", "pptx", "odt", "libreoffice"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    ctx.progress(0.0, "Converting with LibreOffice")
    convert_to = _lo.pdf_filter(_lo.format_of(src), export_options(opts))
    out = _lo.convert(src, "pdf", convert_to, ctx.out_dir, what, TIMEOUT_S)
    ctx.progress(0.9, "Counting pages")
    _check_pages(ctx, out, what)
    return [out]


def _check_pages(ctx: StepContext, out: Path, what: str) -> None:
    """Delete ``out`` and refuse it when it is not a readable PDF or has too many pages."""
    import pikepdf

    try:
        with pikepdf.open(out) as pdf:
            count = len(pdf.pages)
    except pikepdf.PdfError as e:
        out.unlink(missing_ok=True)
        raise StepError("ENGINE_FAILED", f"{what} could not be converted.") from e
    if count > ctx.max_pages:
        out.unlink(missing_ok=True)
        raise StepError(
            "TOO_MANY_PAGES",
            f"{what} makes {count} pages as a PDF; the limit is {ctx.max_pages}.",
        )
