"""Checks that the QA corpus files are what the README says they are.

Needs the corpus requirements (scripts/requirements-corpus.txt). Skips if they are missing,
so the registry unit tests still run in a minimal environment.
"""

import importlib.util
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

pikepdf = pytest.importorskip("pikepdf")
pymupdf = pytest.importorskip("pymupdf")
PIL = pytest.importorskip("PIL")
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "corpus"

_spec = importlib.util.spec_from_file_location("make_corpus", ROOT / "scripts" / "make_corpus.py")
make_corpus = importlib.util.module_from_spec(_spec)
sys.modules["make_corpus"] = make_corpus
_spec.loader.exec_module(make_corpus)

SMALL = [i for i in make_corpus.ITEMS if not i.large]
USER, OWNER = make_corpus.USER_PASSWORD, make_corpus.OWNER_PASSWORD


def f(rel: str) -> Path:
    return CORPUS / rel


# --- bookkeeping


@pytest.mark.parametrize("item", SMALL, ids=lambda i: i.path)
def test_committed_file_exists_with_provenance(item):
    assert f(item.path).exists()
    assert item.source and item.licence
    assert f"`{item.path}`" in (CORPUS / "README.md").read_text(encoding="utf-8")


def test_no_unlisted_files_in_corpus():
    listed = {i.path for i in make_corpus.ITEMS}
    on_disk = {
        p.relative_to(CORPUS).as_posix()
        for p in CORPUS.rglob("*")
        if p.is_file() and p.name != "README.md" and "generated" not in p.parts
    }
    assert on_disk <= listed, f"files without provenance: {sorted(on_disk - listed)}"


def test_committed_files_are_small():
    for item in SMALL:
        assert f(item.path).stat().st_size < 1_000_000, item.path


# --- PDF


@pytest.mark.parametrize(
    "rel,pages",
    [
        ("pdf/text_1p.pdf", 1),
        ("pdf/text_10p.pdf", 10),
        ("pdf/mixed_pages.pdf", 4),
        ("pdf/scanned_3p.pdf", 3),
        ("pdf/forms.pdf", 1),
        ("pdf/metadata.pdf", 2),
        ("pdf/pdfa_2b.pdf", 2),
        ("odd/pdf_named.docx", 3),
    ],
)
def test_valid_pdf_page_count(rel, pages):
    with pikepdf.open(f(rel)) as pdf:
        assert len(pdf.pages) == pages


def test_text_pdf_has_extractable_text():
    with pymupdf.open(f("pdf/text_1p.pdf")) as doc:
        assert "Text PDF" in doc[0].get_text()


def test_scanned_pdf_has_no_text_layer():
    with pymupdf.open(f("pdf/scanned_3p.pdf")) as doc:
        assert all(not page.get_text().strip() for page in doc)


def test_mixed_pages_sizes_and_rotation():
    with pikepdf.open(f("pdf/mixed_pages.pdf")) as pdf:
        boxes = [tuple(round(float(v)) for v in p.mediabox) for p in pdf.pages]
        assert len(set(boxes)) == 3
        assert pdf.pages[3].obj.get("/Rotate") == 90


def test_forms_pdf_has_fields():
    with pikepdf.open(f("pdf/forms.pdf")) as pdf:
        names = {str(fld.get("/T")) for fld in pdf.Root.AcroForm.Fields}
        assert {"name", "email", "subscribe", "country", "plan"} <= names


def test_metadata_pdf_has_info_and_xmp():
    with pikepdf.open(f("pdf/metadata.pdf")) as pdf:
        assert str(pdf.docinfo["/Author"]) == "Jane Example"
        assert "/Company" in pdf.docinfo
        assert "Jane Example" in str(pdf.open_metadata().get("dc:creator"))


@pytest.mark.parametrize("rel", ["pdf/encrypted_aes256.pdf", "pdf/encrypted_rc4_40.pdf"])
def test_encrypted_pdf_needs_user_password(rel):
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(f(rel))
    with pikepdf.open(f(rel), password=USER) as pdf:
        assert pdf.is_encrypted
    with pikepdf.open(f(rel), password=OWNER) as pdf:
        assert pdf.is_encrypted


def test_owner_only_pdf_opens_without_password_but_restricts():
    with pikepdf.open(f("pdf/encrypted_owner_only.pdf")) as pdf:
        assert pdf.is_encrypted
        assert not pdf.allow.print_highres
        assert not pdf.allow.extract


def test_pdfa_declares_part_2b_and_output_intent():
    with pikepdf.open(f("pdf/pdfa_2b.pdf")) as pdf:
        meta = pdf.open_metadata()
        assert meta.get("pdfaid:part") == "2" and meta.get("pdfaid:conformance") == "B"
        assert len(pdf.Root.OutputIntents) == 1


def test_truncated_pdf_cannot_be_opened():
    with pytest.raises(pikepdf.PdfError):
        pikepdf.open(f("pdf/broken_truncated.pdf"))


def test_bad_xref_pdf_is_repairable():
    with pikepdf.open(f("pdf/broken_xref.pdf")) as pdf:
        assert len(pdf.pages) == 3


# --- images


@pytest.mark.parametrize(
    "rel,fmt,frames",
    [
        ("image/photo_exif.jpg", "JPEG", 1),
        ("image/photo_cmyk.jpg", "JPEG", 1),
        ("image/graphic_rgba.png", "PNG", 1),
        ("image/scan_page.tiff", "TIFF", 1),
        ("image/scan_multipage_g4.tiff", "TIFF", 3),
        ("image/photo.webp", "WEBP", 1),
    ],
)
def test_image_format_and_frames(rel, fmt, frames):
    with Image.open(f(rel)) as im:
        assert im.format == fmt
        assert getattr(im, "n_frames", 1) == frames
        im.load()


def test_jpeg_has_gps_and_orientation():
    with Image.open(f("image/photo_exif.jpg")) as im:
        exif = im.getexif()
        assert exif[0x0112] == 6
        assert exif.get_ifd(0x8825)[1] == "N"


def test_cmyk_and_rgba_modes():
    assert Image.open(f("image/photo_cmyk.jpg")).mode == "CMYK"
    assert Image.open(f("image/graphic_rgba.png")).mode == "RGBA"


def test_g4_tiff_is_bilevel_group4():
    with Image.open(f("image/scan_multipage_g4.tiff")) as im:
        assert im.mode == "1" and im.info["compression"] == "group4"


def test_heic_opens():
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    with Image.open(f("image/photo.heic")) as im:
        assert im.format == "HEIF" and im.size == (320, 240)


def test_huge_dimension_png_trips_bomb_guard():
    with pytest.raises(Image.DecompressionBombError):
        Image.open(f("odd/png_huge_dimensions.png"))


def test_truncated_jpeg_fails_to_decode():
    with Image.open(f("odd/truncated.jpg")) as im, pytest.raises(OSError):
        im.load()


# --- office


def test_docx_xlsx_pptx_open():
    docx = pytest.importorskip("docx")
    openpyxl = pytest.importorskip("openpyxl")
    pptx = pytest.importorskip("pptx")
    assert len(docx.Document(f("office/report.docx")).tables) == 1
    assert len(docx.Document(f("office/report_20p.docx")).paragraphs) > 90
    assert openpyxl.load_workbook(f("office/budget.xlsx")).sheetnames == ["Budget", "Notes"]
    assert len(pptx.Presentation(f("office/slides.pptx")).slides) == 3


def test_xls_is_ole2():
    assert f("office/legacy.xls").read_bytes()[:8] == bytes.fromhex("d0cf11e0a1b11ae1")


def test_odt_mimetype_is_first_and_stored():
    with zipfile.ZipFile(f("office/letter.odt")) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/vnd.oasis.opendocument.text"
        ElementTree.fromstring(zf.read("content.xml"))


# --- ebooks


def test_epub_opens():
    epub = pytest.importorskip("ebooklib.epub")
    book = epub.read_epub(str(f("ebook/book.epub")))
    assert book.get_metadata("DC", "title")[0][0] == "FileX Sample Book"


def test_fb2_is_wellformed():
    root = ElementTree.parse(f("ebook/book.fb2")).getroot()
    ns = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0"}
    assert len(root.findall("fb:body/fb:section", ns)) == 3


@pytest.mark.parametrize("rel", ["ebook/book.mobi", "ebook/book.azw3"])
def test_mobi_family_has_palmdb_header(rel):
    assert f(rel).read_bytes()[60:68] == b"BOOKMOBI"


def test_cbz_has_five_png_pages():
    with zipfile.ZipFile(f("ebook/comic.cbz")) as zf:
        assert len([n for n in zf.namelist() if n.endswith(".png")]) == 5


def test_djvu_matches_pinned_hash():
    import hashlib

    data = f("ebook/un_resolution_1837.djvu").read_bytes()
    assert data[:8] == b"AT&TFORM" and data[12:16] == b"DJVM"
    assert hashlib.sha256(data).hexdigest() == make_corpus.DJVU_SHA256


# --- odd files


def test_mislabeled_files_have_true_magic():
    assert f("odd/zip_named.pdf").read_bytes()[:4] == b"PK\x03\x04"
    assert f("odd/pdf_named.docx").read_bytes()[:5] == b"%PDF-"
    assert not f("odd/text_named.pdf").read_bytes().startswith(b"%PDF")
    assert f("odd/empty.pdf").stat().st_size == 0


def test_zip_traversal_entry_present():
    with zipfile.ZipFile(f("odd/zip_traversal.cbz")) as zf:
        assert "../outside.txt" in zf.namelist()


# --- large-file generator (tiny sizes, so this stays fast)


def test_padded_pdf_generator_makes_valid_pdf(tmp_path):
    out = tmp_path / "padded.pdf"
    make_corpus.make_huge_1gb(out, size_mb=2)
    assert out.stat().st_size > 2 * 1024 * 1024
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1
