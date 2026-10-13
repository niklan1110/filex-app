"""Overlays drawn with reportlab and stamped onto pages with pikepdf (watermark, page numbers).

Each target page gets a Form XObject drawn in the page's *displayed* coordinates (crop box,
after ``/Rotate``), so a stamp placed "top-right" is at the top right as the reader sees the
page and its text reads upright, whatever the page's rotation. Pages that share a size and a
tag share one XObject. Over content, the page's own content is wrapped in ``q``/``Q`` first, so
a graphics state it leaves behind cannot move or tint the stamp; under content, the stamp is
drawn first (an opaque page background then hides it).

Fonts: reportlab's built-in fonts are Latin-1 only, so text uses the first TrueType font found
in ``FONT_PATHS`` (DejaVu Sans or Noto Sans from the Debian font packages; Arial on a Windows
dev box), falling back to the Bitstream Vera that ships inside reportlab (Latin only). Text with
a character the font lacks fails with UNSUPPORTED_TEXT instead of printing empty boxes.

Engine imports stay inside the functions (registry rule 1).
"""

from __future__ import annotations

import functools
import io
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from filex.registry import StepError

Position = Literal[
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "center",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
]
POSITION_TITLES = [  # x-enum-titles for Position, in its order
    "Top left",
    "Top centre",
    "Top right",
    "Middle left",
    "Centre",
    "Middle right",
    "Bottom left",
    "Bottom centre",
    "Bottom right",
]
# (horizontal, vertical) share of the free space left of / below the item: 0, 0.5 or 1.
ANCHORS: dict[str, tuple[float, float]] = {
    f"{v}-{h}" if (v, h) != ("middle", "center") else "center": (hx, vy)
    for v, vy in (("top", 1.0), ("middle", 0.5), ("bottom", 0.0))
    for h, hx in (("left", 0.0), ("center", 0.5), ("right", 1.0))
}

MM = 72 / 25.4  # points per millimetre
FONT = "FileXSans"
FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian fonts-dejavu-core
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",  # Debian fonts-noto-core
    "C:/Windows/Fonts/arial.ttf",
)

Draw = Callable[[Any, float, float], None]  # draw(canvas, width, height) of the displayed page


@functools.cache
def _font() -> Any:
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    bundled = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    path = next((Path(p) for p in FONT_PATHS if Path(p).is_file()), bundled)
    font = TTFont(FONT, str(path))
    pdfmetrics.registerFont(font)
    return font


def font_for(text: str, what: str) -> str:
    """The registered font name for ``text``. UNSUPPORTED_TEXT names up to ten characters the
    font has no glyph for; ``what`` names the option, e.g. "watermark text"."""
    glyphs = _font().face.charToGlyph
    missing = "".join(sorted({c for c in text if not c.isspace() and ord(c) not in glyphs}))
    if missing:
        raise StepError(
            "UNSUPPORTED_TEXT",
            f"The {what} has characters the installed font cannot show: {missing[:10]}",
        )
    return FONT


def text_box(text: str, font: str, size: float) -> tuple[float, float]:
    """Width and height (ascent to descent) of ``text`` set in ``font`` at ``size``."""
    from reportlab.pdfbase import pdfmetrics

    ascent, descent = pdfmetrics.getAscentDescent(font, size)
    return pdfmetrics.stringWidth(text, font, size), ascent - descent


def draw_text(c: Any, text: str, font: str, size: float, cx: float, cy: float, angle: float):
    """Draw ``text`` centred on (cx, cy), turned ``angle`` degrees counter-clockwise."""
    from reportlab.pdfbase import pdfmetrics

    ascent, descent = pdfmetrics.getAscentDescent(font, size)
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle)
    c.setFont(font, size)
    c.drawString(-pdfmetrics.stringWidth(text, font, size) / 2, -(ascent + descent) / 2, text)
    c.restoreState()


def rotated_box(w: float, h: float, angle: float) -> tuple[float, float]:
    """Width and height of the bounding box of a w x h item turned by ``angle`` degrees."""
    a = math.radians(angle)
    cos, sin = abs(math.cos(a)), abs(math.sin(a))
    return w * cos + h * sin, w * sin + h * cos


def centre(
    w: float, h: float, position: str, width: float, height: float, margin: float
) -> tuple[float, float]:
    """Centre of a w x h box placed at ``position`` inside the page, ``margin`` from its edges."""
    hx, vy = ANCHORS[position]
    return (
        margin + w / 2 + hx * (width - 2 * margin - w),
        margin + h / 2 + vy * (height - 2 * margin - h),
    )


def _matrix(rotation: int, x0: float, y0: float, w: float, h: float) -> tuple[float, ...]:
    """Maps displayed-page coordinates onto the page's user space for its ``/Rotate``."""
    return {
        0: (1, 0, 0, 1, x0, y0),
        90: (0, 1, -1, 0, x0 + w, y0),
        180: (-1, 0, 0, -1, x0 + w, y0 + h),
        270: (0, -1, 1, 0, x0, y0 + h),
    }[rotation % 360]


def stamp(pdf: Any, jobs: Sequence[tuple[int, Any, Draw]], *, under: bool = False) -> Any:
    """Stamp ``pdf.pages[index]`` with what ``draw`` paints, for each ``(index, tag, draw)``.
    Pages with equal ``tag`` and displayed size share one drawing. Returns the overlay Pdf:
    keep it open until ``pdf`` is saved, since qpdf copies the stream data at write time."""
    import pikepdf
    from reportlab.pdfgen.canvas import Canvas

    buf = io.BytesIO()
    canvas = Canvas(buf, pageCompression=1)
    slots: dict[tuple[Any, float, float], int] = {}
    placed = []
    for index, tag, draw in jobs:
        page = pdf.pages[index]
        x1, y1, x2, y2 = (float(v) for v in page.cropbox)
        x0, y0, w, h = min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
        rotation = page.rotation % 360
        width, height = (h, w) if rotation in (90, 270) else (w, h)
        key = (tag, round(width, 2), round(height, 2))
        if key not in slots:
            slots[key] = len(slots)
            canvas.setPageSize((width, height))
            draw(canvas, width, height)
            canvas.showPage()
        placed.append((page, slots[key], _matrix(rotation, x0, y0, w, h)))
    canvas.save()

    overlay = pikepdf.open(io.BytesIO(buf.getvalue()))
    forms = [pdf.copy_foreign(p.as_form_xobject()) for p in overlay.pages]
    for page, k, m in placed:
        name = page.add_resource(forms[k], pikepdf.Name.XObject, prefix="FxStamp")
        matrix = " ".join(f"{v:.4f}" for v in m)
        ops = f"q {matrix} cm {name} Do Q\n".encode()
        if under:
            page.contents_add(ops, prepend=True)
        else:
            page.contents_add(b"q\n", prepend=True)
            page.contents_add(b"Q\n" + ops)
        page.contents_coalesce()
    return overlay
