"""``pdf.to-cbz``: render PDF pages into a CBZ comic book (spec 6.4, plan task 52).

pypdfium2 renders every page at the chosen DPI (form fields included, on white), as in
``pdf.to-image``; each page becomes ``0001.jpg`` (or ``.png``) in a zip, stored without
recompression, written page by page so only one page image is in memory at a time. No CBR
output: writing RAR needs the non-free ``rar`` tool.

Limits (review H3 fix 4): DPI at most 600, every page's pixel count checked against
``FILEX_MAX_PIXELS`` before anything renders, ``FILEX_MAX_PAGES`` pages in the input.
"""

from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.registry import Edge, Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, open_pdfium

_SAVE = {"jpg": {"format": "JPEG", "quality": 90}, "png": {"format": "PNG"}}


class Options(ToolOptions):
    dpi: int = Field(150, ge=36, le=600, title="Resolution (DPI)")
    image_format: Literal["jpg", "png"] = Field(
        "jpg",
        title="Page images",
        json_schema_extra={"x-enum-descriptions": ["JPEG (smaller)", "PNG (lossless, larger)"]},
    )


@tool(
    id="pdf.to-cbz",
    title="PDF to comic book (CBZ)",
    summary="Turn a PDF into a CBZ comic book with one image per page.",
    category="ebook",
    group="ebook",
    converts=[Edge("pdf", "cbz", lossy=True)],
    files=Files(min=1, max=20, each=True),
    options=Options,
    engines=["pypdfium2", "pillow"],
    limits=Limits(timeout_s=900, memory_mb=1024),
    examples=[Example(inputs=["pdf/text_10p.pdf"], options={"dpi": 72}, target="cbz")],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["cbz", "comic", "manga", "reader", "images", "pages", "zip"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    scale = opts.dpi / 72
    pdf = open_pdfium(ctx, src, what)
    try:
        count = len(pdf)
        for i in range(count):
            w, h = (math.ceil(x * scale) for x in pdf.get_page_size(i))
            if w * h > ctx.max_pixels:
                raise StepError(
                    "INVALID_OPTIONS",
                    f"Page {i + 1} would be {w}x{h} pixels at {opts.dpi} DPI; the limit is "
                    f"{ctx.max_pixels / 1e6:g} megapixels. Choose a lower resolution.",
                )
        target = ctx.out_dir / "comic.cbz"
        ext = opts.image_format
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as zf:
            for i in range(count):
                ctx.progress(i / count, f"Rendering page {i + 1}")
                image = pdf[i].render(scale=scale, may_draw_forms=True).to_pil()
                buf = io.BytesIO()
                try:
                    image.save(buf, dpi=(opts.dpi, opts.dpi), **_SAVE[ext])
                finally:
                    image.close()
                zf.writestr(f"{i + 1:04d}.{ext}", buf.getbuffer())
        return [target]
    finally:
        pdf.close()
