"""``image.to-pdf``: JPEG, PNG, TIFF, WebP, HEIC, GIF and BMP images to PDF (spec 6, plan task 28).

img2pdf builds the PDF. JPEG (including CMYK) is embedded as is, without re-encoding; other
formats are stored losslessly (Flate), so every edge is lossless. Transparency is kept as a soft
mask. EXIF orientation becomes the page's rotation. Every frame of a multi-page TIFF (or GIF)
becomes a page. HEIC is decoded through pillow-heif.

Mode ``combine`` (default) makes one PDF of all images in upload order; ``each`` makes one PDF
per image. Page size ``image`` makes each page the size of its image (at the image's DPI, 96 when
unknown) plus the margin; a paper size places the image on that page by ``fit``: ``fit`` scales
it to fit inside the margins, ``fill`` covers the page (edges may be cut), ``shrink`` only shrinks
images that are too big. Orientation ``auto`` turns each page to match its image.

Before anything is decoded, each image and frame is checked against ``FILEX_MAX_PIXELS``
(``ctx.max_pixels``) and at most ``MAX_FRAMES`` frames (review H3 fix 4): Pillow's bomb guard is
set to the same limit with its warning raised as an error. Pillow opens each image with the one
decoder of its sniffed format (review H4 fix 2). In ``combine`` mode the frames of all images
together are capped at ``FILEX_MAX_PAGES`` (``TOO_MANY_PAGES``). Every frame is then decoded once,
so a truncated or corrupt image fails here with its name instead of producing a broken page.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.formats import FORMATS
from filex.registry import Edge, Example, Files, StepContext, StepError, ToolOptions, tool

IMAGES = ("jpg", "png", "tiff", "webp", "heic", "gif", "bmp")
MAX_FRAMES = 500  # review H3 fix 4
# Our format id -> the one Pillow decoder allowed for it (HEIF needs pillow-heif registered).
PIL_FORMATS = {
    "jpg": "JPEG",
    "png": "PNG",
    "tiff": "TIFF",
    "webp": "WEBP",
    "heic": "HEIF",
    "gif": "GIF",
    "bmp": "BMP",
}
PAPER_MM = {
    "a3": (297, 420),
    "a4": (210, 297),
    "a5": (148, 210),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
}


class Options(ToolOptions):
    mode: Literal["combine", "each"] = Field(
        "combine",
        title="Output",
        json_schema_extra={"x-enum-titles": ["One PDF for all images", "One PDF per image"]},
    )
    page_size: Literal["image", "a4", "letter", "legal", "a3", "a5"] = Field(
        "image",
        title="Page size",
        json_schema_extra={
            "x-enum-titles": ["Same size as the image", "A4", "Letter", "Legal", "A3", "A5"]
        },
    )
    orientation: Literal["auto", "portrait", "landscape"] = Field(
        "auto",
        title="Orientation",
        json_schema_extra={"x-enum-titles": ["Match each image", "Portrait", "Landscape"]},
    )
    fit: Literal["fit", "fill", "shrink"] = Field(
        "fit",
        title="Fit on the page",
        json_schema_extra={
            "x-enum-titles": ["Fit inside the page", "Fill the page", "Shrink only"],
            "x-enum-descriptions": [
                "The whole image, inside the margins",
                "Covers the page; edges may be cut",
                "Only images too big for the page get smaller",
            ],
        },
    )
    margin_mm: int = Field(0, ge=0, le=50, title="Margin (mm)")


@tool(
    id="image.to-pdf",
    title="Images to PDF",
    summary="Turn photos, scans and pictures into a PDF, one file for all or one per image.",
    category="convert",
    group="core",
    converts=[Edge(f, "pdf") for f in IMAGES],
    files=Files(min=1, max=200),
    options=Options,
    engines=["img2pdf", "pillow"],
    examples=[
        Example(
            inputs=["image/photo_exif.jpg", "image/graphic_rgba.png"],
            options={"pageSize": "a4", "marginMm": 10},
            target="pdf",
        )
    ],
    errors=["TOO_MANY_PAGES"],
    keywords=["jpg", "jpeg", "png", "heic", "photo", "picture", "scan", "tiff", "webp"],
)
def run(ctx: StepContext, src: list[Path], opts: Options) -> list[Path]:
    import img2pdf
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = ctx.max_pixels
    names = [*ctx.input_names, *[""] * len(src)]
    whats = [f'"{n}"' if n else f"Image {i}" for i, n in enumerate(names[: len(src)], 1)]
    layout = _layout(img2pdf, opts)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        pages = 0
        for i, (path, what) in enumerate(zip(src, whats)):
            ctx.progress(i / len(src) / 2, f"Checking image {i + 1} of {len(src)}")
            frames = _check(ctx, path, what)
            pages = pages + frames if opts.mode == "combine" else frames
            if pages > ctx.max_pages:
                whose = "The images together have" if opts.mode == "combine" else f"{what} has"
                raise StepError("TOO_MANY_PAGES", f"{whose} over {ctx.max_pages} pages, the limit.")
        groups = [src] if opts.mode == "combine" else [[p] for p in src]
        out = []
        for i, group in enumerate(groups):
            ctx.progress(0.5 + i / len(groups) / 2, "Building the PDF")
            target = ctx.out_dir / f"{i + 1:04}.pdf"
            what = whats[i] if opts.mode == "each" else "One of the images"
            try:
                with target.open("wb") as f:
                    img2pdf.convert(
                        [str(p) for p in group],
                        layout_fun=layout,
                        rotation=img2pdf.Rotation.ifvalid,
                        outputstream=f,
                    )
            except (img2pdf.ImageOpenError, ValueError, OSError) as e:
                target.unlink(missing_ok=True)
                raise StepError("INVALID_INPUT", f"{what} could not be put into a PDF.") from e
            out.append(target)
        return out


def open_image(path: Path):
    """``Image.open`` limited to the decoder of the format the runner sniffed, which is the
    scratch file's suffix. Any other suffix is a ValueError, as Pillow's own refusals are."""
    from PIL import Image

    suffix = path.suffix.lower()
    for fmt, decoder in PIL_FORMATS.items():
        if suffix in FORMATS[fmt].extensions:
            return Image.open(path, formats=[decoder])
    raise ValueError(f"no image decoder for {suffix!r}")


def _check(ctx: StepContext, path: Path, what: str) -> int:
    """Pixel and frame limits from the header, then one decode of every frame. Returns the
    number of frames."""
    from PIL import Image

    limit = f"the limit is {ctx.max_pixels / 1e6:g} megapixels"
    try:
        with open_image(path) as im:
            frames = getattr(im, "n_frames", 1)
            if frames > MAX_FRAMES:
                raise StepError(
                    "INVALID_INPUT", f"{what} has {frames} frames; the limit is {MAX_FRAMES}."
                )
            for n in range(frames):
                im.seek(n)
                w, h = im.size
                if w * h > ctx.max_pixels:
                    raise StepError("INVALID_INPUT", f"{what} is {w}x{h} pixels; {limit}.")
                im.load()
            return frames
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise StepError("INVALID_INPUT", f"{what} is too large; {limit}.") from e
    except (OSError, ValueError, SyntaxError, EOFError) as e:
        raise StepError("INVALID_INPUT", f"{what} is damaged or not a readable image.") from e


def _layout(img2pdf: object, opts: Options) -> object:
    border = (img2pdf.mm_to_pt(opts.margin_mm),) * 2 if opts.margin_mm else None
    if opts.page_size == "image":
        return img2pdf.get_layout_fun(border=border)
    w, h = (img2pdf.mm_to_pt(x) for x in PAPER_MM[opts.page_size])
    if opts.orientation == "landscape":
        w, h = h, w
    fit = {"fit": "into", "fill": "fill", "shrink": "shrink"}[opts.fit]
    return img2pdf.get_layout_fun(
        (w, h), border=border, fit=img2pdf.FitMode[fit], auto_orient=opts.orientation == "auto"
    )
