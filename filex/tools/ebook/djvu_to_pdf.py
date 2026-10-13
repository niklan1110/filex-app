"""``djvu.to-pdf``: DjVu documents to PDF, keeping the hidden text layer (spec 6.4, plan task 52).

djvulibre does the reading (ADR-0007 Appendix A): ``djvused -e n`` counts the pages first and a
document over ``FILEX_MAX_PAGES`` is refused; ``djvused`` then reads each page's size and hidden
text, and ``ddjvu -format=tiff`` renders the pages in batches, each batch with its own timeout.
A page over ``FILEX_MAX_PIXELS`` is rendered alone, scaled down to fit (``-size``).

img2pdf builds the PDF: bitonal pages stay CCITT G4 as ddjvu wrote them (lossless), grey and
colour pages are stored as JPEG (quality 90), since a DjVu background is already lossy and Flate
would make them several times larger. Pages keep their physical size (the DjVu resolution).
The hidden text becomes an invisible text layer (render mode 3, DejaVu Sans Mono embedded), each
word stretched over its box, so the PDF can be searched and copied like the DjVu.

OCR (option ``ocr``, ``auto`` by default) is planned when the job is created (ADR-0005
amendment, :func:`plan`): with the ``ocr`` worker online the job gets a second step,
``ocr.pdf`` in skip mode, which leaves pages that already have text alone; with it offline the
job has this one step and an ``OCR_SKIPPED`` warning. This step never adds steps itself.
"""

from __future__ import annotations

import ctypes
import math
import re
import shutil
import tempfile
import warnings
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from filex.registry import (
    Edge,
    Example,
    Files,
    FollowUp,
    Limits,
    Plan,
    StepContext,
    StepError,
    ToolOptions,
    tool,
)
from filex.tools._pdf import label
from filex.tools.ebook import _engine

TIMEOUT_S = 1800
BATCH = 20  # pages per ddjvu run
BATCH_TIMEOUT_S = 300
JPEG_QUALITY = 90
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
OCR_TOOL = "ocr.pdf"

_SIZE = re.compile(r"^width=(\d+) height=(\d+)", re.MULTILINE)
_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.DOTALL)
_ESCAPE = re.compile(r"\\([0-7]{1,3}|.)", re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "b": "\b", "v": "\v", "a": "\a"}


class Options(ToolOptions):
    ocr: Literal["auto", "never"] = Field(
        "auto",
        title="Recognise text (OCR)",
        json_schema_extra={
            "x-enum-descriptions": [
                "On pages without a text layer, when the OCR worker is running",
                "Never",
            ]
        },
    )


def plan(opts: Options, online: AbstractSet[str]) -> Plan:
    """The job's steps after this one, fixed at job creation (ADR-0005 amendment)."""
    if opts.ocr == "never":
        return Plan()
    if OCR_TOOL in online:
        return Plan(steps=(FollowUp(OCR_TOOL, {"mode": "skip"}),))
    message = (
        "Pages without a text layer were not made searchable: the OCR worker is not running. "
        "Add 'ocr' to COMPOSE_PROFILES to turn it on."
    )
    return Plan(warnings=(("OCR_SKIPPED", message),))


@tool(
    id="djvu.to-pdf",
    title="DjVu to PDF",
    summary="Convert DjVu scans to PDF, keeping their text layer, and make them searchable.",
    category="ebook",
    group="ebook",
    converts=[Edge("djvu", "pdf")],
    files=Files(min=1, max=10, each=True),
    options=Options,
    engines=["djvulibre", "img2pdf", "pillow", "pypdfium2"],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=1536),
    examples=[Example(inputs=["ebook/un_resolution_1837.djvu"], target="pdf")],
    warnings=["OCR_SKIPPED"],
    keywords=["djvu", "djv", "scan", "book", "searchable", "ocr", "text"],
    plan=plan,
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    _engine.format_of(src, ("djvu",))
    work = Path(tempfile.mkdtemp(prefix="djvu-"))  # under TMPDIR, the step's scratch dir
    try:
        book = src.resolve()
        count = page_count(book, work, what)
        if count > ctx.max_pages:
            raise StepError(
                "INVALID_INPUT", f"{what} has {count} pages; the limit is {ctx.max_pages}."
            )
        pages: list[Page] = []
        for first in range(1, count + 1, BATCH):
            numbers = range(first, min(first + BATCH, count + 1))
            ctx.progress(0.8 * first / count, f"Reading pages {numbers[0]}-{numbers[-1]}")
            batch = read_pages(book, numbers, work, what)
            render(book, batch, work, ctx.max_pixels, what)
            pages += batch
        ctx.progress(0.85, "Building the PDF")
        images = [page_image(p, ctx.max_pixels, what) for p in pages]
        built = work / "images.pdf"
        build_pdf(images, built, what)
        target = ctx.out_dir / "converted.pdf"
        ctx.progress(0.92, "Adding the text layer")
        add_text_layer(built, pages, target)
        return [target]
    finally:
        shutil.rmtree(work, ignore_errors=True)


@dataclass
class Page:
    number: int
    width: int
    height: int
    words: list[tuple[float, float, float, float, str]]
    image: Path | None = None


def _djvused(book: Path, work: Path, script: str, what: str) -> str:
    (work / "script").write_text(script, encoding="utf-8")
    code, out = _engine.run(
        ["djvused", "-u", "-f", str(work / "script"), str(book)],
        cwd=work,
        timeout_s=BATCH_TIMEOUT_S,
        what=what,
        engine="djvused",
        stdout_only=True,
    )
    if code:
        raise StepError("INVALID_INPUT", f"{what} is damaged or not a readable DjVu file.")
    return out


def page_count(book: Path, work: Path, what: str) -> int:
    out = _djvused(book, work, "n\n", what).strip()
    if not out.isdigit() or int(out) == 0:
        raise StepError("INVALID_INPUT", f"{what} is damaged or has no pages.")
    return int(out)


def read_pages(book: Path, numbers: range, work: Path, what: str) -> list[Page]:
    """Size and hidden text of each page: one djvused run per batch."""
    script = "".join(f"select {n}; size; print-txt\n" for n in numbers)
    out = _djvused(book, work, script, what)
    sizes = list(_SIZE.finditer(out))
    if len(sizes) != len(numbers):
        raise StepError("INVALID_INPUT", f"{what} is damaged or not a readable DjVu file.")
    pages = []
    for i, (n, m) in enumerate(zip(numbers, sizes)):
        end = sizes[i + 1].start() if i + 1 < len(sizes) else len(out)
        try:
            found = words(parse_sexpr(out[m.end() : end]))
        except ValueError:  # a damaged text layer: the page keeps its image, without text
            found = []
        pages.append(Page(n, int(m[1]), int(m[2]), found))
    return pages


def parse_sexpr(text: str) -> list:
    """djvused's hidden-text S-expressions as nested lists of strings."""
    stack: list[list] = [[]]
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip():
                raise ValueError("bad S-expression")
            break
        pos = m.end()
        if m[1]:
            stack.append([])
        elif m[2]:
            if len(stack) == 1:
                raise ValueError("unbalanced S-expression")
            done = stack.pop()
            stack[-1].append(done)
        elif m[3] is not None:
            stack[-1].append(_Str(_unescape(m[3])))
        else:
            stack[-1].append(m[4])
    if len(stack) != 1:
        raise ValueError("unbalanced S-expression")
    return stack[0]


class _Str(str):
    """A quoted string in an S-expression, told apart from bare atoms."""


def _unescape(s: str) -> str:
    """C-style escapes; an octal escape is one byte of UTF-8 (djvused writes non-ASCII text
    as octal bytes without ``-u``, and control bytes so even with it)."""
    out, pos = bytearray(), 0
    for m in _ESCAPE.finditer(s):
        out += s[pos : m.start()].encode("utf-8")
        c = m[1]
        if c[0] in "01234567":
            out.append(int(c, 8) & 0xFF)
        else:
            out += _ESCAPES.get(c, c).encode("utf-8")
        pos = m.end()
    out += s[pos:].encode("utf-8")
    return out.decode("utf-8", "replace")


def words(tree: list) -> list[tuple[float, float, float, float, str]]:
    """The leaf zones ``(type x0 y0 x1 y1 "text")`` of a hidden-text tree, whatever their type
    (word, line or char)."""
    found = []
    stack = list(tree)
    while stack:
        z = stack.pop()
        if not isinstance(z, list) or len(z) < 6:
            continue
        if isinstance(z[-1], _Str):
            try:
                x0, y0, x1, y1 = (float(v) for v in z[1:5])
            except ValueError:
                continue
            if z[-1].strip() and x1 > x0 and y1 > y0:
                found.append((x0, y0, x1, y1, z[-1].strip()))
        else:
            stack += reversed(z[5:])
    return found


def render(book: Path, pages: list[Page], work: Path, max_pixels: int, what: str) -> None:
    """ddjvu: the batch's pages to TIFF in one run; pages over the pixel limit alone, scaled."""
    fits = [p for p in pages if p.width * p.height <= max_pixels]
    if fits:
        span = f"{fits[0].number}-{fits[-1].number}"
        _ddjvu(book, ["-eachpage", f"-page={span}"], work / "p%d.tif", work, what)
    for p in pages:
        if p.width * p.height > max_pixels:
            f = math.sqrt(max_pixels / (p.width * p.height))
            size = f"-size={max(1, int(p.width * f))}x{max(1, int(p.height * f))}"
            _ddjvu(book, [f"-page={p.number}", size], work / f"p{p.number}.tif", work, what)
        p.image = work / f"p{p.number}.tif"
        if not p.image.is_file():
            raise StepError("INVALID_INPUT", f"{what}: page {p.number} could not be read.")


def _ddjvu(book: Path, args: list[str], out: Path, work: Path, what: str) -> None:
    code, _ = _engine.run(
        ["ddjvu", "-format=tiff", *args, str(book), str(out)],
        cwd=work,
        timeout_s=BATCH_TIMEOUT_S,
        what=what,
        engine="ddjvu",
    )
    if code:
        raise StepError("INVALID_INPUT", f"{what} has a page that cannot be read.")


def page_image(page: Page, max_pixels: int, what: str) -> Path:
    """The file img2pdf gets for a page: the G4 TIFF as is, else a JPEG of it."""
    from PIL import Image

    assert page.image is not None
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(page.image) as im:
                if im.mode == "1":
                    return page.image
                jpg = page.image.with_suffix(".jpg")
                im.convert("L" if im.mode in ("L", "LA", "I;16") else "RGB").save(
                    jpg, quality=JPEG_QUALITY, dpi=im.info.get("dpi", (300, 300))
                )
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise StepError("INVALID_INPUT", f"{what}: page {page.number} could not be read.") from e
    page.image.unlink()
    return jpg


def build_pdf(images: list[Path], target: Path, what: str) -> None:
    import img2pdf

    try:
        with target.open("wb") as f:
            img2pdf.convert([str(p) for p in images], outputstream=f)
    except (img2pdf.ImageOpenError, ValueError, OSError) as e:
        raise StepError("INVALID_INPUT", f"{what} has a page that cannot go into a PDF.") from e


def add_text_layer(src: Path, pages: list[Page], target: Path) -> None:
    """Invisible text (render mode 3) for every page with hidden text, each word scaled onto
    its box; saved as ``target``. Without words or without the font, ``src`` is moved."""
    if not any(p.words for p in pages) or not FONT.is_file():
        shutil.move(src, target)
        return
    import pypdfium2
    import pypdfium2.raw as c

    data = FONT.read_bytes()
    buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    pdf = pypdfium2.PdfDocument(src)
    font = c.FPDFText_LoadFont(pdf.raw, buf, len(data), c.FPDF_FONT_TRUETYPE, True)
    if not font:
        pdf.close()
        shutil.move(src, target)
        return
    try:
        for i, p in enumerate(pages):
            if not p.words:
                continue
            page = pdf[i]
            w, h = page.get_size()
            sx, sy = w / p.width, h / p.height
            for x0, y0, x1, y1, text in p.words:
                _place(c, pdf, page, font, text, x0 * sx, y0 * sy, x1 * sx, y1 * sy)
            if not c.FPDFPage_GenerateContent(page.raw):
                raise StepError("ENGINE_FAILED", "The text layer could not be added.")
            page.close()
        pdf.save(target)
    finally:
        c.FPDFFont_Close(font)
        pdf.close()


def _place(c, pdf, page, font, text: str, x0: float, y0: float, x1: float, y1: float) -> None:
    obj = c.FPDFPageObj_CreateTextObj(pdf.raw, font, ctypes.c_float(1.0))
    ws = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
    c.FPDFText_SetText(obj, ctypes.cast(ws, c.FPDF_WIDESTRING))
    c.FPDFTextObj_SetTextRenderMode(obj, c.FPDF_TEXTRENDERMODE_INVISIBLE)
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    c.FPDFPageObj_GetBounds(obj, left, bottom, right, top)
    bw, bh = right.value - left.value, top.value - bottom.value
    if bw <= 0 or bh <= 0:
        c.FPDFPageObj_Destroy(obj)
        return
    a, d = (x1 - x0) / bw, (y1 - y0) / bh
    c.FPDFPageObj_Transform(obj, a, 0, 0, d, x0 - left.value * a, y0 - bottom.value * d)
    c.FPDFPage_InsertObject(page.raw, obj)
