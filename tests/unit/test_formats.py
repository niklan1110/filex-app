"""Format table and content sniffing (magic bytes, never the extension for binary formats)."""

import struct
from pathlib import Path

import pytest

from filex.formats import FORMATS, sniff

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def ole_with_stream(tmp_path: Path, stream: str) -> Path:
    """legacy.xls with its 'Workbook' directory entry renamed, e.g. to 'WordDocument'.

    The corpus has no .doc/.ppt (they need LibreOffice to generate), and the sniffer only
    reads stream names from the compound-file directory, so a renamed entry is enough.
    """
    data = bytearray((CORPUS / "office/legacy.xls").read_bytes())
    at = data.find("Workbook".encode("utf-16-le") + b"\0\0")
    assert at > 0 and len(stream) < 32
    data[at : at + 64] = stream.encode("utf-16-le").ljust(64, b"\0")
    data[at + 64 : at + 66] = struct.pack("<H", (len(stream) + 1) * 2)
    out = tmp_path / "renamed.bin"
    out.write_bytes(bytes(data))
    return out


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("pdf/text_1p.pdf", "pdf"),
        ("pdf/encrypted_aes256.pdf", "pdf"),
        ("image/graphic_rgba.png", "png"),
        ("image/photo_exif.jpg", "jpg"),
        ("image/photo_cmyk.jpg", "jpg"),
        ("image/scan_page.tiff", "tiff"),
        ("image/scan_multipage_g4.tiff", "tiff"),
        ("image/photo.heic", "heic"),
        ("image/photo.webp", "webp"),
        ("office/report.docx", "docx"),
        ("office/budget.xlsx", "xlsx"),
        ("office/slides.pptx", "pptx"),
        ("office/letter.odt", "odt"),
        ("office/legacy.xls", "xls"),
        ("ebook/book.epub", "epub"),
        ("ebook/book.mobi", "mobi"),
        ("ebook/book.azw3", "azw3"),
        ("ebook/un_resolution_1837.djvu", "djvu"),
    ],
)
def test_sniff_corpus_file(rel, expected):
    assert sniff(CORPUS / rel) == expected


@pytest.mark.parametrize(
    "stream,expected",
    [
        ("WordDocument", "doc"),
        ("PowerPoint Document", "ppt"),
        ("SomethingElse", None),
    ],
)
def test_sniff_ole2_by_directory_stream(tmp_path, stream, expected):
    assert sniff(ole_with_stream(tmp_path, stream)) == expected


def test_zip_named_pdf_is_not_pdf():
    assert sniff(CORPUS / "odd/zip_named.pdf", "zip_named.pdf") == "zip"


def test_zip_storing_a_pdf_is_not_pdf(tmp_path):
    import zipfile

    p = tmp_path / "polyglot.pdf"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.pdf", (CORPUS / "pdf/text_1p.pdf").read_bytes())
    assert sniff(p, "polyglot.pdf") == "zip"


def test_pdf_named_docx_is_pdf():
    assert sniff(CORPUS / "odd/pdf_named.docx", "pdf_named.docx") == "pdf"


def test_text_named_pdf_is_plain_text():
    assert sniff(CORPUS / "odd/text_named.pdf", "text_named.pdf") == "txt"


def test_empty_file_is_unknown():
    assert sniff(CORPUS / "odd/empty.pdf", "empty.pdf") is None


def test_truncated_jpeg_still_sniffs_as_jpeg():
    # Sniffing names the format; a decode failure is the tool's INVALID_* error, not ours.
    assert sniff(CORPUS / "odd/truncated.jpg") == "jpg"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("notes.md", "md"),
        ("notes.markdown", "md"),
        ("table.csv", "csv"),
        ("page.html", "html"),
        ("book.fb2", "txt"),  # FictionBook is recognised by its root element, not the name
        ("readme.txt", "txt"),
        ("noext", "txt"),
    ],
)
def test_text_formats_use_extension_only_for_text_content(tmp_path, name, expected):
    p = tmp_path / name
    p.write_text("# Heading\n\nSome text, 1, 2\n", encoding="utf-8")
    assert sniff(p, name) == expected


FB2 = """<?xml version="1.0" encoding="windows-1251"?>
<!-- generated -->
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body/></FictionBook>
"""


@pytest.mark.parametrize("name", ["book.fb2", "book.xml", "book.txt", "noext"])
@pytest.mark.parametrize("encoding", ["utf-8", "cp1251", "utf-16"])
def test_fictionbook_root_makes_text_fb2_whatever_the_name(tmp_path, name, encoding):
    p = tmp_path / name
    p.write_bytes(FB2.replace("<body/>", "<body>Привет</body>").encode(encoding))
    assert sniff(p, name) == "fb2"


def test_corpus_fb2_is_fb2():
    assert sniff(CORPUS / "ebook/book.fb2", "book.fb2") == "fb2"


def test_other_xml_named_fb2_is_text(tmp_path):
    p = tmp_path / "book.fb2"
    p.write_text('<?xml version="1.0"?><html><p>FictionBook</p></html>', encoding="utf-8")
    assert sniff(p, "book.fb2") == "txt"


def test_binary_content_with_text_extension_is_unknown(tmp_path):
    p = tmp_path / "notes.md"
    p.write_bytes(b"\x00\x01\x02binary\xff\xfe")
    assert sniff(p, "notes.md") is None


def test_zip_named_cbz_is_cbz_and_extension_cannot_make_a_zip_a_docx(tmp_path):
    zipped = (CORPUS / "odd/zip_named.pdf").read_bytes()
    (tmp_path / "a.cbz").write_bytes(zipped)
    (tmp_path / "a.docx").write_bytes(zipped)
    assert sniff(tmp_path / "a.cbz", "a.cbz") == "cbz"
    assert sniff(tmp_path / "a.docx", "a.docx") == "zip"
    assert sniff(CORPUS / "ebook/comic.cbz", "comic.cbz") == "cbz"


def test_rar_is_cbr(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\0" * 32)
    assert sniff(p) == "cbr"


def test_pkcs12_is_p12(tmp_path):
    p = tmp_path / "cert.bin"
    p.write_bytes(bytes.fromhex("308209f9020103") + b"\0" * 64)
    assert sniff(p) == "p12"


def test_avif_is_not_heic(tmp_path):
    p = tmp_path / "x.avif"
    p.write_bytes(b"\0\0\0\x1cftypavif\0\0\0\0avifmif1miaf" + b"\0" * 32)
    assert sniff(p) is None


def test_garbage_ole2_header_does_not_crash(tmp_path):
    p = tmp_path / "bad.doc"
    p.write_bytes(OLE_MAGIC + b"\xff" * 600)
    assert sniff(p) is None


def test_every_format_has_extension_and_mime():
    for fid, fmt in FORMATS.items():
        assert fmt.id == fid
        assert fmt.extensions and all(e.startswith(".") for e in fmt.extensions)
        assert "/" in fmt.mime


def test_extensions_are_unique():
    exts = [e for fmt in FORMATS.values() for e in fmt.extensions]
    assert len(exts) == len(set(exts))


def test_ooxml_and_odf_named_cbz_keep_their_format(tmp_path):
    for rel, expected in (("office/letter.odt", "odt"), ("office/report.docx", "docx")):
        p = tmp_path / "comic.cbz"
        p.write_bytes((CORPUS / rel).read_bytes())
        assert sniff(p, "comic.cbz") == expected


# --- served download type (architecture: HTML, SVG, XML as application/octet-stream)


def test_markup_formats_are_served_as_octet_stream():
    served_raw = {
        fid for fid, f in FORMATS.items() if f.download_mime == "application/octet-stream"
    }
    assert served_raw == {"html", "fb2"}
    assert FORMATS["html"].mime == "text/html"  # the real type stays for the catalog
    for fid, f in FORMATS.items():
        if fid not in served_raw:
            assert f.download_mime == f.mime


def test_any_xml_or_svg_mime_is_served_as_octet_stream():
    from filex.formats import download_mime

    for mime in ("image/svg+xml", "application/xhtml+xml", "text/xml", "application/xml"):
        assert download_mime(mime) == "application/octet-stream"
    assert download_mime("application/pdf") == "application/pdf"


# --- text that is not UTF-8 (owner decision on review M2)


@pytest.mark.parametrize(
    "encoding,bom",
    [
        ("utf-16-le", b"\xff\xfe"),
        ("utf-16-be", b"\xfe\xff"),
        ("utf-32-le", b"\xff\xfe\0\0"),
        ("utf-32-be", b"\0\0\xfe\xff"),
        ("utf-8", b"\xef\xbb\xbf"),
    ],
)
def test_text_with_a_bom_is_text(tmp_path, encoding, bom):
    p = tmp_path / "table.csv"
    p.write_bytes(bom + "Имя;Сумма\r\nÄnne;1\r\n".encode(encoding))
    assert sniff(p, "table.csv") == "csv"
    assert sniff(p) == "txt"


@pytest.mark.parametrize("encoding", ["cp1251", "latin-1", "cp1252"])
def test_8bit_text_is_text(tmp_path, encoding):
    text = "Имя;Сумма\r\nИван;1\r\n" if encoding == "cp1251" else "Name;Città;Größe\r\n"
    p = tmp_path / "table.csv"
    p.write_bytes(text.encode(encoding))
    assert sniff(p, "table.csv") == "csv"


def test_utf16_with_a_bom_named_md_is_markdown(tmp_path):
    p = tmp_path / "notes.md"
    p.write_bytes("# Заголовок\n".encode("utf-16"))
    assert sniff(p, "notes.md") == "md"


@pytest.mark.parametrize("data", [b"abc\x01def", b"abc\x7fdef\xff", b"\xff\xfe\x01\x00\x02\x00"])
def test_control_bytes_are_not_text(tmp_path, data):
    p = tmp_path / "notes.txt"
    p.write_bytes(data)
    assert sniff(p, "notes.txt") is None


def test_text_cut_by_the_probe_limit_is_still_text(tmp_path):
    p = tmp_path / "big.csv"
    p.write_bytes(b"a" * 65535 + "é".encode() + b"tail")  # 2-byte char split at 64 KiB
    assert sniff(p, "big.csv") == "csv"


def test_control_byte_in_the_probe_tail_is_not_text(tmp_path):
    # Review L1: the tail of the probe got a free pass for any invalid byte.
    p = tmp_path / "big.csv"
    p.write_bytes(b"a" * 65533 + b"\x01\xff\xfe")
    assert sniff(p, "big.csv") is None


def test_utf16_surrogate_pair_cut_by_the_probe_limit_is_still_text(tmp_path):
    p = tmp_path / "big.txt"
    # BOM (2 bytes) + 65532 bytes of "a", then the high surrogate of an emoji ends the probe.
    p.write_bytes(b"\xff\xfe" + ("a" * 32766 + "\U0001f600 tail").encode("utf-16-le"))
    assert sniff(p, "big.txt") == "txt"
