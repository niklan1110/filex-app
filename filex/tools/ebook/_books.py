"""Checks shared by the ebook tools before an engine opens a book: the archive pre-check
(review H3), a DOCX's links to outside files (:mod:`filex.tools._ooxml`, task 64), FictionBook's
root element through ``defusedxml``, a PDF's page count, and DRM detection (``DRM_PROTECTED``;
FileX never removes DRM).
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

from filex.registry import StepContext, StepError
from filex.tools import _archive, _ooxml
from filex.tools._pdf import open_pdfium
from filex.tools._xml import parse_xml, xml_root_tag

# EPUB font obfuscation is not DRM: engines read such books (IDPF and Adobe algorithms).
FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
MAX_ENCRYPTION_XML = 1 << 20


def check_input(ctx: StepContext, src: Path, fmt: str, what: str) -> int:
    """Every check before Calibre opens ``src``; returns the page count of a PDF, else 0."""
    if fmt == "epub":
        _archive.check(src, what)
        epub_drm(src, what)
    elif fmt == "docx":  # embedded workbooks and OLE objects are packages
        _archive.check(src, what, allow_nested=True)
        _ooxml.check_links(src)  # Calibre embeds external file: images (task 64)
    elif fmt in ("mobi", "azw3"):
        mobi_drm(src, what)
    elif fmt == "fb2":
        if not xml_root_tag(src, what).endswith("FictionBook"):
            raise StepError("INVALID_INPUT", f"{what} is not a FictionBook file.")
    elif fmt == "pdf":
        pdf = open_pdfium(ctx, src, what)
        try:
            return len(pdf)
        finally:
            pdf.close()
    return 0


def drm_error(what: str) -> StepError:
    return StepError(
        "DRM_PROTECTED",
        f"{what} is protected by DRM. FileX does not remove DRM; use a DRM-free copy.",
    )


def epub_drm(src: Path, what: str) -> None:
    """Refuse an EPUB with DRM (call after the archive check)."""
    with zipfile.ZipFile(src) as zf:
        names = {i.filename for i in zf.infolist()}
        if names & {"META-INF/rights.xml", "META-INF/sinf.xml"}:
            raise drm_error(what)
        if "META-INF/encryption.xml" not in names:
            return
        info = zf.getinfo("META-INF/encryption.xml")
        if info.file_size > MAX_ENCRYPTION_XML:
            raise StepError("INVALID_INPUT", f"{what} has an oversized encryption.xml.")
        root = parse_xml(zf.read(info), what)
    algorithms = {
        el.get("Algorithm", "") for el in root.iter() if el.tag.endswith("}EncryptionMethod")
    }
    if algorithms - FONT_OBFUSCATION:
        raise drm_error(what)


def mobi_drm(src: Path, what: str) -> None:
    """Refuse a MOBI/AZW3 whose record 0 names an encryption type (offset 12, 0 = none)."""
    with src.open("rb") as fh:
        head = fh.read(4096)
    try:
        rec0 = struct.unpack(">I", head[78:82])[0]
        encryption = struct.unpack(">H", head[rec0 + 12 : rec0 + 14])[0]
    except struct.error:
        raise StepError("INVALID_INPUT", f"{what} is damaged or not a MOBI file.") from None
    if encryption:
        raise drm_error(what)
