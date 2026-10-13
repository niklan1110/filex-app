"""Format ids, extensions, MIME types and content sniffing.

Binary formats are identified by their bytes only; the file name never decides. The name is
used in two narrow cases where bytes cannot tell formats apart:

- text formats (txt, md, csv, html, fb2, json): the content must be text, then the extension
  picks the format; text with any other extension is ``txt``. Text is UTF-16/32 with a BOM,
  UTF-8, or 8-bit text (cp1251, latin-1, ...); NUL or another C0 control byte (other than tab,
  line breaks, form feed, SUB and ESC) means binary;
- plain zip archives: ``.cbz`` makes a zip a ``cbz``. OOXML, ODF and EPUB are recognised from
  their contents, so a zip named ``.docx`` stays ``zip``.

``sniff`` never raises on content: anything it cannot classify is ``None``. A zip with more
than ``MAX_ARCHIVE_ENTRIES`` entries is ``None`` too; the count comes from the central
directory before ``zipfile`` builds one object per entry. Upload validation (task 6) reuses the
constant and ``zip_entry_count`` to tell the user why.

Stdlib only: the API image has no libmagic and no engines (architecture section 3).
"""

from __future__ import annotations

import os
import re
import struct
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_ARCHIVE_ENTRIES = int(os.environ.get("FILEX_MAX_ARCHIVE_ENTRIES", "5000"))  # spec 5.1


@dataclass(frozen=True)
class Format:
    id: str
    title: str
    extensions: tuple[str, ...]  # first one is used for stored and output names
    mime: str  # the real type: catalog, file-picker ``accept``
    download_mime: str  # Content-Type for downloads (see download_mime)
    base: str | None = None  # "text" or "zip": bytes alone cannot pick this format


def download_mime(mime: str) -> str:
    """HTML, SVG and any XML are served as bytes, so a download never renders in our origin."""
    if mime == "text/html" or mime.endswith(("/xml", "+xml")):
        return "application/octet-stream"
    return mime


def _f(id: str, title: str, exts: str, mime: str, base: str | None = None) -> Format:
    return Format(id, title, tuple(f".{e}" for e in exts.split()), mime, download_mime(mime), base)


_OOXML = "application/vnd.openxmlformats-officedocument"
_ODF = "application/vnd.oasis.opendocument"

FORMATS: dict[str, Format] = {
    f.id: f
    for f in [
        _f("pdf", "PDF", "pdf", "application/pdf"),
        # images
        _f("jpg", "JPEG image", "jpg jpeg", "image/jpeg"),
        _f("png", "PNG image", "png", "image/png"),
        _f("webp", "WebP image", "webp", "image/webp"),
        _f("tiff", "TIFF image", "tiff tif", "image/tiff"),
        _f("heic", "HEIC image", "heic heif", "image/heic"),
        _f("gif", "GIF image", "gif", "image/gif"),
        _f("bmp", "BMP image", "bmp", "image/bmp"),
        # office
        _f("docx", "Word document", "docx", f"{_OOXML}.wordprocessingml.document"),
        _f("doc", "Word 97-2003 document", "doc", "application/msword"),
        _f("odt", "OpenDocument text", "odt", f"{_ODF}.text"),
        _f("rtf", "Rich Text Format", "rtf", "application/rtf"),
        _f("xlsx", "Excel workbook", "xlsx", f"{_OOXML}.spreadsheetml.sheet"),
        _f("xls", "Excel 97-2003 workbook", "xls", "application/vnd.ms-excel"),
        _f("ods", "OpenDocument spreadsheet", "ods", f"{_ODF}.spreadsheet"),
        _f("pptx", "PowerPoint presentation", "pptx", f"{_OOXML}.presentationml.presentation"),
        _f("ppt", "PowerPoint 97-2003 presentation", "ppt", "application/vnd.ms-powerpoint"),
        _f("odp", "OpenDocument presentation", "odp", f"{_ODF}.presentation"),
        # ebooks and comics
        _f("epub", "EPUB ebook", "epub", "application/epub+zip"),
        _f("mobi", "MOBI ebook", "mobi", "application/x-mobipocket-ebook"),
        _f("azw3", "Kindle AZW3 ebook", "azw3", "application/vnd.amazon.ebook"),
        _f("fb2", "FictionBook", "fb2", "application/x-fictionbook+xml", "text"),
        _f("djvu", "DjVu document", "djvu djv", "image/vnd.djvu"),
        _f("cbz", "Comic book (zip)", "cbz", "application/vnd.comicbook+zip", "zip"),
        _f("cbr", "Comic book (rar)", "cbr", "application/vnd.comicbook-rar"),
        # text
        _f("txt", "Plain text", "txt", "text/plain", "text"),
        _f("md", "Markdown", "md markdown", "text/markdown", "text"),
        _f("csv", "CSV", "csv", "text/csv", "text"),
        _f("html", "HTML", "html htm", "text/html", "text"),
        _f("json", "JSON", "json", "application/json", "text"),
        # other
        _f("zip", "Zip archive", "zip", "application/zip"),
        _f("p12", "PKCS#12 certificate", "p12 pfx", "application/x-pkcs12"),
    ]
}

_BY_EXT = {ext: f for f in FORMATS.values() for ext in f.extensions}

_PROBE = 65536
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx"}
_ODF_MIMES = {
    f"{_ODF}.text": "odt",
    f"{_ODF}.spreadsheet": "ods",
    f"{_ODF}.presentation": "odp",
    "application/epub+zip": "epub",
}
_OOXML_MAIN = {
    "word/document.xml": "docx",
    "xl/workbook.xml": "xlsx",
    "ppt/presentation.xml": "pptx",
}
_OLE_STREAMS = {
    "WordDocument": "doc",
    "Workbook": "xls",
    "Book": "xls",
    "PowerPoint Document": "ppt",
}


def sniff(path: Path | str | BinaryIO, filename: str | None = None) -> str | None:
    """Return the format id of the file's content, or None if it is not a known format.
    ``path`` may also be an open binary file positioned at its start (the caller closes it)."""
    ext = _BY_EXT.get(Path(filename).suffix.lower()) if filename else None
    with nullcontext(path) if hasattr(path, "read") else Path(path).open("rb") as fh:
        head = fh.read(_PROBE)
        fid = _magic(head, fh)
        if fid == "zip":
            fid = _zip_kind(fh)
            return ext.id if fid == "zip" and ext and ext.base == "zip" else fid
    if fid is None and (text := _text(head)) is not None:
        if _FB2_ROOT.match(text):
            return "fb2"  # by content; an .fb2 name alone does not make text FictionBook
        return ext.id if ext and ext.base == "text" and ext.id != "fb2" else "txt"
    return fid


def _magic(head: bytes, fh: BinaryIO) -> str | None:
    if head.startswith(b"%PDF-"):  # strict offset 0: no polyglots (zip/HTML wrapping a PDF)
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+"):
        return "tiff"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:2] == b"BM" and head[6:10] == b"\0\0\0\0" and len(head) > 26:
        return "bmp"
    if head[4:8] == b"ftyp":
        return _ftyp(head)
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"Rar!\x1a\x07"):
        return "cbr"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head[:8] == b"AT&TFORM" and head[12:16] in (b"DJVU", b"DJVM"):
        return "djvu"
    if head[60:68] == b"BOOKMOBI":
        return _mobi_kind(head)
    if head.startswith(_OLE_MAGIC):
        streams = _ole_streams(fh)
        return next((fid for name, fid in _OLE_STREAMS.items() if name in streams), None)
    if _is_pkcs12(head):
        return "p12"
    return None


def _ftyp(head: bytes) -> str | None:
    size = struct.unpack(">I", head[:4])[0]
    box = head[8 : min(size, len(head))]
    brands = {box[0:4]} | {box[i : i + 4] for i in range(8, len(box) - 3, 4)}
    return "heic" if brands & _HEIC_BRANDS else None


def _mobi_kind(head: bytes) -> str | None:
    # PalmDB header (78 bytes), then the record list; record 0 holds the MOBI header, whose
    # file version is 8 for KF8 (AZW3) and 6 or lower for MOBI.
    if len(head) < 82:
        return None
    rec0 = struct.unpack(">I", head[78:82])[0]
    if rec0 + 40 > len(head) or head[rec0 + 16 : rec0 + 20] != b"MOBI":
        return None
    version = struct.unpack(">I", head[rec0 + 36 : rec0 + 40])[0]
    return "azw3" if version == 8 else "mobi"


def _is_pkcs12(head: bytes) -> bool:
    # PFX ::= SEQUENCE { version INTEGER (3), ... }, DER-encoded.
    if len(head) < 8 or head[0] != 0x30:
        return False
    n = head[1]
    i = 2 + (n & 0x7F if n & 0x80 else 0)
    return head[i : i + 3] == b"\x02\x01\x03"


def zip_entry_count(fh: BinaryIO, limit: int) -> int | None:
    """Entries in a zip's central directory, counted up to ``limit + 1``; None if there is none.

    Reads the end record (ZIP64 too) and walks the directory headers without building ZipInfo
    objects. zipfile walks the directory by its byte size and ignores the declared count, so
    the headers are counted too and the larger number wins. May raise on a truncated file.
    """
    size = fh.seek(0, os.SEEK_END)
    tail_at = max(0, size - 22 - 0xFFFF)  # end record plus the longest comment
    fh.seek(tail_at)
    tail = fh.read()
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        return None
    record_at = tail_at + at
    declared, cd_size = struct.unpack("<HI", tail[at + 10 : at + 16])
    if record_at >= 76:
        fh.seek(record_at - 76)
        z64 = fh.read(76)  # ZIP64 end record (56 bytes), then its locator (20)
        if z64[:4] == b"PK\x06\x06" and z64[56:60] == b"PK\x06\x07":
            declared, cd_size = struct.unpack("<QQ", z64[32:48])
            record_at -= 76
    pos = record_at - cd_size
    if pos < 0:
        return None
    count = 0
    while pos + 46 <= record_at and count <= limit:
        fh.seek(pos)
        hdr = fh.read(46)
        if hdr[:4] != b"PK\x01\x02":
            break
        name, extra, comment = struct.unpack("<HHH", hdr[28:34])
        pos += 46 + name + extra + comment
        count += 1
    return max(count, declared)


def _zip_kind(fh: BinaryIO) -> str | None:
    try:
        count = zip_entry_count(fh, MAX_ARCHIVE_ENTRIES)
        if count is None or count > MAX_ARCHIVE_ENTRIES:
            return None
        with zipfile.ZipFile(fh) as zf:
            infos = zf.infolist()
            first = infos[0] if infos else None
            # ODF and EPUB store ``mimetype`` first, uncompressed and unencrypted; anything
            # else is not ODF, and no decompressor ever runs on untrusted data here.
            if (
                first
                and first.filename == "mimetype"
                and first.compress_type == zipfile.ZIP_STORED
                and not first.flag_bits & 1
            ):
                with zf.open(first) as m:
                    kind = _ODF_MIMES.get(m.read(100).decode("ascii", "replace").strip())
                if kind:
                    return kind
            names = {i.filename for i in infos}
            if "[Content_Types].xml" in names:
                for main, kind in _OOXML_MAIN.items():
                    if main in names:
                        return kind
            return "zip"
    except Exception:  # noqa: BLE001 - a sniffer never raises on content: unreadable is unknown
        return None


def _ole_streams(fh: BinaryIO, max_sectors: int = 4096) -> set[str]:
    """Stream names in an OLE2 compound file's directory. Bounded; never raises on junk."""
    try:
        fh.seek(0)
        hdr = fh.read(512)
        shift = struct.unpack("<H", hdr[30:32])[0]
        if shift not in (9, 12):
            return set()
        ssz = 1 << shift
        per_sector = ssz // 4
        first_dir = struct.unpack("<I", hdr[48:52])[0]
        difat = list(struct.unpack("<109I", hdr[76:512]))
        next_difat, n_difat = struct.unpack("<II", hdr[68:76])
        for _ in range(min(n_difat, max_sectors)):
            if next_difat >= 0xFFFFFFFA:
                break
            ids = struct.unpack(f"<{per_sector}I", _sector(fh, ssz, next_difat))
            difat.extend(ids[:-1])
            next_difat = ids[-1]

        def next_sector(sid: int) -> int:
            fat_sid = difat[sid // per_sector]
            entry = _sector(fh, ssz, fat_sid)[(sid % per_sector) * 4 :][:4]
            return struct.unpack("<I", entry)[0]

        names: set[str] = set()
        sid, seen = first_dir, 0
        while sid < 0xFFFFFFFA and seen < max_sectors:
            data = _sector(fh, ssz, sid)
            for off in range(0, ssz, 128):
                entry = data[off : off + 128]
                name_len, kind = struct.unpack("<HB", entry[64:67])
                if kind == 2 and 2 <= name_len <= 64:
                    names.add(entry[: name_len - 2].decode("utf-16-le", "replace"))
            sid, seen = next_sector(sid), seen + 1
        return names
    except Exception:  # noqa: BLE001 - junk sector ids, short reads: unknown, never an error
        return set()


def _sector(fh: BinaryIO, ssz: int, sid: int) -> bytes:
    fh.seek((sid + 1) * ssz)
    data = fh.read(ssz)
    if len(data) != ssz:
        raise IndexError("sector past end of file")
    return data


_BOMS = (  # UTF-32 first: its little-endian BOM starts with UTF-16's
    (b"\xff\xfe\0\0", "utf-32-le", 4),
    (b"\0\0\xfe\xff", "utf-32-be", 4),
    (b"\xff\xfe", "utf-16-le", 2),
    (b"\xfe\xff", "utf-16-be", 2),
)
_BINARY_CHARS = {chr(c) for c in (*range(32), 127)} - set("\t\n\r\f\x1a\x1b")


# FictionBook: the root element after an optional BOM, XML declaration, comments and DOCTYPE.
_FB2_ROOT = re.compile(
    r"\ufeff?\s*(<\?xml[^>]*\?>\s*)?(<!--.*?-->\s*|<!DOCTYPE[^>]*>\s*)*"
    r"<([\w.-]+:)?FictionBook[\s>/]",
    re.DOTALL,
)


def _text(data: bytes) -> str | None:
    """``data`` decoded if it is text: UTF-16/32 with a BOM, else UTF-8, else 8-bit text (as
    latin-1); with no binary control characters. Else None."""
    for bom, codec, unit in _BOMS:
        if data.startswith(bom):
            body = data[len(bom) :]
            body = body[: len(body) - len(body) % unit]
            for cut in (0, 2):  # the probe limit may split a UTF-16 surrogate pair
                try:
                    text = body[: len(body) - cut].decode(codec)
                    break
                except UnicodeDecodeError:
                    pass
            else:
                return None
            break
    else:
        if not data or b"\0" in data:
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")  # 8-bit text; a UTF-8 char cut by the probe too
    return None if _BINARY_CHARS.intersection(text) else text
