"""comic.to-pdf (plan task 52). CBZ runs everywhere; CBR needs libarchive (worker-ebook image).
Pages must come out in natural order with their pixels unchanged."""

import io
import zipfile

import pytest
from conftest import CORPUS
from ebook_samples import EBOOK, scratch_copy
from rar_writer import rar4, rar5

from filex.formats import sniff
from filex.registry import StepError, ToolOptions
from filex.tools.ebook.comic_to_pdf import is_page, natural_key, run

pypdfium2 = pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")


def _libarchive() -> bool:
    try:
        import libarchive  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


needs_libarchive = pytest.mark.skipif(not _libarchive(), reason="needs libarchive (worker-ebook)")


def png(color: tuple[int, int, int], size=(40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 70), color).save(buf, "JPEG", quality=80)
    return buf.getvalue()


# Archive order differs from reading order on purpose: page10 must come after page2.
PAGES = [
    ("pages/page10.png", (10, 10, 10)),
    ("pages/page2.png", (2, 2, 2)),
    ("pages/page1.png", (1, 1, 1)),
    ("ComicInfo.xml", None),
    ("__MACOSX/pages/._page1.png", None),
]
ORDER = [(1, 1, 1), (2, 2, 2), (10, 10, 10)]


def entries() -> list[tuple[str, bytes]]:
    return [(n, png(c) if c else b"<ComicInfo/>") for n, c in PAGES]


def cbz(tmp_path, items=None):
    p = tmp_path / "in" / "0.cbz"
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w") as zf:
        for name, data in items or entries():
            zf.writestr(name, data)
    return p


def page_colors(pdf_path) -> list[tuple[int, int, int]]:
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        out = []
        for i in range(len(pdf)):
            (img,) = [o for o in pdf[i].get_objects() if o.type == pypdfium2.raw.FPDF_PAGEOBJ_IMAGE]
            out.append(img.get_bitmap().to_pil().convert("RGB").getpixel((5, 5)))
        return out
    finally:
        pdf.close()


def convert(step_ctx, src):
    step_ctx.target = "pdf"
    (out,) = run(step_ctx, src, ToolOptions())
    assert sniff(out) == "pdf"
    return out


def test_natural_order():
    names = ["p10.png", "p2.png", "P1.png", "a/p3.png", "p02b.png"]
    assert sorted(names, key=natural_key) == ["a/p3.png", "P1.png", "p2.png", "p02b.png", "p10.png"]


def test_only_visible_images_are_pages():
    from filex.tools._archive import Entry

    def e(name):
        return Entry(name, 1, False, 0)

    assert is_page(e("x/01.JPG")) and is_page(e("02.webp"))
    assert not is_page(e("ComicInfo.xml")) and not is_page(e(".hidden.png"))
    assert not is_page(e("__MACOSX/01.png")) and not is_page(e("x/.thumbs/01.png"))


def test_cbz_pages_in_natural_order_with_identical_pixels(step_ctx, tmp_path):
    assert page_colors(convert(step_ctx, cbz(tmp_path))) == ORDER


def test_jpeg_pages_are_embedded_byte_for_byte(step_ctx, tmp_path):
    data = jpeg((200, 30, 30))
    out = convert(step_ctx, cbz(tmp_path, [("001.jpg", data)]))
    assert data in out.read_bytes()


def test_corpus_cbz_converts(step_ctx, tmp_path):
    out = convert(step_ctx, scratch_copy(EBOOK / "comic.cbz", tmp_path / "in", "cbz"))
    pdf = pypdfium2.PdfDocument(out)
    assert len(pdf) == 5
    pdf.close()


def test_traversal_cbz_is_refused(step_ctx, tmp_path):
    src = scratch_copy(CORPUS / "odd" / "zip_traversal.cbz", tmp_path / "in", "cbz")
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, src, ToolOptions())
    assert e.value.code == "INVALID_INPUT"


def test_nested_archive_cbz_is_refused(step_ctx, tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("a.png", png((1, 2, 3)))
    src = cbz(tmp_path, [("001.png", png((1, 1, 1))), ("002.png", inner.getvalue())])
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, src, ToolOptions())
    assert e.value.code == "INVALID_INPUT" and "another archive" in e.value.message


def test_cbz_without_images_is_refused(step_ctx, tmp_path):
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, cbz(tmp_path, [("ComicInfo.xml", b"<x/>")]), ToolOptions())
    assert e.value.code == "INVALID_INPUT" and "no page images" in e.value.message


def test_more_pages_than_the_limit_are_refused(step_ctx, tmp_path):
    step_ctx.max_pages = 2
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, cbz(tmp_path), ToolOptions())
    assert e.value.code == "TOO_MANY_PAGES"


def test_oversized_page_is_refused(step_ctx, tmp_path):
    step_ctx.max_pixels = 1000
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, cbz(tmp_path), ToolOptions())
    assert e.value.code == "INVALID_INPUT" and "megapixels" in e.value.message


@needs_libarchive
@pytest.mark.parametrize("make", [rar4, rar5], ids=["rar4", "rar5"])
def test_cbr_pages_in_natural_order_with_identical_pixels(step_ctx, tmp_path, make):
    src = tmp_path / "in" / "0.cbr"
    src.parent.mkdir()
    src.write_bytes(make(entries()))
    assert sniff(src) == "cbr"
    assert page_colors(convert(step_ctx, src)) == ORDER


@needs_libarchive
def test_cbr_with_a_nested_archive_is_refused(step_ctx, tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("a.png", png((1, 2, 3)))
    src = tmp_path / "0.cbr"
    src.write_bytes(rar5([("001.png", png((1, 1, 1))), ("002.jpg", inner.getvalue())]))
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, src, ToolOptions())
    assert e.value.code == "INVALID_INPUT"


@needs_libarchive
@pytest.mark.parametrize("make", [rar4, rar5], ids=["rar4", "rar5"])
def test_cbr_traversal_is_refused(step_ctx, tmp_path, make):
    src = tmp_path / "0.cbr"
    src.write_bytes(make([("../001.png", png((1, 1, 1)))]))
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, src, ToolOptions())
    assert e.value.code == "INVALID_INPUT" and "unsafe name" in e.value.message


FIXTURES = CORPUS.parent / "unit" / "archive_fixtures"


@needs_libarchive
@pytest.mark.parametrize(
    ("fixture", "words"),
    [
        ("rar4_symlink.rar", "link"),  # real RAR4 with a symlink: refused before unpacking
        ("rar5_symlink.rar", "link"),
        ("rar5_hardlink.rar", "link"),
        ("rar5_solid.rar", "no page images"),  # real solid RAR5, text files only
    ],
)
def test_real_rar_archives_without_safe_pages_are_refused(step_ctx, tmp_path, fixture, words):
    src = scratch_copy(FIXTURES / fixture, tmp_path / "in", "cbr")
    assert sniff(src) == "cbr"
    step_ctx.target = "pdf"
    with pytest.raises(StepError) as e:
        run(step_ctx, src, ToolOptions())
    assert e.value.code == "INVALID_INPUT" and words in e.value.message, e.value.message
