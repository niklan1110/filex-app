"""``comic.to-pdf``: CBZ and CBR comic books to PDF, one page per image (spec 6.4, plan task 52).

The archive goes through the shared pre-check and unpack loop (:mod:`filex.tools._archive`,
review H3): entry count, names, links, nesting, unpacked size and ratio are checked before
anything is written, and only image entries are unpacked, under generated names. CBZ is read
with the stdlib, CBR (RAR4 and RAR5) with libarchive. Hidden files and ``__MACOSX`` entries are
skipped; ``ComicInfo.xml`` and other non-images are never unpacked.

Pages are sorted in natural order of their names in the archive (``page2`` before ``page10``,
folders included), each image is checked against ``FILEX_MAX_PIXELS`` and decoded once like in
``image.to-pdf``, then img2pdf builds the PDF losslessly: JPEG is embedded as is, other formats
are stored with lossless compression, each page the size of its image.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import warnings
from pathlib import Path

from filex.registry import Edge, Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools import _archive
from filex.tools._pdf import label
from filex.tools.ebook import _engine

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")
_DIGITS = re.compile(r"(\d+)")


@tool(
    id="comic.to-pdf",
    title="Comic book to PDF",
    summary="Turn CBZ and CBR comic books into a PDF, pages in order and images untouched.",
    category="ebook",
    group="ebook",
    converts=[Edge("cbz", "pdf"), Edge("cbr", "pdf")],
    files=Files(min=1, max=20, each=True),
    engines=["img2pdf", "pillow", "libarchive"],
    limits=Limits(timeout_s=600, memory_mb=1536),
    examples=[Example(inputs=["ebook/comic.cbz"], target="pdf")],
    errors=["TOO_MANY_PAGES"],
    keywords=["cbz", "cbr", "comic", "manga", "rar", "zip", "pages", "images"],
)
def run(ctx: StepContext, src: Path, opts: ToolOptions) -> list[Path]:
    import img2pdf
    from PIL import Image

    what = label(ctx)
    _engine.format_of(src, ("cbz", "cbr"))
    work = Path(tempfile.mkdtemp(prefix="comic-"))  # under TMPDIR, the step's scratch dir
    try:
        ctx.progress(0.0, "Unpacking the pages")
        pages = _archive.unpack(src, work / "pages", what, select=is_page, max_pages=ctx.max_pages)
        if not pages:
            raise StepError("INVALID_INPUT", f"{what} contains no page images.")
        pages.sort(key=lambda u: natural_key(u.entry.name))
        from filex.tools.convert.image_to_pdf import _check

        Image.MAX_IMAGE_PIXELS = ctx.max_pixels
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            for i, page in enumerate(pages):
                ctx.progress(0.1 + 0.6 * i / len(pages), f"Checking page {i + 1}")
                _check(ctx, page.path, f'{what}, page "{page.entry.name[-100:]}",')
        ctx.progress(0.7, "Building the PDF")
        target = ctx.out_dir / "comic.pdf"
        try:
            with target.open("wb") as f:
                img2pdf.convert(
                    [str(p.path) for p in pages],
                    layout_fun=img2pdf.get_layout_fun(),
                    rotation=img2pdf.Rotation.ifvalid,
                    outputstream=f,
                )
        except (img2pdf.ImageOpenError, ValueError, OSError) as e:
            target.unlink(missing_ok=True)
            raise StepError("INVALID_INPUT", f"{what} has a page that cannot go into a PDF.") from e
        return [target]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def is_page(entry: _archive.Entry) -> bool:
    parts = entry.name.split("/")
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return False
    return parts[-1].lower().endswith(IMAGE_EXTENSIONS)


def natural_key(name: str) -> list[int | str]:
    """``page2`` before ``page10``: digit runs compare as numbers, the rest case-folded."""
    return [int(t) if i % 2 else t.casefold() for i, t in enumerate(_DIGITS.split(name))]
