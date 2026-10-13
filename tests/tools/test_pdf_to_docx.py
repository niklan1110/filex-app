"""pdf.to-docx on real corpus files and on small PDFs built here (plan task 32)."""

import io
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
from conftest import PDF

from filex.formats import sniff
from filex.registry import StepError, ToolOptions, discover
from filex.tools.convert.pdf_to_docx import _paragraph, run

docx = pytest.importorskip("docx")
pymupdf = pytest.importorskip("pymupdf")

PAGE_BREAK = 'w:type="page"'


def convert(ctx, path: Path) -> Path:
    (out,) = run(ctx, path, ToolOptions())
    assert out.parent == ctx.out_dir
    assert sniff(out, out.name) == "docx"
    return out


def body_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


def text_of(path: Path) -> str:
    d = docx.Document(str(path))
    cells = [c.text for t in d.tables for row in t.rows for c in row.cells]
    return "\n".join([p.text for p in d.paragraphs] + cells)


def png(w: int, h: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def test_text_is_kept_in_page_order_with_a_break_between_pages(step_ctx):
    out = convert(step_ctx, PDF / "text_10p.pdf")
    text = text_of(out)
    found = [int(n) for n in re.findall(r"page (\d+) of 10", text)]
    assert found == list(range(1, 11))
    assert body_xml(out).count(PAGE_BREAK) == 9
    assert step_ctx.warnings == []


def test_titles_become_headings(step_ctx):
    out = convert(step_ctx, PDF / "text_10p.pdf")
    headings = [p.text for p in docx.Document(str(out)).paragraphs if p.style.name == "Heading 1"]
    assert len(headings) == 10 and headings[0].endswith("page 1 of 10")


def cyrillic_font() -> str:
    # The built-in Helvetica cannot draw Cyrillic (it writes wrong characters), so embed a real
    # TrueType font: DejaVu on the Linux images, Arial on Windows. Skip where neither exists.
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        if Path(path).is_file():
            return path
    pytest.skip("no Cyrillic-capable TrueType font on this machine")


def test_cyrillic_text_a_table_and_an_image_come_through(step_ctx, tmp_path):
    src = tmp_path / "ru.pdf"
    font = {"fontname": "cyr", "fontfile": cyrillic_font()}
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Привет, мир! Отчёт", fontsize=12, **font)
        page.insert_image(pymupdf.Rect(72, 100, 272, 200), stream=png(200, 100))
        x, y = [72, 200, 328], [260, 290, 320]  # a ruled 2x2 table
        for xi in x:
            page.draw_line((xi, y[0]), (xi, y[-1]))
        for yi in y:
            page.draw_line((x[0], yi), (x[-1], yi))
        for r, row in enumerate([["Регион", "Сумма"], ["Север", "120"]]):
            for c, cell in enumerate(row):
                page.insert_text((x[c] + 4, y[r] + 20), cell, fontsize=11, **font)
        pdf.save(src)
    out = convert(step_ctx, src)
    d = docx.Document(str(out))
    assert "Привет, мир! Отчёт" in text_of(out).replace("\xa0", " ")
    assert len(d.inline_shapes) == 1
    ((table),) = d.tables
    assert [[c.text for c in row.cells] for row in table.rows] == [
        ["Регион", "Сумма"],
        ["Север", "120"],
    ]
    assert "Регион" not in "\n".join(p.text for p in d.paragraphs)  # not written twice


def test_scan_gives_its_page_images_and_suggests_ocr(step_ctx):
    out = convert(step_ctx, PDF / "scanned_3p.pdf")
    assert len(docx.Document(str(out)).inline_shapes) == 3
    assert body_xml(out).count(PAGE_BREAK) == 2
    ((code, message),) = step_ctx.warnings
    assert code == "NO_TEXT" and "OCR" in message and "only the page images" in message


def test_images_over_the_pixel_limit_are_left_out(step_ctx):
    step_ctx.max_pixels = 1_000_000  # the scan's pages are 1240x1753
    out = convert(step_ctx, PDF / "scanned_3p.pdf")
    assert len(docx.Document(str(out)).inline_shapes) == 0
    assert "IMAGES_SKIPPED" in [c for c, _ in step_ctx.warnings]


def masked_image_pdf(path: Path, mask: bytes, mask_filter: str) -> Path:
    """One 20x20 RGB image whose soft mask declares 20x20 but holds ``mask`` (security review
    L-5: a JPX codestream may be far larger than the size it declares)."""
    import zlib

    import pikepdf

    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 200))
        image = {
            "/Type": pikepdf.Name.XObject,
            "/Subtype": pikepdf.Name.Image,
            "/Width": 20,
            "/Height": 20,
            "/BitsPerComponent": 8,
        }
        smask = pikepdf.Stream(pdf, mask)
        smask.update(
            image | {"/ColorSpace": pikepdf.Name.DeviceGray, "/Filter": pikepdf.Name(mask_filter)}
        )
        img = pikepdf.Stream(pdf, zlib.compress(b"\x80" * 20 * 20 * 3))
        img.update(
            image
            | {
                "/ColorSpace": pikepdf.Name.DeviceRGB,
                "/Filter": pikepdf.Name.FlateDecode,
                "/SMask": smask,
            }
        )
        page = pdf.pages[0]
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        page.Contents = pdf.make_stream(b"q 100 0 0 100 50 50 cm /Im0 Do Q")
        pdf.save(path)
    return path


def jpx_gray(size: int) -> bytes:
    from PIL import Image, features

    if not features.check("jpg_2000"):
        pytest.skip("Pillow has no JPEG 2000 encoder here")
    buf = io.BytesIO()
    Image.new("L", (size, size), 128).save(buf, "JPEG2000")
    return buf.getvalue()


def test_a_soft_mask_bigger_than_it_declares_is_left_out(step_ctx, tmp_path):
    step_ctx.max_pixels = 10_000  # the image is 20x20; the mask decodes to 400x400
    src = masked_image_pdf(tmp_path / "jpx_mask.pdf", jpx_gray(400), "/JPXDecode")
    out = convert(step_ctx, src)
    assert len(docx.Document(str(out)).inline_shapes) == 0
    assert "IMAGES_SKIPPED" in [c for c, _ in step_ctx.warnings]


def test_a_soft_mask_of_another_size_is_left_out_not_a_crash(step_ctx, tmp_path):
    src = masked_image_pdf(tmp_path / "jpx_mask.pdf", jpx_gray(40), "/JPXDecode")
    out = convert(step_ctx, src)
    assert len(docx.Document(str(out)).inline_shapes) == 0
    assert "IMAGES_SKIPPED" in [c for c, _ in step_ctx.warnings]


def test_a_matching_soft_mask_keeps_the_image(step_ctx, tmp_path):
    import zlib

    src = masked_image_pdf(tmp_path / "mask.pdf", zlib.compress(b"\xff" * 400), "/FlateDecode")
    out = convert(step_ctx, src)
    assert len(docx.Document(str(out)).inline_shapes) == 1


def test_a_new_page_size_starts_a_new_section(step_ctx):
    out = convert(step_ctx, PDF / "mixed_pages.pdf")  # A4, Letter, A4 landscape, A4 rotated
    sections = docx.Document(str(out)).sections
    assert [s.page_width > s.page_height for s in sections] == [False, False, True]
    assert body_xml(out).count(PAGE_BREAK) + len(sections) - 1 == 3


def test_columns_are_flattened_with_a_warning(step_ctx, tmp_path):
    src = tmp_path / "columns.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for n, x in enumerate((50, 310)):
            page.insert_textbox(pymupdf.Rect(x, 72, x + 230, 500), f"Column {n + 1}. " * 60)
        pdf.save(src)
    text = text_of(convert(step_ctx, src))
    assert text.index("Column 1.") < text.index("Column 2.")
    ((code, message),) = step_ctx.warnings
    assert code == "LAYOUT_SIMPLIFIED" and "one column" in message


def test_xml_control_characters_are_dropped(step_ctx):
    span = {"text": "a\x00b\x0bc", "size": 10, "flags": 0, "color": 0}
    block = {"bbox": (0, 0, 100, 10), "lines": [{"bbox": (0, 0, 100, 10), "spans": [span]}]}
    d = docx.Document()
    _paragraph(d, block, 10)
    assert d.paragraphs[-1].text == "abc"


def test_owner_restricted_pdf_converts(step_ctx):
    assert "Owner password only" in text_of(convert(step_ctx, PDF / "encrypted_owner_only.pdf"))


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", ToolOptions())
    assert e.value.code == "TOO_MANY_PAGES"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "encrypted_rc4_40.pdf", "ENCRYPTED_PDF"),
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
        (PDF / "broken_truncated.pdf", "INVALID_PDF"),
        (PDF.parent / "odd/empty.pdf", "INVALID_PDF"),
        (PDF.parent / "odd/zip_named.pdf", "INVALID_PDF"),
    ],
)
def test_bad_files(step_ctx, bad, code):
    with pytest.raises(StepError) as e:
        run(step_ctx, bad, ToolOptions())
    assert e.value.code == code


def test_damaged_file_is_repaired_and_converted(step_ctx):
    out = convert(step_ctx, PDF / "broken_xref.pdf")
    assert text_of(out).strip()
    assert [c for c, _ in step_ctx.warnings] == ["INPUT_REPAIRED"]


def test_100_pages_finish_well_within_the_timeout(step_ctx, tmp_path):
    src = tmp_path / "100p.pdf"
    with pymupdf.open() as pdf, pymupdf.open(PDF / "text_10p.pdf") as ten:
        for _ in range(10):
            pdf.insert_pdf(ten)
        pdf.save(src)
    start = time.monotonic()
    out = convert(step_ctx, src)
    elapsed = time.monotonic() - start
    assert body_xml(out).count(PAGE_BREAK) == 99
    assert elapsed < discover()["pdf.to-docx"].limits.timeout_s / 4


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_libreoffice_opens_the_result(step_ctx, tmp_path):
    out = convert(step_ctx, PDF / "text_10p.pdf")
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation=file://{tmp_path}/lo",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_path),
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    with pymupdf.open(tmp_path / "document.pdf") as pdf:
        assert len(pdf) == 10
