"""``pdf.watermark``: stamp text or an image on all or selected pages (spec 6.6, plan task 29).

The watermark is drawn once per page size with reportlab and placed on each page as a Form
XObject with pikepdf (see ``_overlay``): at one of nine positions 10 mm from the edges, or tiled
over the whole page; turned by ``rotation`` degrees counter-clockwise (45 is the usual
diagonal); with an opacity. "Under the content" draws it first, so it shows only where the page
has no opaque background (scans usually hide it). Text is real text, so it can be searched,
selected and removed again by an editor: a watermark marks a document, it does not protect it.
Everything else in the file (metadata, bookmarks, forms) is kept. Full pikepdf rewrite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from filex.registry import Aux, Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import Pages, label, open_pdf, parse_ranges, save, select
from filex.tools.edit._overlay import (
    MM,
    POSITION_TITLES,
    Position,
    centre,
    draw_text,
    font_for,
    rotated_box,
    stamp,
    text_box,
)

MARGIN = 10 * MM
MAX_TILES = 10_000  # per page: a hostile CropBox (1e7 pt) would otherwise ask for billions


class Options(ToolOptions):
    kind: Literal["text", "image"] = Field(
        "text", title="Watermark", json_schema_extra={"x-enum-titles": ["Text", "Image"]}
    )
    text: str = Field("", title="Text", max_length=200)
    font_size: int = Field(48, title="Font size (pt)", ge=6, le=300)
    color: str = Field(
        "#808080",
        title="Colour",
        pattern=r"^#[0-9A-Fa-f]{6}$",
        max_length=7,
        json_schema_extra={"x-pattern-message": "Use a colour like #FF0000."},
    )
    image_width: int = Field(40, title="Image width (% of the page width)", ge=5, le=100)
    opacity: int = Field(30, title="Opacity (%)", ge=5, le=100)
    rotation: int = Field(45, title="Rotation (degrees, counter-clockwise)", ge=-180, le=180)
    position: Literal[Position, "tiled"] = Field(
        "center",
        title="Position",
        json_schema_extra={"x-enum-titles": [*POSITION_TITLES, "Tiled across the page"]},
    )
    layer: Literal["over", "under"] = Field(
        "over",
        title="Layer",
        json_schema_extra={"x-enum-titles": ["Over the content", "Under the content"]},
    )
    pages: Pages = Field("", title="Pages (blank for all)")

    @model_validator(mode="after")
    def _needs_text(self) -> Self:
        if self.kind == "text" and not self.text.strip():
            raise ValueError("Enter the watermark text.")
        return self


@tool(
    id="pdf.watermark",
    title="Add a watermark",
    summary="Stamp text or an image on every page, or the pages you pick.",
    category="edit",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(
        min=1,
        max=50,
        each=True,
        aux={"stamp": Aux(formats=["png", "jpg"], title="Watermark image", required=False)},
    ),
    options=Options,
    engines=["pikepdf", "reportlab", "pillow"],
    examples=[
        Example(
            inputs=["pdf/text_10p.pdf"],
            options={"text": "CONFIDENTIAL", "pages": "1-3", "color": "#CC0000"},
        )
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES", "UNSUPPORTED_TEXT"],
    keywords=["stamp", "confidential", "draft", "logo", "mark", "brand", "overlay"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    from reportlab.lib.colors import HexColor

    what = label(ctx)
    if opts.kind == "image":
        image, aspect = _image(ctx)
    else:
        font = font_for(opts.text, "watermark text")
        text_w, text_h = text_box(opts.text, font, opts.font_size)

    def draw(c, width: float, height: float) -> None:
        c.setFillColor(HexColor(opts.color))
        c.setFillAlpha(opts.opacity / 100)
        if opts.kind == "image":
            w = width * opts.image_width / 100
            h = w * aspect
        else:
            w, h = text_w, text_h
        for cx, cy in _centres(w, h, opts, width, height):
            if opts.kind == "image":
                c.saveState()
                c.translate(cx, cy)
                c.rotate(opts.rotation)
                c.drawImage(image, -w / 2, -h / 2, w, h, mask="auto")
                c.restoreState()
            else:
                draw_text(c, opts.text, font, opts.font_size, cx, cy, opts.rotation)

    with open_pdf(ctx, src, what) as pdf:
        count = len(pdf.pages)
        chosen = sorted({i for r in select(parse_ranges(opts.pages), count, what) for i in r})
        ctx.progress(0.2, "Drawing the watermark")
        jobs = [(i, None, draw) for i in chosen or range(count)]
        with stamp(pdf, jobs, under=opts.layer == "under"):
            ctx.progress(0.6, "Saving")
            return [save(pdf, ctx.out_dir / "watermarked.pdf", what)]


def _centres(w: float, h: float, opts: Options, width: float, height: float):
    """Where to centre each copy: one at the position, or a staggered grid when tiled."""
    bw, bh = rotated_box(w, h, opts.rotation)
    if opts.position != "tiled":
        return [centre(bw, bh, opts.position, width, height, MARGIN)]
    step_x, step_y = bw + 2 * MARGIN, bh + 2 * MARGIN
    found = []
    row, y = 0, bh / 2
    while y - bh / 2 < height:
        x = bw / 2 + (step_x / 2 if row % 2 else 0)
        while x - bw / 2 < width:
            if len(found) == MAX_TILES:
                raise StepError(
                    "INVALID_OPTIONS",
                    f"Tiling would need more than {MAX_TILES} copies on one page. "
                    "Make the watermark larger or choose one position.",
                )
            found.append((x, y))
            x += step_x
        row, y = row + 1, y + step_y
    return found


def _image(ctx: StepContext):
    """The ``stamp`` image as a reportlab ImageReader and its height/width ratio."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    from filex.tools.convert.image_to_pdf import open_image

    path = ctx.aux.get("stamp")
    if path is None:
        raise StepError("INVALID_OPTIONS", "Add a watermark image, or choose a text watermark.")
    Image.MAX_IMAGE_PIXELS = ctx.max_pixels
    try:
        with open_image(path) as im:
            w, h = im.size
            if w * h > ctx.max_pixels:
                raise StepError(
                    "INVALID_INPUT",
                    f"The watermark image is {w}x{h} pixels; the limit is "
                    f"{ctx.max_pixels / 1e6:g} megapixels.",
                )
            alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            return ImageReader(im.convert("RGBA" if alpha else "RGB")), h / w
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise StepError("INVALID_INPUT", "The watermark image is too large.") from e
    except (OSError, ValueError, SyntaxError, EOFError) as e:
        raise StepError("INVALID_INPUT", "The watermark image is damaged or unreadable.") from e
