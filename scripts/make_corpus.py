"""Build the FileX QA corpus.

    python scripts/make_corpus.py            # regenerate the small committed files in tests/corpus/
    python scripts/make_corpus.py --large    # build big files into tests/corpus/generated/ (git-ignored)
    python scripts/make_corpus.py --large --only 'scan_*'   # just some of the big ones

Everything is generated from code with free libraries (see scripts/requirements-corpus.txt),
except the one DJVU sample, which is a public-domain file pinned by URL and SHA-256.
MOBI/AZW3 are converted from the generated EPUB with Calibre's `ebook-convert` when it is on PATH
(or in its default Windows install folder); without Calibre those two files are left as they are.

The README in tests/corpus/ is written by this script from the ITEMS table below, so every file's
provenance line stays in sync with the code that made it.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import io
import math
import random
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"
GENERATED = CORPUS / "generated"

# Fixed values so outputs are stable between runs.
FIXED_DATE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
SEED = 20260101

USER_PASSWORD = "filex-user"
OWNER_PASSWORD = "filex-owner"

LOREM = (
    "FileX test corpus. The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs. Sphinx of black quartz, judge my vow. "
    "How vexingly quick daft zebras jump. 0123456789 !?&%$#@ "
)


# --------------------------------------------------------------------------- helpers


def _font_path() -> Path:
    import reportlab

    return Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def _register_font() -> str:
    """Bitstream Vera ships with reportlab and may be embedded and redistributed."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "Vera" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Vera", str(_font_path())))
    return "Vera"


def _canvas(path: Path, pagesize=None, **kw):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=pagesize or A4, invariant=1, **kw)
    c.setCreator("FileX make_corpus.py")
    c.setProducer("reportlab")
    return c


def _text_page(c, page_no: int, total: int, title: str) -> None:
    w, h = c._pagesize
    font = _register_font()
    c.setFont(font, 18)
    c.drawString(72, h - 72, f"{title} - page {page_no} of {total}")
    c.setFont(font, 10)
    y = h - 110
    rnd = random.Random(SEED + page_no)
    while y > 90:
        words = LOREM.split()
        rnd.shuffle(words)
        c.drawString(72, y, " ".join(words)[:95])
        y -= 14
    c.drawCentredString(w / 2, 40, f"- {page_no} -")


def text_pdf(path: Path, pages: int, title: str) -> None:
    c = _canvas(path)
    c.setTitle(title)
    for i in range(1, pages + 1):
        _text_page(c, i, pages, title)
        c.showPage()
    c.save()


def _scan_image(text_lines: list[str], dpi: int, skew_deg: float, seed: int, blank=False):
    """A4 greyscale 'scan': text, paper noise, slight skew, dark border like a flatbed."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    w, h = int(8.27 * dpi), int(11.69 * dpi)
    img = Image.new("L", (w, h), 245)
    if not blank:
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(str(_font_path()), max(10, dpi // 7))
        y = dpi
        for line in text_lines:
            draw.text((dpi, y), line, fill=25, font=font)
            y += int(dpi / 4.5)
            if y > h - dpi:
                break
    noise = Image.effect_noise((w, h), 18).point(lambda v: v // 6)  # faint grain
    img = Image.blend(img, noise, 0.08)
    if blank:  # a few specks so it is "nearly blank", like a real empty page
        rnd = random.Random(seed)
        d = ImageDraw.Draw(img)
        for _ in range(30):
            x, y = rnd.randrange(w), rnd.randrange(h)
            d.ellipse((x, y, x + 3, y + 3), fill=90)
    img = img.rotate(skew_deg, resample=Image.BICUBIC, expand=False, fillcolor=60)
    return img.filter(ImageFilter.GaussianBlur(0.4))


def _jpeg_bytes(img, quality=75) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, dpi=(200, 200))
    return buf.getvalue()


def images_to_pdf(path: Path, jpegs, dpi: int) -> None:
    """Image-only PDF: each JPEG embedded as-is (DCTDecode), one per A4 page. No text layer.

    `jpegs` may be a generator so large scans never sit in memory all at once.
    """
    import pikepdf
    from PIL import Image

    pdf = pikepdf.new()
    for data in jpegs:
        im = Image.open(io.BytesIO(data))
        pw, ph = im.width * 72 / dpi, im.height * 72 / dpi
        xobj = pikepdf.Stream(pdf, data)
        xobj.Type = pikepdf.Name.XObject
        xobj.Subtype = pikepdf.Name.Image
        xobj.Width, xobj.Height = im.width, im.height
        xobj.ColorSpace = pikepdf.Name.DeviceGray if im.mode == "L" else pikepdf.Name.DeviceRGB
        xobj.BitsPerComponent = 8
        xobj.Filter = pikepdf.Name.DCTDecode
        content = pikepdf.Stream(pdf, f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q".encode())
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, pw, ph],
                Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj)),
                Contents=content,
            )
        )
        pdf.pages.append(page)
    pdf.save(path, deterministic_id=True)


def _sample_lines(n: int, seed: int) -> list[str]:
    rnd = random.Random(seed)
    words = LOREM.split()
    out = []
    for _ in range(n):
        rnd.shuffle(words)
        out.append(" ".join(words)[:70])
    return out


def _photo(w=640, h=480):
    """Synthetic 'photo': gradient sky, sun, hills. Pure code, no external image."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (40 + y * 120 // h, 110 + y * 80 // h, 220 - y * 100 // h)
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.7, h * 0.1, w * 0.85, h * 0.3), fill=(255, 220, 90))
    d.polygon(
        [(0, h), (0, h * 0.65), (w * 0.3, h * 0.5), (w * 0.6, h * 0.7), (w, h * 0.55), (w, h)],
        fill=(50, 130, 60),
    )
    d.text((10, 10), "FileX corpus", fill=(255, 255, 255))
    return img


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes | str, store=False) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE.timetuple()[:6])
    info.compress_type = zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


# --------------------------------------------------------------------------- PDF samples


def make_text_1p(p: Path) -> None:
    text_pdf(p, 1, "Text PDF")


def make_text_10p(p: Path) -> None:
    text_pdf(p, 10, "Ten page text PDF")


def make_mixed_pages(p: Path) -> None:
    """A4 portrait, Letter, A4 landscape, and a page with /Rotate 90: tests rotate/organize/n-up."""
    import pikepdf
    from reportlab.lib.pagesizes import A4, landscape, letter

    c = _canvas(p)
    for i, size in enumerate([A4, letter, landscape(A4), A4], start=1):
        c.setPageSize(size)
        _text_page(c, i, 4, "Mixed page sizes")
        c.showPage()
    c.save()
    with pikepdf.open(p, allow_overwriting_input=True) as pdf:
        pdf.pages[3].Rotate = 90
        pdf.save(p, deterministic_id=True)


def make_scanned(p: Path) -> None:
    """3 image-only pages at 150 dpi: skewed 2 degrees, nearly blank, straight."""
    pages = [
        _scan_image(_sample_lines(40, 1), 150, 2.0, 1),
        _scan_image([], 150, 0.0, 2, blank=True),
        _scan_image(_sample_lines(40, 3), 150, 0.0, 3),
    ]
    images_to_pdf(p, (_jpeg_bytes(im, 60) for im in pages), 150)


def make_forms(p: Path) -> None:
    from reportlab.lib.colors import black, white

    c = _canvas(p)
    font = _register_font()
    c.setFont(font, 16)
    c.drawString(72, 770, "AcroForm sample")
    c.setFont(font, 11)
    form = c.acroForm
    c.drawString(72, 720, "Name:")
    form.textfield(
        name="name",
        tooltip="Full name",
        x=160,
        y=710,
        width=250,
        height=20,
        borderColor=black,
        fillColor=white,
        value="",
    )
    c.drawString(72, 680, "Email:")
    form.textfield(name="email", x=160, y=670, width=250, height=20, value="test@example.com")
    c.drawString(72, 640, "Subscribe:")
    form.checkbox(name="subscribe", x=160, y=635, size=18, checked=True)
    c.drawString(72, 600, "Country:")
    form.choice(
        name="country",
        x=160,
        y=590,
        width=150,
        height=20,
        value="Other",
        options=["Germany", "France", "Other"],
    )
    c.drawString(72, 560, "Plan:")
    form.radio(name="plan", value="free", selected=True, x=160, y=555, size=16)
    form.radio(name="plan", value="pro", selected=False, x=200, y=555, size=16)
    c.showPage()
    c.save()


def make_metadata(p: Path) -> None:
    """Info dict + XMP with personal-looking fields, for metadata-edit and metadata-strip."""
    import pikepdf

    text_pdf(p, 2, "Metadata sample")
    with pikepdf.open(p, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Title"] = "Quarterly report (metadata sample)"
        pdf.docinfo["/Author"] = "Jane Example"
        pdf.docinfo["/Subject"] = "FileX metadata test"
        pdf.docinfo["/Keywords"] = "filex, corpus, metadata"
        pdf.docinfo["/CreationDate"] = "D:20260101120000Z"
        pdf.docinfo["/ModDate"] = "D:20260102120000Z"
        pdf.docinfo["/Company"] = "Example Corp"  # custom key, should be stripped too
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = "Quarterly report (metadata sample)"
            meta["dc:creator"] = ["Jane Example"]
            meta["xmp:CreatorTool"] = "FileX make_corpus.py"
        pdf.save(p, deterministic_id=True)


def _encrypt(src: Path, dst: Path, **enc) -> None:
    import pikepdf

    with pikepdf.open(src) as pdf:
        # qpdf cannot make a deterministic /ID for encrypted output, so these change every run.
        pdf.save(dst, encryption=pikepdf.Encryption(**enc))


def make_encrypted_aes256(p: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pdf"
        text_pdf(src, 3, "Encrypted AES-256")
        _encrypt(src, p, user=USER_PASSWORD, owner=OWNER_PASSWORD, R=6)


def make_encrypted_rc4(p: Path) -> None:
    import pikepdf

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pdf"
        text_pdf(src, 1, "Encrypted RC4 40-bit (legacy)")
        _encrypt(
            src,
            p,
            user=USER_PASSWORD,
            owner=OWNER_PASSWORD,
            R=2,
            aes=False,
            metadata=False,
            allow=pikepdf.Permissions(),
        )


def make_encrypted_owner_only(p: Path) -> None:
    import pikepdf

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pdf"
        text_pdf(src, 1, "Owner password only, printing and copying not allowed")
        _encrypt(
            src,
            p,
            user="",
            owner=OWNER_PASSWORD,
            R=4,
            allow=pikepdf.Permissions(
                extract=False, print_lowres=False, print_highres=False, modify_other=False
            ),
        )


def make_pdfa(p: Path) -> None:
    """PDF/A-2b attempt: embedded font, sRGB output intent, XMP pdfaid. Not yet veraPDF-checked."""
    import pikepdf
    from PIL import ImageCms

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pdf"
        text_pdf(src, 2, "PDF/A-2b sample")
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        with pikepdf.open(src) as pdf:
            pdf.docinfo["/Title"] = "PDF/A-2b sample"
            with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
                meta["dc:title"] = "PDF/A-2b sample"
                meta["pdfaid:part"] = "2"
                meta["pdfaid:conformance"] = "B"
            icc_stream = pikepdf.Stream(pdf, icc)
            icc_stream.N = 3
            pdf.Root.OutputIntents = pikepdf.Array(
                [
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.OutputIntent,
                        S=pikepdf.Name.GTS_PDFA1,
                        OutputConditionIdentifier="sRGB",
                        Info="sRGB IEC61966-2.1",
                        DestOutputProfile=icc_stream,
                    )
                ]
            )
            pdf.save(p, deterministic_id=True)


def _good_pdf_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.pdf"
        text_pdf(src, 3, "Broken sample source")
        return src.read_bytes()


def make_truncated(p: Path) -> None:
    """First 60% of a valid 3-page PDF: no xref, no trailer, last object cut mid-stream."""
    data = _good_pdf_bytes()
    p.write_bytes(data[: int(len(data) * 0.6)])


def make_bad_xref(p: Path) -> None:
    """Valid body, but startxref points at a wrong offset. qpdf/pikepdf can rebuild it."""
    data = _good_pdf_bytes()
    i = data.rindex(b"startxref")
    p.write_bytes(data[:i] + b"startxref\n999999\n%%EOF\n")


# --------------------------------------------------------------------------- images


def make_jpeg_exif(p: Path) -> None:
    """JPEG with EXIF (camera, date, GPS, orientation=6) for metadata-strip and auto-rotate."""
    from PIL import Image

    img = _photo()
    exif = Image.Exif()
    exif[0x010F] = "FileX Camera Co"  # Make
    exif[0x0110] = "Corpus-1"  # Model
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display
    exif[0x0132] = "2026:01:01 12:00:00"  # DateTime
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (52.0, 31.0, 12.0)
    gps[3], gps[4] = "E", (13.0, 24.0, 36.0)
    img.save(p, "JPEG", quality=85, exif=exif)


def make_jpeg_cmyk(p: Path) -> None:
    _photo(400, 300).convert("CMYK").save(p, "JPEG", quality=85)


def make_png_rgba(p: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 20, 380, 280), 30, fill=(30, 120, 220, 200))
    d.ellipse((150, 100, 250, 200), fill=(255, 255, 255, 255))
    img.save(p, "PNG", optimize=True)


def make_tiff(p: Path) -> None:
    _scan_image(_sample_lines(30, 7), 100, 0.0, 7).save(
        p, "TIFF", compression="tiff_lzw", dpi=(100, 100)
    )


def make_tiff_multipage(p: Path) -> None:
    """3-page bilevel CCITT G4 TIFF, the classic fax/scanner output."""
    pages = [
        _scan_image(_sample_lines(30, s), 150, 0.0, s)
        .point(lambda v: 255 if v > 128 else 0)
        .convert("1")
        for s in (11, 12, 13)
    ]
    pages[0].save(
        p, "TIFF", compression="group4", save_all=True, append_images=pages[1:], dpi=(150, 150)
    )


def make_heic(p: Path) -> None:
    import pillow_heif

    pillow_heif.register_heif_opener()
    _photo(320, 240).save(p, "HEIF", quality=60)


def make_webp(p: Path) -> None:
    _photo(320, 240).save(p, "WEBP", quality=70)


# --------------------------------------------------------------------------- office


def _docx(p: Path, paragraphs_per_section: int, sections: int) -> None:
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.core_properties.author = "Jane Example"
    doc.core_properties.title = "FileX DOCX sample"
    doc.core_properties.created = FIXED_DATE.replace(tzinfo=None)
    doc.add_heading("FileX DOCX sample", 0)
    rnd = random.Random(SEED)
    for s in range(1, sections + 1):
        doc.add_heading(f"Section {s}", 1)
        for _ in range(paragraphs_per_section):
            words = LOREM.split()
            rnd.shuffle(words)
            doc.add_paragraph(" ".join(words * 3))
        if s == 1:
            table = doc.add_table(rows=4, cols=3)
            table.style = "Table Grid"
            for r, row in enumerate(
                [
                    ("Item", "Qty", "Price"),
                    ("Pens", "10", "4.50"),
                    ("Paper", "2", "9.99"),
                    ("Ink", "1", "24.00"),
                ]
            ):
                for col, val in enumerate(row):
                    table.cell(r, col).text = val
            doc.add_picture(io.BytesIO(_png_bytes(_photo(320, 240))), width=Inches(3))
            doc.add_paragraph("Bullet one", style="List Bullet")
            doc.add_paragraph("Bullet two", style="List Bullet")
    doc.save(p)


def make_docx(p: Path) -> None:
    _docx(p, 3, 2)


def make_docx_20p(p: Path) -> None:
    """Roughly 20 pages when rendered (spec 5.1 office.to-pdf timing target)."""
    _docx(p, 9, 10)


def make_xlsx(p: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Month", "Income", "Costs", "Profit"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun"], start=2):
        ws.append([m, 1000 + i * 37, 600 + i * 21, f"=B{i}-C{i}"])
        ws[f"B{i}"].number_format = ws[f"C{i}"].number_format = "#,##0.00"
    ws.append(["Total", "=SUM(B2:B7)", "=SUM(C2:C7)", "=SUM(D2:D7)"])
    notes = wb.create_sheet("Notes")
    notes["A1"] = "Second sheet so converters must handle more than one."
    notes["A2"] = FIXED_DATE.replace(tzinfo=None)
    wb.properties.creator = "Jane Example"
    wb.properties.created = wb.properties.modified = FIXED_DATE.replace(tzinfo=None)
    wb.save(p)


def make_xls(p: Path) -> None:
    """Legacy BIFF8 .xls (OLE2 container), written by xlwt."""
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Legacy")
    for r, row in enumerate([("Item", "Qty"), ("Pens", 10), ("Paper", 2)]):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    ws.write(3, 1, xlwt.Formula("SUM(B2:B3)"))
    wb.save(str(p))


def make_pptx(p: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.core_properties.author = "Jane Example"
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "FileX PPTX sample"
    s.placeholders[1].text = "Generated by make_corpus.py"
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Bullets"
    tf = s.placeholders[1].text_frame
    tf.text = "First point"
    for t in ("Second point", "Third point"):
        tf.add_paragraph().text = t
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Picture"
    s.shapes.add_picture(
        io.BytesIO(_png_bytes(_photo(320, 240))), Inches(2), Inches(2), width=Inches(5)
    )
    prs.save(p)


def make_odt(p: Path) -> None:
    """Minimal valid ODF text document, hand-built (mimetype first and stored, per ODF spec)."""
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.2">
 <office:body><office:text>
  <text:h text:outline-level="1">FileX ODT sample</text:h>
  <text:p>{escape(LOREM)}</text:p>
  <text:p>{escape(LOREM[::-1])}</text:p>
  <table:table table:name="T1"><table:table-column table:number-columns-repeated="2"/>
   <table:table-row><table:table-cell><text:p>Item</text:p></table:table-cell>
    <table:table-cell><text:p>Qty</text:p></table:table-cell></table:table-row>
   <table:table-row><table:table-cell><text:p>Pens</text:p></table:table-cell>
    <table:table-cell><text:p>10</text:p></table:table-cell></table:table-row>
  </table:table>
 </office:text></office:body>
</office:document-content>"""
    meta = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
 xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.2">
 <office:meta><dc:title>FileX ODT sample</dc:title><dc:creator>Jane Example</dc:creator>
 <meta:generator>FileX make_corpus.py</meta:generator></office:meta></office:document-meta>"""
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
 manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:version="1.2"
  manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""
    with zipfile.ZipFile(p, "w") as zf:
        _zip_write(zf, "mimetype", "application/vnd.oasis.opendocument.text", store=True)
        _zip_write(zf, "content.xml", content)
        _zip_write(zf, "meta.xml", meta)
        _zip_write(zf, "META-INF/manifest.xml", manifest)


# --------------------------------------------------------------------------- ebooks

CHAPTERS = [(f"Chapter {i}", " ".join([LOREM] * 4)) for i in (1, 2, 3)]


def _cover_png() -> bytes:
    from PIL import ImageDraw

    img = _photo(300, 450)
    ImageDraw.Draw(img).text((20, 200), "FileX Sample Book", fill=(255, 255, 255))
    return _png_bytes(img)


def make_epub(p: Path) -> None:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("urn:uuid:3f1f2b9e-0000-4000-8000-f11e0c0a1501")
    book.set_title("FileX Sample Book")
    book.set_language("en")
    book.add_author("Jane Example")
    book.set_cover("cover.png", _cover_png())
    items = []
    for i, (title, body) in enumerate(CHAPTERS, start=1):
        ch = epub.EpubHtml(title=title, file_name=f"chap_{i}.xhtml", lang="en")
        ch.content = f"<h1>{title}</h1><p>{escape(body)}</p>"
        book.add_item(ch)
        items.append(ch)
    book.toc = items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    epub.write_epub(str(p), book)


def make_fb2(p: Path) -> None:
    sections = "\n".join(
        f"<section><title><p>{t}</p></title><p>{escape(b)}</p></section>" for t, b in CHAPTERS
    )
    cover = base64.b64encode(_cover_png()).decode()
    p.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"
 xmlns:l="http://www.w3.org/1999/xlink">
 <description>
  <title-info><genre>prose</genre>
   <author><first-name>Jane</first-name><last-name>Example</last-name></author>
   <book-title>FileX Sample Book</book-title>
   <coverpage><image l:href="#cover.png"/></coverpage><lang>en</lang></title-info>
  <document-info><author><nickname>filex</nickname></author><program-used>make_corpus.py</program-used>
   <date value="2026-01-01">2026-01-01</date><id>filex-corpus-fb2-1</id><version>1.0</version></document-info>
 </description>
 <body>{sections}</body>
 <binary id="cover.png" content-type="image/png">{cover}</binary>
</FictionBook>
""",
        encoding="utf-8",
    )


def make_cbz(p: Path) -> None:
    """5-page comic: numbered PNG pages plus ComicInfo.xml."""
    from PIL import ImageDraw

    with zipfile.ZipFile(p, "w") as zf:
        for i in range(1, 6):
            img = _photo(400, 600)
            d = ImageDraw.Draw(img)
            d.rectangle((20, 20, 380, 120), fill=(255, 255, 255), outline=(0, 0, 0), width=3)
            d.text((40, 60), f"Panel page {i}", fill=(0, 0, 0))
            _zip_write(zf, f"page_{i:03d}.png", _png_bytes(img), store=True)
        _zip_write(
            zf,
            "ComicInfo.xml",
            '<?xml version="1.0"?><ComicInfo><Title>FileX Sample Comic'
            "</Title><Writer>Jane Example</Writer><PageCount>5</PageCount></ComicInfo>",
        )


def _calibre() -> str | None:
    found = shutil.which("ebook-convert")
    if found:
        return found
    win = Path(r"C:\Program Files\Calibre2\ebook-convert.exe")
    return str(win) if win.exists() else None


def _calibre_from_epub(p: Path) -> None:
    exe = _calibre()
    if not exe:
        raise SkipFile("Calibre ebook-convert not found; keeping the committed file")
    epub_path = CORPUS / "ebook" / "book.epub"
    subprocess.run([exe, str(epub_path), str(p)], check=True, capture_output=True, timeout=300)


make_mobi = make_azw3 = _calibre_from_epub

DJVU_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/4/43/UN_Security_Council_Resolution_1837.djvu"
)
DJVU_SHA256 = "732011eb6eede0ca63022c8e818533a04992b4d8aeb7b1247e840df28862b704"


def make_djvu(p: Path) -> None:
    """No free DjVu encoder in Python, so this one is fetched and pinned by hash."""
    if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == DJVU_SHA256:
        return
    req = urllib.request.Request(DJVU_URL, headers={"User-Agent": "FileX-corpus/0.1"})
    data = urllib.request.urlopen(req, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != DJVU_SHA256:
        raise RuntimeError("DJVU download does not match the pinned SHA-256")
    p.write_bytes(data)


# --------------------------------------------------------------------------- odd files


def make_empty(p: Path) -> None:
    p.write_bytes(b"")


def make_zip_as_pdf(p: Path) -> None:
    """A zip archive with a .pdf name: sniffing must reject it (plan task 2)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        _zip_write(zf, "readme.txt", "I am a zip, not a PDF.")
    p.write_bytes(buf.getvalue())


def make_pdf_as_docx(p: Path) -> None:
    """A real PDF with a .docx name: extension must be ignored."""
    p.write_bytes(_good_pdf_bytes())


def make_text_as_pdf(p: Path) -> None:
    p.write_text("This is plain text with a .pdf extension.\n", encoding="utf-8")


def make_png_huge_dims(p: Path) -> None:
    """PNG header claims 40000x40000 px (1.6 Gpx) with almost no data.

    Pillow refuses it at open with DecompressionBombError; tests that the limit stays on.
    The file is 69 bytes and decodes to nothing harmful (the pixel data is missing).
    """

    def chunk(tag: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + tag + data + zlib.crc32(tag + data).to_bytes(4, "big")

    ihdr = (40000).to_bytes(4, "big") * 2 + bytes([1, 0, 0, 0, 0])  # 1-bit greyscale
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        + chunk(b"IEND", b"")
    )


def make_zip_traversal(p: Path) -> None:
    """CBZ whose second entry is named '../outside.txt'. Content is harmless text.

    The spec (5.2) says zip inputs must be checked for path traversal before use.
    """
    with zipfile.ZipFile(p, "w") as zf:
        _zip_write(zf, "page_001.png", _png_bytes(_photo(100, 150)), store=True)
        _zip_write(
            zf,
            "../outside.txt",
            "If you can read this outside the job folder, the traversal check is missing.",
        )


def make_jpeg_truncated(p: Path) -> None:
    buf = io.BytesIO()
    _photo().save(buf, "JPEG", quality=85)
    data = buf.getvalue()
    p.write_bytes(data[: len(data) // 2])


# --------------------------------------------------------------------------- large files


class SkipFile(Exception):
    pass


def make_large_text_500p(p: Path) -> None:
    text_pdf(p, 500, "500 page text PDF")


def _scan_jpegs(pages: int, dpi: int, quality: int, grain: int):
    from PIL import Image

    for i in range(pages):
        img = _scan_image(_sample_lines(60, 100 + i), dpi, 0.6 * math.sin(i), 100 + i)
        if grain:  # heavy grain makes JPEGs big, like a bad colour-mode scan
            img = Image.blend(img, Image.effect_noise(img.size, grain), 0.35)
        yield _jpeg_bytes(img, quality)


def make_scan_50p_30mb(p: Path) -> None:
    """~30 MB, 50 pages, 300 dpi: the spec 5.1 pdf.compress MEDIUM target."""
    images_to_pdf(p, _scan_jpegs(50, 300, 60, 0), 300)


def make_scan_200mb(p: Path) -> None:
    """~200 MB image-only PDF at 300 dpi, noisy so it barely compresses further."""
    images_to_pdf(p, _scan_jpegs(75, 300, 85, 30), 300)


def make_huge_1gb(p: Path, size_mb: int = 1024) -> None:
    """A valid 1-page PDF padded with an unused ~1 GB stream, written in chunks (low RAM).

    For upload-cap and streaming tests (spec 5.1: API RSS stays under 200 MB on a 1 GB upload).
    """
    pad = size_mb * 1024 * 1024
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> "
            b"/XObject << >> >> /PieceInfo << /FileX << /Private 5 0 R >> >> >>"
        ),
    ]
    content = b"BT /F1 24 Tf 72 760 Td (1 GB padded PDF) Tj ET"
    offsets = []
    chunk = b"\0" * (1024 * 1024)
    with open(p, "wb") as f:
        f.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        for i, body in enumerate(objs, start=1):
            offsets.append(f.tell())
            f.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
        offsets.append(f.tell())
        f.write(
            b"4 0 obj\n<< /Length %d >>\nstream\n" % len(content)
            + content
            + b"\nendstream\nendobj\n"
        )
        offsets.append(f.tell())
        f.write(b"5 0 obj\n<< /Length %d >>\nstream\n" % pad)
        f.writelines(chunk for _ in range(size_mb))
        f.write(b"\nendstream\nendobj\n")
        xref = f.tell()
        f.write(b"xref\n0 6\n0000000000 65535 f \n")
        f.writelines(b"%010d 00000 n \n" % off for off in offsets)
        f.write(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref)


def make_many_entries_cbz(p: Path) -> None:
    """CBZ with 20 000 tiny entries, for the zip entry-count cap (spec 5.2)."""
    with zipfile.ZipFile(p, "w") as zf:
        for i in range(20000):
            _zip_write(zf, f"p/{i:05d}.txt", b"x")


def make_high_ratio_epub(p: Path) -> None:
    """EPUB-shaped zip with one 600 MB entry of zeros (~600 KB compressed, ~1000:1).

    Single layer, no nesting. For the 'total uncompressed size' check (spec 5.2).
    """
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _zip_write(zf, "mimetype", "application/epub+zip", store=True)
        info = zipfile.ZipInfo("OEBPS/big.xhtml", date_time=FIXED_DATE.timetuple()[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        with zf.open(info, "w", force_zip64=True) as w:
            block = b"\0" * (1024 * 1024)
            for _ in range(600):
                w.write(block)


# --------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Item:
    path: str  # relative to tests/corpus/
    make: Callable[[Path], None]
    what: str  # what it is / what it tests
    source: str = "Generated by scripts/make_corpus.py"
    licence: str = "CC0-1.0 (generated test data)"
    large: bool = False


GEN = "Generated by scripts/make_corpus.py"
CALIBRE = "Converted from ebook/book.epub with Calibre ebook-convert (GPL tool; output is our CC0 content)"

ITEMS: list[Item] = [
    # PDF
    Item(
        "pdf/text_1p.pdf",
        make_text_1p,
        "1-page text PDF, embedded Bitstream Vera font",
        licence="CC0-1.0; embedded font: Bitstream Vera licence (redistributable)",
    ),
    Item(
        "pdf/text_10p.pdf",
        make_text_10p,
        "10-page text PDF (merge timing target, page tools)",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/mixed_pages.pdf",
        make_mixed_pages,
        "4 pages: A4, Letter, A4 landscape, A4 with /Rotate 90",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/scanned_3p.pdf",
        make_scanned,
        "Image-only 150 dpi scan, no text layer: p1 skewed 2 deg, p2 nearly blank, p3 straight "
        "(OCR, scan.clean deskew/blank removal, compress)",
    ),
    Item(
        "pdf/forms.pdf",
        make_forms,
        "AcroForm: text fields, checkbox, choice, radio group",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/metadata.pdf",
        make_metadata,
        "Info dict + XMP with author, keywords, dates and a custom /Company key",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/encrypted_aes256.pdf",
        make_encrypted_aes256,
        f"AES-256 (R6). User password '{USER_PASSWORD}', owner '{OWNER_PASSWORD}'",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/encrypted_rc4_40.pdf",
        make_encrypted_rc4,
        f"Legacy RC4 40-bit (R2), all permissions off. User '{USER_PASSWORD}', owner '{OWNER_PASSWORD}'",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/encrypted_owner_only.pdf",
        make_encrypted_owner_only,
        f"Opens without a password; owner '{OWNER_PASSWORD}' restricts print/copy/modify (AES-128, R4)",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item(
        "pdf/pdfa_2b.pdf",
        make_pdfa,
        "Intended PDF/A-2b: embedded font, sRGB OutputIntent, pdfaid XMP. Not yet veraPDF-validated",
        licence="CC0-1.0; embedded font: Bitstream Vera licence; ICC: sRGB built by LittleCMS via Pillow",
    ),
    Item(
        "pdf/broken_truncated.pdf",
        make_truncated,
        "First 60% of a valid 3-page PDF: no xref/trailer, last object cut (expect FAILED or partial repair)",
    ),
    Item(
        "pdf/broken_xref.pdf",
        make_bad_xref,
        "Valid objects, startxref points to a bogus offset (pdf.repair should fix it)",
    ),
    # images
    Item(
        "image/photo_exif.jpg",
        make_jpeg_exif,
        "Synthetic photo, EXIF Make/Model/DateTime, GPS 52N 13E, Orientation=6",
    ),
    Item(
        "image/photo_cmyk.jpg", make_jpeg_cmyk, "CMYK JPEG (colour-space handling in image.to-pdf)"
    ),
    Item("image/graphic_rgba.png", make_png_rgba, "PNG with alpha channel"),
    Item(
        "image/scan_page.tiff",
        make_tiff,
        "Single-page greyscale TIFF, LZW, 100 dpi",
        licence="CC0-1.0; text rendered with Bitstream Vera",
    ),
    Item(
        "image/scan_multipage_g4.tiff",
        make_tiff_multipage,
        "3-page bilevel TIFF, CCITT G4, 150 dpi",
        licence="CC0-1.0; text rendered with Bitstream Vera",
    ),
    Item("image/photo.heic", make_heic, "HEIC (HEVC in HEIF) written by pillow-heif"),
    Item("image/photo.webp", make_webp, "Lossy WebP"),
    # office
    Item(
        "office/report.docx",
        make_docx,
        "DOCX: headings, paragraphs, table, embedded PNG, bullets, author in core props",
    ),
    Item(
        "office/report_20p.docx",
        make_docx_20p,
        "About 20 pages of DOCX (spec 5.1 office.to-pdf timing target)",
    ),
    Item("office/budget.xlsx", make_xlsx, "XLSX: 2 sheets, formulas, number formats, a date"),
    Item("office/legacy.xls", make_xls, "Legacy BIFF8 .xls (OLE2) written by xlwt, with a formula"),
    Item("office/slides.pptx", make_pptx, "PPTX: 3 slides (title, bullets, picture)"),
    Item(
        "office/letter.odt", make_odt, "Minimal ODT built by hand: heading, paragraphs, table, meta"
    ),
    # ebooks
    Item("ebook/book.epub", make_epub, "EPUB 3 (ebooklib): 3 chapters, cover, nav + NCX"),
    Item("ebook/book.fb2", make_fb2, "FictionBook 2: 3 sections, base64 cover"),
    Item("ebook/book.mobi", make_mobi, "MOBI (from book.epub)", source=CALIBRE),
    Item("ebook/book.azw3", make_azw3, "AZW3 / KF8, no DRM (from book.epub)", source=CALIBRE),
    Item("ebook/comic.cbz", make_cbz, "CBZ: 5 PNG pages + ComicInfo.xml"),
    Item(
        "ebook/un_resolution_1837.djvu",
        make_djvu,
        "4-page bundled DjVu (DJVM), scanned document",
        source=f"Wikimedia Commons, 'UN Security Council Resolution 1837.djvu', {DJVU_URL} "
        f"(sha256 {DJVU_SHA256[:16]}...)",
        licence="Public domain (UN document; tagged Public domain on Commons)",
    ),
    # odd
    Item("odd/empty.pdf", make_empty, "Zero bytes"),
    Item("odd/zip_named.pdf", make_zip_as_pdf, "Zip archive named .pdf (sniffing must reject)"),
    Item(
        "odd/pdf_named.docx",
        make_pdf_as_docx,
        "Real PDF named .docx (extension must be ignored)",
        licence="CC0-1.0; embedded font: Bitstream Vera licence",
    ),
    Item("odd/text_named.pdf", make_text_as_pdf, "Plain text named .pdf"),
    Item(
        "odd/png_huge_dimensions.png",
        make_png_huge_dims,
        "69-byte PNG claiming 40000x40000 px: must hit Pillow's decompression-bomb guard",
    ),
    Item(
        "odd/zip_traversal.cbz",
        make_zip_traversal,
        "CBZ with an entry named '../outside.txt' (harmless text): zip path-traversal check",
    ),
    Item("odd/truncated.jpg", make_jpeg_truncated, "First half of a valid JPEG"),
    # large, generated on demand into tests/corpus/generated/
    Item("generated/text_500p.pdf", make_large_text_500p, "500-page text PDF (~2 MB)", large=True),
    Item(
        "generated/scan_50p_30mb.pdf",
        make_scan_50p_30mb,
        "50-page 300 dpi image-only scan, ~30 MB (spec 5.1 compress target)",
        large=True,
    ),
    Item(
        "generated/scan_200mb.pdf",
        make_scan_200mb,
        "75-page 300 dpi noisy image-only scan, ~200 MB",
        large=True,
    ),
    Item(
        "generated/huge_1gb.pdf",
        make_huge_1gb,
        "Valid 1-page PDF padded with an unused ~1 GB stream (upload cap / streaming)",
        large=True,
    ),
    Item(
        "generated/many_entries.cbz",
        make_many_entries_cbz,
        "CBZ with 20 000 entries (zip entry-count cap)",
        large=True,
    ),
    Item(
        "generated/high_ratio.epub",
        make_high_ratio_epub,
        "EPUB-shaped zip, one 600 MB entry of zeros, ~1000:1 (uncompressed-size cap)",
        large=True,
    ),
]


# --------------------------------------------------------------------------- README

# The LFI samples tests/integration/lfi_corpus.py builds at test time (test_lfi checks that the
# README lists every one). The last three are positive controls: the canary is in the document.
LFI_SAMPLES = [
    ("lfi_pdf", "pdf", "external /F content and image streams, /FS /URL, reference XObject /Ref"),
    ("linked_odt", "odt", "linked sections and image, LinkUpdateMode=2"),
    ("linked_ods", "ods", "WEBSERVICE, external reference, LinkUpdateMode=2"),
    ("linked_docx", "docx", "external image r:link, INCLUDETEXT field"),
    (
        "docx_template_altchunk",
        "docx",
        "attachedTemplate + linkStyles, external altChunk, subDocument",
    ),
    ("webservice_xlsx", "xlsx", "WEBSERVICE formulas"),
    ("xlsx_external_link", "xlsx", "externalLink part, fullCalcOnLoad"),
    ("pptx_linked_media", "pptx", "linked picture and video"),
    ("odp_linked_media", "odp", "linked images, object and media frames, LinkUpdateMode=2"),
    ("rtf_include", "rtf", "INCLUDETEXT / INCLUDEPICTURE fields"),
    ("csv_formulas", "csv", "WEBSERVICE, external reference, INDIRECT, FILTERXML"),
    ("html_lfi", "html", "img/iframe/object/embed/link/@import/CSS url()"),
    ("md_lfi", "md", "image paths, raw HTML, include attribute"),
    ("fb2_lfi", "fb2", "image l:href outside the book"),
    ("fb2_entity", "fb2", "external XML entities"),
    ("epub_lfi", "epub", "XHTML references and OPF item outside the book"),
    ("cbz_symlink", "cbz", "symlink entries"),
    ("djvu_incl", "djvu", "INCL chunks naming outside files"),
    ("planted_pdf", "pdf", "positive control: canary drawn by the page itself"),
    ("planted_rtf", "rtf", "positive control: canary as document text"),
    ("planted_djvu", "djvu", "positive control: canary as the page's hidden text"),
]


def write_readme() -> None:
    def table(items):
        rows = ["| File | What it is / tests | Source | Licence |", "|---|---|---|---|"]
        for it in items:
            rows.append(f"| `{it.path}` | {it.what} | {it.source} | {it.licence} |")
        return "\n".join(rows)

    def lfi_table():
        rows = ["| Sample | Format | References |", "|---|---|---|"]
        rows += [f"| `{name}` | {fmt} | {what} |" for name, fmt, what in LFI_SAMPLES]
        return "\n".join(rows)

    small = [i for i in ITEMS if not i.large]
    large = [i for i in ITEMS if i.large]
    text = f"""# QA corpus

<!-- Written by scripts/make_corpus.py. Edit the ITEMS table there, not this file. -->

Small, licence-clean sample files for tool tests (`tests/tools/`), the API tests and the
broken-file suite. Almost everything is generated by code, so the corpus is reproducible and
contains nothing personal: names such as "Jane Example" and the GPS position are made up.

Known passwords: user `{USER_PASSWORD}`, owner `{OWNER_PASSWORD}`.

## Regenerate

```bash
python -m venv .venv
.venv/Scripts/pip install -r scripts/requirements-corpus.txt   # Linux/macOS: .venv/bin/pip
.venv/Scripts/python scripts/make_corpus.py            # small committed files + this README
.venv/Scripts/python scripts/make_corpus.py --large    # big files into tests/corpus/generated/
.venv/Scripts/python scripts/make_corpus.py --large --only "scan_*"
```

Files are content-stable but not always byte-identical between runs (zip timestamps inside
Office files, random JPEG noise, Calibre output). `book.mobi` and `book.azw3` need Calibre's
`ebook-convert`; without it the committed copies are kept. The DJVU file is downloaded and
checked against a pinned SHA-256 only if it is missing or changed.

`tests/corpus/generated/` is git-ignored. Never commit large files.

## Committed files

{table(small)}

## Generated on demand (`--large`)

{table(large)}

## Local-file-inclusion samples (built at test time)

Plan task 64 (review H2 fix 4). `tests/integration/lfi_corpus.py` builds these when
`tests/integration/test_lfi.py` runs, because each one names the absolute path of another job's
files (and their path relative to the step's scratch dir), which exist only then; nothing is
committed. They hold references only: no exploit, no payload, no network (the one URL besides
`file:` is on the reserved `.invalid` domain). Each refers to the stand-in other job's files
(canary text that is also a PDF content stream drawing `FILEXCANARY`, a PNG with a canary
`tEXt` chunk, an ODS, a PDF, a DOCX and a DjVu component holding the canary) and, where the
format allows, to `/etc/hostname`. `python tests/integration/lfi_corpus.py <dir>` writes them
all to look at. Source: generated by that script; licence CC0-1.0 (generated test data;
`pptx_linked_media` and `epub_lfi` start from `office/slides.pptx` and `ebook/book.epub`).
LibreOffice's binary `.doc`, `.xls` and `.ppt` cases are converted from `linked_odt`,
`linked_ods` and `odp_linked_media` by `office.convert` inside the test.

{lfi_table()}

## Not in the corpus yet

- Legacy `.doc` and `.ppt` (needs LibreOffice to generate freely).
- A digitally signed PDF (add once `cert.create` / `pdf.sign` exist).
- PDF/A sample validated by veraPDF (the current one is unverified).
- Non-Latin text (Cyrillic, CJK, RTL) PDFs and Office files: needs a redistributable font
  with those scripts (for example Noto, OFL).
- Real camera photos and real scanner output (all images here are synthetic).
- CBR / CB7 (RAR and 7z writers are not free/pure-Python).
"""
    (CORPUS / "README.md").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- main


def build(items: list[Item]) -> int:
    failures = 0
    for it in items:
        out = CORPUS / it.path
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            it.make(out)
        except SkipFile as e:
            print(f"skip  {it.path}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - report and carry on, so one missing tool does not stop the rest
            failures += 1
            print(f"FAIL  {it.path}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        print(f"ok    {it.path}  ({out.stat().st_size:,} bytes)")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--large", action="store_true", help="build the big files instead of the small ones"
    )
    ap.add_argument("--small", action="store_true", help="with --large, build the small ones too")
    ap.add_argument("--only", help="glob on the file name, e.g. 'scan_*' or '*.pdf'")
    args = ap.parse_args(argv)

    want_small = args.small or not args.large
    items = [i for i in ITEMS if (i.large and args.large) or (not i.large and want_small)]
    if args.only:
        items = [i for i in items if fnmatch.fnmatch(Path(i.path).name, args.only)]
    # book.epub must exist before Calibre converts it.
    items.sort(key=lambda i: i.make in (make_mobi,))
    failures = build(items)
    if want_small and not args.only:
        write_readme()
        print("ok    README.md")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
