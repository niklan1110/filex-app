"""``pdf.split``: cut one PDF into several (spec 6.1, plan task 20).

Modes: every N pages (N = 1 gives one file per page), by ranges (one file per range, in the
order written; ranges may overlap), or at each top-level bookmark (pages before the first
bookmark become the first file). Each part is a new PDF built like ``pdf.extract-pages``: form
fields and the bookmarks into that part come along, the Info dictionary, XMP metadata and page
labels do not. Every part is a ``RESULT`` output named ``<input name>-part-001.pdf`` (or
``part-001.pdf`` when the input has no name); the API zips them for download. At most
``MAX_OUTPUTS`` parts, the runner's per-step output cap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from filex.registry import (
    MAX_OUTPUTS,
    Example,
    Files,
    Output,
    StepContext,
    StepError,
    ToolOptions,
    tool,
)
from filex.tools._pdf import (
    Pages,
    label,
    open_pdf,
    parse_ranges,
    select,
    top_level_bookmarks,
    write_pages,
)


class Options(ToolOptions):
    mode: Literal["every", "ranges", "bookmarks"] = Field(
        "every",
        title="Split",
        json_schema_extra={
            "x-enum-titles": ["Every N pages", "By page ranges", "At bookmarks"],
            "x-enum-descriptions": [
                "The same number of pages in each file",
                "One file per range",
                "One file per top-level bookmark",
            ],
        },
    )
    every: int = Field(1, ge=1, le=10000, title="Pages per file")
    ranges: Pages = Field("", title="Ranges (one file each)", validate_default=True)

    @field_validator("ranges")
    @classmethod
    def _ranges_for_ranges_mode(cls, v: str, info: ValidationInfo) -> str:
        if info.data.get("mode") == "ranges" and not parse_ranges(v):
            raise PydanticCustomError("page_range", "Enter at least one range, like 1-3, 4-6, 7-.")
        return v


@tool(
    id="pdf.split",
    title="Split PDF",
    summary="Cut a PDF into several files: every few pages, by page ranges or by bookmarks.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=1),
    options=Options,
    engines=["pikepdf"],
    examples=[
        Example(inputs=["pdf/text_10p.pdf"], options={"mode": "ranges", "ranges": "1-3, 4-"})
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES", "NO_BOOKMARKS"],
    keywords=["separate", "divide", "cut", "burst", "break", "chapters"],
)
def run(ctx: StepContext, src: list[Path], opts: Options) -> list[Output]:
    what = label(ctx)
    with open_pdf(ctx, src[0], what) as pdf:
        parts = _parts(pdf, len(pdf.pages), opts, what)
        if len(parts) > MAX_OUTPUTS:
            raise StepError(
                "INVALID_OPTIONS",
                f"This would make {len(parts)} files; the limit is {MAX_OUTPUTS}. "
                "Put more pages in each file.",
            )
        width = max(3, len(str(len(parts))))
        name = ctx.input_names[0] if ctx.input_names else ""
        stem = (name.rpartition(".")[0] if "." in name else name)[:100]  # downloads cut at 150
        prefix = f"{stem}-" if stem.strip() else ""
        out = []
        for n, pages in enumerate(parts, 1):
            ctx.progress((n - 1) / len(parts), f"Writing file {n} of {len(parts)}")
            part = f"part-{n:0{width}}.pdf"
            path = write_pages(pdf, pages, ctx.out_dir / part, what)
            out.append(Output(path, prefix + part))
        return out


def _parts(pdf: object, count: int, opts: Options, what: str) -> list[range]:
    if opts.mode == "ranges":
        return select(parse_ranges(opts.ranges), count, what)
    if opts.mode == "every":
        return [range(i, min(i + opts.every, count)) for i in range(0, count, opts.every)]
    starts = top_level_bookmarks(pdf)
    if not starts:
        raise StepError("NO_BOOKMARKS", f"{what} has no bookmarks to split at.")
    if starts[0] != 0:
        starts.insert(0, 0)
    return [range(a, b) for a, b in zip(starts, [*starts[1:], count])]
