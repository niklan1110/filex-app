"""``office.convert``: Office to Office within one family (spec 6, plan task 35).

Documents (doc, docx, odt, rtf, txt), spreadsheets (xls, xlsx, ods, csv) and presentations
(ppt, pptx, odp) convert to any other format of the same family through the same LibreOffice
runner as ``office.to-pdf``. The target comes from the job (``ctx.target``), like every
converter. Text and CSV are written as UTF-8 (text with a BOM), CSV comma-separated; reading
them, the character set is detected (``_lo.text_charset``). A CSV holds one sheet only, and
plain text drops all formatting, so those two targets are marked lossy for the planner.
"""

from __future__ import annotations

from itertools import permutations
from pathlib import Path

from filex.formats import FORMATS
from filex.registry import Edge, Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label
from filex.tools.office import _lo

TIMEOUT_S = 300
_LOSSY = ("txt", "csv")

EDGES = [
    Edge(a, b, lossy=b in _LOSSY)
    for group in (_lo.DOCUMENTS, _lo.SHEETS, _lo.SLIDES)
    for a, b in permutations(group, 2)
]


@tool(
    id="office.convert",
    title="Convert Office files",
    summary="Convert between Word, Excel, PowerPoint and OpenDocument formats, CSV and text.",
    category="convert",
    group="office",
    converts=EDGES,
    files=Files(min=1, max=50, each=True),
    engines=["libreoffice"],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=1536),
    examples=[Example(inputs=["office/budget.xlsx"], target="ods")],
    keywords=["docx", "odt", "xlsx", "ods", "csv", "pptx", "odp", "word", "excel", "openoffice"],
)
def run(ctx: StepContext, src: Path, opts: ToolOptions) -> list[Path]:
    what = label(ctx)
    src_fmt, dst = _lo.format_of(src), ctx.target
    if dst is None:
        raise StepError("INVALID_OPTIONS", "Choose the format to convert to.")
    if not any(e.src == src_fmt and e.dst == dst for e in EDGES):
        raise StepError(
            "INVALID_OPTIONS",
            f"{what} ({_title(src_fmt)}) cannot be converted to {_title(dst)}.",
        )
    ctx.progress(0.0, "Converting with LibreOffice")
    convert_to = f"{dst}:{_lo.filter_for(dst)}"
    return [_lo.convert(src, dst, convert_to, ctx.out_dir, what, TIMEOUT_S)]


def _title(fmt: str) -> str:
    return FORMATS[fmt].title if fmt in FORMATS else fmt
