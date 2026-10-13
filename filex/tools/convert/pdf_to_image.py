"""``pdf.to-image``: render PDF pages to PNG, JPEG, WebP or TIFF (spec 6, plan task 28).

pypdfium2 (PDFium) renders each selected page at the chosen DPI, form fields included, on white.
The format is the job's target when one is given, else the ``format`` option. Pages
are numbered as in the PDF; a page selected twice is rendered once. Each page is one ``RESULT``
output named ``<input name>-p001.png`` (``p001.png`` when the input has no name); the API zips
them for download.

Limits (review H3 fix 4): DPI at most 600, every selected page's pixel count checked against
``FILEX_MAX_PIXELS`` before anything renders, at most ``MAX_OUTPUTS`` pages per file and
``FILEX_MAX_PAGES`` pages in the input.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.registry import (
    MAX_OUTPUTS,
    Edge,
    Example,
    Files,
    Output,
    StepContext,
    StepError,
    ToolOptions,
    tool,
)
from filex.tools._pdf import Pages, label, open_pdfium, parse_ranges, select

FORMATS = ("png", "jpg", "webp", "tiff")
_SAVE = {
    "png": {"format": "PNG"},
    "jpg": {"format": "JPEG", "quality": 90},
    "webp": {"format": "WEBP", "quality": 90},
    "tiff": {"format": "TIFF", "compression": "tiff_lzw"},
}


class Options(ToolOptions):
    format: Literal["png", "jpg", "webp", "tiff"] = Field(
        "png",
        title="Image format",
        json_schema_extra={"x-enum-titles": ["PNG", "JPEG", "WebP", "TIFF"]},
    )
    dpi: int = Field(150, ge=36, le=600, title="Resolution (DPI)")
    pages: Pages = Field("", title="Pages (blank for all)")


@tool(
    id="pdf.to-image",
    title="PDF to images",
    summary="Save PDF pages as PNG, JPEG, WebP or TIFF images at the resolution you choose.",
    category="convert",
    group="core",
    converts=[Edge("pdf", f, lossy=True) for f in FORMATS],
    files=Files(min=1, max=20, each=True),
    options=Options,
    engines=["pypdfium2", "pillow"],
    examples=[
        Example(inputs=["pdf/text_10p.pdf"], options={"dpi": 72, "pages": "1-2"}, target="png")
    ],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["png", "jpg", "jpeg", "picture", "rasterize", "render", "screenshot", "tiff"],
    target_option="format",
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Output]:
    what = label(ctx)
    fmt = ctx.target or opts.format
    if fmt not in FORMATS:
        raise StepError("INVALID_OPTIONS", f"PDF pages cannot be saved as {fmt}.")
    scale = opts.dpi / 72
    pdf = open_pdfium(ctx, src, what)
    try:
        count = len(pdf)
        ranges = parse_ranges(opts.pages)
        chosen = [i for r in select(ranges, count, what) for i in r] if ranges else range(count)
        pages = list(dict.fromkeys(chosen))
        if len(pages) > MAX_OUTPUTS:
            raise StepError(
                "INVALID_OPTIONS",
                f"This would make {len(pages)} images; the limit is {MAX_OUTPUTS}. "
                "Choose fewer pages.",
            )
        for i in pages:
            w, h = (math.ceil(x * scale) for x in pdf.get_page_size(i))
            if w * h > ctx.max_pixels:
                raise StepError(
                    "INVALID_OPTIONS",
                    f"Page {i + 1} would be {w}x{h} pixels at {opts.dpi} DPI; the limit is "
                    f"{ctx.max_pixels / 1e6:g} megapixels. Choose a lower resolution.",
                )
        name = ctx.input_names[0] if ctx.input_names else ""
        stem = (name.rpartition(".")[0] if "." in name else name)[:100]
        prefix = f"{stem}-" if stem.strip() else ""
        width = max(3, len(str(count)))
        out = []
        for n, i in enumerate(pages):
            ctx.progress(n / len(pages), f"Rendering page {i + 1}")
            image = pdf[i].render(scale=scale, may_draw_forms=True).to_pil()
            file = f"p{i + 1:0{width}}.{fmt}"
            image.save(ctx.out_dir / file, dpi=(opts.dpi, opts.dpi), **_SAVE[fmt])
            image.close()
            out.append(Output(ctx.out_dir / file, prefix + file))
        return out
    finally:
        pdf.close()
