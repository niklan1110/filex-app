"""Samples and markers for the ebook tools' tests. Real conversions need the engines of the
worker-ebook image (``needs_calibre``, ``needs_pandoc``, ``needs_djvu``)."""

import shutil
import struct
import zipfile
from pathlib import Path

import pytest
from conftest import CORPUS

EBOOK = CORPUS / "ebook"
POSIX = hasattr(__import__("os"), "killpg")

needs_calibre = pytest.mark.skipif(
    not (POSIX and shutil.which("ebook-convert")), reason="needs Calibre (worker-ebook)"
)
needs_pandoc = pytest.mark.skipif(
    not (POSIX and shutil.which("pandoc")), reason="needs Pandoc (worker-ebook)"
)
needs_djvu = pytest.mark.skipif(
    not (POSIX and shutil.which("ddjvu") and shutil.which("djvused")),
    reason="needs djvulibre (worker-ebook)",
)

CYRILLIC = "Съешь же ещё этих мягких французских булок, да выпей чаю."
FB2_CP1251 = f"""<?xml version="1.0" encoding="windows-1251"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
 <description><title-info><genre>prose</genre>
  <author><first-name>Иван</first-name><last-name>Пример</last-name></author>
  <book-title>Пробная книга</book-title><lang>ru</lang></title-info></description>
 <body><section><title><p>Глава 1</p></title><p>{CYRILLIC}</p></section></body>
</FictionBook>
"""
PASSWD_MARKERS = (b"root:x:0", b"/usr/sbin/nologin", b"daemon:x:")
LFI_HTML = """<html><head><title>LFI</title><link rel="stylesheet" href="/etc/passwd">
<style>@import url(file:///etc/passwd);</style></head>
<body><h1>Chapter</h1><p>Visible text.</p>
<img src="file:///etc/passwd"><img src="/etc/passwd"><img src="../../../../etc/passwd">
<a href="file:///etc/passwd">link</a><iframe src="file:///etc/passwd"></iframe>
<object data="/etc/passwd"></object><img src="http://192.0.2.1/x.png">
</body></html>
"""


def scratch_copy(src: Path, dest_dir: Path, fmt: str) -> Path:
    """Copy ``src`` as the runner names scratch inputs: ``0.<format extension>``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = {"md": ".md", "html": ".html", "txt": ".txt"}.get(fmt, f".{fmt}")
    return Path(shutil.copyfile(src, dest_dir / f"0{ext}"))


def write(dest_dir: Path, fmt: str, data: bytes | str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / f"0.{fmt}"
    p.write_bytes(data.encode() if isinstance(data, str) else data)
    return p


def epub_with(dest: Path, extra: dict[str, bytes]) -> Path:
    """The corpus EPUB plus ``extra`` entries (e.g. META-INF/encryption.xml)."""
    with zipfile.ZipFile(EBOOK / "book.epub") as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info))
        for name, data in extra.items():
            zout.writestr(name, data)
    return dest


def encryption_xml(*algorithms: str) -> bytes:
    methods = "".join(
        f'<enc:EncryptedData><enc:EncryptionMethod Algorithm="{a}"/>'
        f'<enc:CipherData><enc:CipherReference URI="OEBPS/c{i}.xhtml"/></enc:CipherData>'
        "</enc:EncryptedData>"
        for i, a in enumerate(algorithms)
    )
    return (
        '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
        f' xmlns:enc="http://www.w3.org/2001/04/xmlenc#">{methods}</encryption>'
    ).encode()


def mobi_with_encryption(src: Path, dest: Path, kind: int = 2) -> Path:
    """A copy of a MOBI/AZW3 whose PalmDOC header names encryption type ``kind``."""
    data = bytearray(src.read_bytes())
    rec0 = struct.unpack(">I", data[78:82])[0]
    data[rec0 + 12 : rec0 + 14] = struct.pack(">H", kind)
    dest.write_bytes(bytes(data))
    return dest


def leaked(path: Path) -> bool:
    """True if any passwd line shows up in the file, its zip entries or its PDF text."""
    blobs = [path.read_bytes()]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            blobs += [zf.read(n) for n in zf.namelist()]
    if blobs[0].startswith(b"%PDF"):
        blobs.append(pdf_text(path).encode())
    return any(m in b for b in blobs for m in PASSWD_MARKERS)


def pdf_text(path: Path) -> str:
    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()


def book_text(path: Path) -> str:
    """The text in an EPUB/DOCX (all XML entries), PDF, or text file."""
    data = path.read_bytes()
    if data.startswith(b"%PDF"):
        return pdf_text(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            return "\n".join(
                zf.read(n).decode("utf-8", "replace")
                for n in zf.namelist()
                if n.endswith((".xhtml", ".html", ".htm", ".xml"))
            )
    return data.decode("utf-8", "replace")
