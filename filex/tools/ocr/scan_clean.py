"""``scan.clean``: basic scan clean-up (spec 6.5, plan task 40).

Steps, each an option, in this order:

1. **Blank pages** are removed: each page is rendered at about 100 dpi in greyscale and counts
   as blank when, away from its edges (scanner borders, skew wedges), fewer than
   ``BLANK_INK`` of its pixels are dark. That is below a single short word, so specks, dust
   and grain are ignored while a page with any line of text stays. ``BLANK_PAGES_REMOVED``
   names the removed pages.
2. **Deskew** and **auto-rotate** through OCRmyPDF, which asks Tesseract for each page's skew
   angle and orientation (``--deskew``, ``--rotate-pages``). On the corpus scan this leaves a
   2 degree skew under 0.1 degree, so OpenCV is not needed for this tool.
3. **Clean**: unpaper through OCRmyPDF's ``--clean-final`` with its conservative preset
   (no layout guessing, no content moving, no grey filter), plus unpaper's black filter, which
   wipes solid black areas that touch the page edges (scanner lid, book gutter). Black areas
   inside the content, such as photos, stay.
4. **OCR** (off by default): the same text layer as ``ocr.pdf``.

Pages that already have text (born-digital or OCRed earlier) are not rasterised: OCRmyPDF's
skip mode leaves them unchanged, with ``TEXT_PAGES_SKIPPED``. Images become one PDF each first.
Engine settings, limits and process handling: :mod:`filex.tools.ocr._ocrmypdf`.
"""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from filex.registry import Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, write_pages
from filex.tools.ocr import _ocrmypdf as om

TIMEOUT_S = 1800
ENGINE_TIMEOUT_S = TIMEOUT_S - 120  # leaves time for the blank-page pass and moving the output
BLANK_DPI = 100
BLANK_MAX_PIXELS = 4_000_000  # huge pages are rendered smaller for the blank check
BLANK_MARGIN = 0.05  # each edge, ignored by the blank check
BLANK_DARK = 128  # a pixel darker than this is ink
BLANK_INK = 0.0003  # a page with less ink than this (0.03%) is blank


class Options(ToolOptions):
    deskew: bool = Field(True, title="Straighten crooked pages")
    rotate_pages: bool = Field(True, title="Turn pages the right way up")
    clean: bool = Field(True, title="Remove specks and black scanner borders")
    remove_blank: bool = Field(True, title="Remove blank pages")
    ocr: bool = Field(False, title="Make the text searchable (OCR)")
    languages: om.Languages = Field(
        "eng", title="Languages for OCR and page direction (for example eng or eng+rus)"
    )

    @model_validator(mode="after")
    def _something_to_do(self) -> Options:
        if not (self.deskew or self.rotate_pages or self.clean or self.remove_blank or self.ocr):
            raise ValueError("Choose at least one clean-up step.")
        return self


@tool(
    id="scan.clean",
    title="Clean up scans",
    summary="Straighten and turn pages, remove specks, black borders and blank pages, "
    "optionally add OCR.",
    category="ocr",
    group="ocr",
    inputs=["pdf", *om.IMAGES],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["ocrmypdf", "tesseract", "unpaper", "pikepdf", "pypdfium2", "pillow", "img2pdf"],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=1536),
    examples=[Example(inputs=["pdf/scanned_3p.pdf"])],
    warnings=[*om.WARNINGS, "TEXT_PAGES_SKIPPED", "BLANK_PAGES_REMOVED"],
    errors=[*om.ERRORS, "ALL_PAGES_BLANK"],
    keywords=["deskew", "straighten", "rotate", "blank", "border", "clean", "scan", "unpaper"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    engine = opts.deskew or opts.rotate_pages or opts.clean or opts.ocr
    if engine:
        languages = om.check_languages(opts.languages, om.installed_languages())
    out = ctx.out_dir / "cleaned.pdf"
    with tempfile.TemporaryDirectory(prefix="scan-") as tmp:
        work = Path(tmp)
        ctx.progress(0.02, "Preparing")
        pdf = om.prepare(ctx, src, work, what)
        if opts.remove_blank:
            ctx.progress(0.05, "Looking for blank pages")
            pdf = _remove_blank_pages(ctx, pdf, work, what)
        if engine:
            flags = om.has_text(pdf)
            if any(flags):
                ctx.warn(
                    "TEXT_PAGES_SKIPPED",
                    f"{what}: pages that already had text were left as they are "
                    f"({sum(flags)} of {len(flags)}).",
                )
            args = om.ocrmypdf_args(
                pdf,
                work / "clean.pdf",
                languages=languages,
                mode="skip",
                max_pixels=ctx.max_pixels,
                deskew=opts.deskew,
                rotate_pages=opts.rotate_pages,
                clean="final" if opts.clean else None,
                ocr=opts.ocr,
            )
            ctx.progress(0.15, "Cleaning the pages")
            om.run_ocrmypdf(args, work, ENGINE_TIMEOUT_S, what, "cleaned")
            pdf = work / "clean.pdf"
        shutil.move(pdf, out)
    return [out]


def is_blank(image: Any) -> bool:
    """A greyscale page image is blank when, inside ``BLANK_MARGIN`` of each edge, fewer than
    ``BLANK_INK`` of its pixels are darker than ``BLANK_DARK``."""
    g = image.convert("L")
    mx, my = int(g.width * BLANK_MARGIN), int(g.height * BLANK_MARGIN)
    inner = g.crop((mx, my, g.width - mx, g.height - my))
    total = inner.width * inner.height
    return total == 0 or sum(inner.histogram()[:BLANK_DARK]) < BLANK_INK * total


def blank_pages(path: Path) -> list[int]:
    """0-based indices of the blank pages of ``path``."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        found = []
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            scale = min(BLANK_DPI / 72, math.sqrt(BLANK_MAX_PIXELS / max(w * h, 1)))
            bitmap = page.render(scale=scale, grayscale=True)
            if is_blank(bitmap.to_pil()):
                found.append(i)
            bitmap.close()
            page.close()
        return found
    finally:
        pdf.close()


def _remove_blank_pages(ctx: StepContext, pdf: Path, work: Path, what: str) -> Path:
    import pikepdf

    blank = blank_pages(pdf)
    if not blank:
        return pdf
    with pikepdf.open(pdf) as doc:
        total = len(doc.pages)
        if len(blank) == total:
            raise StepError(
                "ALL_PAGES_BLANK",
                f"{what} has only blank pages, so nothing would be left. "
                "Turn off 'Remove blank pages' to keep them.",
            )
        skip = set(blank)
        keep = [i for i in range(total) if i not in skip]
        target = write_pages(doc, keep, work / "pages.pdf", what)
    numbers = ", ".join(str(i + 1) for i in blank[:20]) + (", ..." if len(blank) > 20 else "")
    ctx.warn(
        "BLANK_PAGES_REMOVED",
        f"{what}: removed {om.pages(len(blank))} that looked blank "
        f"({'page' if len(blank) == 1 else 'pages'} {numbers}).",
    )
    return target
