"""Local-file-inclusion corpus (plan task 64, review H2 fix 4): hostile but harmless samples.

Every sample only *refers* to files: to another job's input files (``OtherJob``, made by the
test under the same data dir the runner uses) and to the host file ``/etc/hostname``. No exploit,
no payload, no network (the only URL besides ``file:`` is the reserved ``.invalid`` domain).
A sample is built at test time because the references hold the other job's absolute path and
its path relative to the step's scratch dir, which only exist then.

The other job's files carry canary markers (``MARKERS``): ``0.txt`` is also a valid PDF content
stream that draws ``FILEXCANARY``, so a renderer that resolves it puts the word on the page
(text, OCR text layer and pixels); ``1.png`` carries a ``tEXt`` marker; ``2.ods``, ``3.pdf``,
``4.docx`` and ``5.djvu`` hold the marker as document text. ``leaks()`` searches an output
(raw bytes, zip members, PDF streams and text, UTF-16) for the markers and the host's name;
``rasters()`` gives the pixels of an output, which the test compares with a control run whose
references point at missing files.

Stdlib only at import time; ``leaks``/``rasters`` use pikepdf, pypdfium2 and Pillow when the
image has them. ``python tests/integration/lfi_corpus.py <dir>`` writes every sample to look at.
"""

from __future__ import annotations

import importlib.util
import io
import os
import stat
import struct
import sys
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

TESTS = Path(__file__).resolve().parents[1]
CORPUS = TESTS / "corpus"
HOST = "/etc/hostname"
MARKERS = (b"FILEXCANARY", b"FILEX-LFI-CANARY")
CANARY_STREAM = b"BT /F1 48 Tf 40 300 Td (FILEXCANARY) Tj ET\n% FILEX-LFI-CANARY-7f3a9c\n"
CANARY_TEXT = "FILEXCANARY FILEX-LFI-CANARY-7f3a9c"


def _office_samples():
    """tests/tools/office_samples.py by path (it is not a package; its conftest is not ours)."""
    spec = importlib.util.spec_from_file_location(
        "lfi_office_samples", TESTS / "tools" / "office_samples.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


office = _office_samples()


# --------------------------------------------------------------------------- the other job


_CANARY_PIXELS = bytes([0x13, 0x57, 0x9B, 0x2A, 0xC4, 0x71] * 2)  # one 4-pixel RGB row


def _png_idat(pixels: bytes) -> bytes:
    return zlib.compress(b"".join(b"\0" + pixels for _ in range(4)), 9)


PNG_IDAT = _png_idat(_CANARY_PIXELS)


def canary_png(pixels: bytes = _CANARY_PIXELS, text: bytes = b"FILEX-LFI-CANARY-png") -> bytes:
    """A 4x4 PNG with an unusual palette and a ``tEXt`` marker: its IDAT bytes are a marker too."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\0" + text)
        + chunk(b"IDAT", _png_idat(pixels))
        + chunk(b"IEND", b"")
    )


def _pdf_literal(s: str) -> bytes:
    return b"(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode() + b")"


def _write_pdf(objs: list[bytes]) -> bytes:
    """A classic-xref PDF from object bodies (object 1 is the catalog)."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _stream(data: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d %s>>\nstream\n" % (len(data), extra) + data + b"\nendstream"


def canary_pdf() -> bytes:
    """One page that shows the canary (the other job's PDF)."""
    content = CANARY_STREAM + b"BT /F1 12 Tf 40 200 Td (FILEX-LFI-CANARY-pdf) Tj ET\n"
    return _write_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            ),
            _stream(content),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
    )


def _iff(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack(">I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


def _djvu_txta(text: str, width: int, height: int) -> bytes:
    """A ``TXTa`` (uncompressed hidden text) chunk body: the text and one page zone
    (djvulibre ``DjVuTXT::encode``)."""
    t = text.encode()
    zone = bytes([1]) + struct.pack(
        ">HHHHH", 0x8000, 0x8000, 0x8000 + width, 0x8000 + height, 0x8000
    )
    zone += len(t).to_bytes(3, "big") + (0).to_bytes(3, "big")
    return len(t).to_bytes(3, "big") + t + bytes([1]) + zone


def djvu_page(chunks: list[bytes] = (), width: int = 400, height: int = 400) -> bytes:
    """A single-page DjVu: an ``INFO`` chunk (blank white page) plus ``chunks``."""
    info = struct.pack(">HHBB", width, height, 24, 0) + struct.pack("<H", 100) + bytes([22, 1])
    return _form(b"DJVU" + _iff(b"INFO", info) + b"".join(chunks))


def _form(body: bytes) -> bytes:
    return b"AT&TFORM" + struct.pack(">I", len(body)) + body


def canary_djvi() -> bytes:
    """A shared DjVu component (``FORM:DJVI``) holding the canary as hidden text."""
    return _form(b"DJVI" + _iff(b"TXTa", _djvu_txta(CANARY_TEXT, 400, 400)))


def _docx(
    path: Path,
    body_xml: str,
    rels: str = "",
    settings: str = "",
    settings_rels: str = "",
    w_ns: str = "",
) -> Path:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    r = office._OFFDOC
    doc = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}" xmlns:r="{r}" {w_ns}>'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    main_ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    ct = office._CT.format(main="word/document.xml", ctype=main_ct)
    if settings:
        st = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
        ct = ct.replace(
            "</Types>", f'<Override PartName="/word/settings.xml" ContentType="{st}"/></Types>'
        )
        rels += f'<Relationship Id="rIdS" Type="{r}/settings" Target="settings.xml"/>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr(
            "_rels/.rels",
            office._RELS.format(
                f'<Relationship Id="rId1" Type="{r}/officeDocument" Target="word/document.xml"/>'
            ),
        )
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/_rels/document.xml.rels", office._RELS.format(rels))
        if settings:
            zf.writestr(
                "word/settings.xml",
                f'<?xml version="1.0" encoding="UTF-8"?><w:settings xmlns:w="{w}" '
                f'xmlns:r="{r}">{settings}</w:settings>',
            )
            zf.writestr("word/_rels/settings.xml.rels", office._RELS.format(settings_rels))
    return path


def _para(text: str) -> str:
    return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


@dataclass(frozen=True)
class OtherJob:
    """Another job's input files (absolute paths) and the scratch dir a sample is opened from."""

    text: Path
    png: Path
    ods: Path
    pdf: Path
    docx: Path
    djvi: Path
    base: Path  # the step's scratch dir: relative references start here

    @classmethod
    def create(cls, in_dir: Path, base: Path) -> OtherJob:
        in_dir.mkdir(parents=True, exist_ok=True)
        (in_dir / "0.txt").write_bytes(CANARY_STREAM)
        (in_dir / "1.png").write_bytes(canary_png())
        office.plain_ods(in_dir / "2.ods", CANARY_TEXT)
        (in_dir / "3.pdf").write_bytes(canary_pdf())
        _docx(in_dir / "4.docx", _para(CANARY_TEXT))
        (in_dir / "5.djvu").write_bytes(canary_djvi())
        return cls(
            text=in_dir / "0.txt",
            png=in_dir / "1.png",
            ods=in_dir / "2.ods",
            pdf=in_dir / "3.pdf",
            docx=in_dir / "4.docx",
            djvi=in_dir / "5.djvu",
            base=base,
        )

    def rel(self, p: Path) -> str:
        return Path(os.path.relpath(p, self.base)).as_posix()

    def missing(self) -> OtherJob:
        """The control: the same references, to files that do not exist."""
        return replace(
            self,
            **{
                f: getattr(self, f).with_name("missing-" + getattr(self, f).name)
                for f in ("text", "png", "ods", "pdf", "docx", "djvi")
            },
        )


# --------------------------------------------------------------------------- samples


def lfi_pdf(path: Path, o: OtherJob, pages: int = 10) -> Path:
    """Every page draws: external content streams (``/F`` as a path, a relative path and a
    ``/FS /URL`` file spec) naming the other job's text and ``/etc/hostname``; external image
    streams on both; a reference XObject (``/Ref``) importing the other job's PDF page."""
    font, fm1, im9, im8 = 3, 4, 5, 6
    ext = [7, 8, 9, 10]
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages, below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] /Ref << /F "
        + _pdf_literal(str(o.pdf))
        + b" /Page 0 >> /Length 0 >>\nstream\n\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 64 /Height 1 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 0 /F "
        + _pdf_literal(str(o.text))
        + b" >>\nstream\n\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 8 /Height 1 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 0 /F " + _pdf_literal(HOST) + b" >>\nstream\n\nendstream",
        b"<< /Length 0 /F " + _pdf_literal(str(o.text)) + b" >>\nstream\n\nendstream",
        b"<< /Length 0 /F " + _pdf_literal(o.rel(o.text)) + b" >>\nstream\n\nendstream",
        b"<< /Length 0 /F << /Type /Filespec /FS /URL /F "
        + _pdf_literal(o.text.as_uri())
        + b" >> >>\nstream\n\nendstream",
        b"<< /Length 0 /F " + _pdf_literal(HOST) + b" >>\nstream\n\nendstream",
    ]
    draw = len(objs) + 1
    objs.append(
        _stream(
            b"q 256 0 0 16 72 420 cm /Im9 Do Q q 256 0 0 16 72 400 cm /Im8 Do Q "
            b"q 0.5 0 0 0.5 300 0 cm /Fm1 Do Q"
        )
    )
    kids = []
    for n in range(1, pages + 1):
        own = len(objs) + 1
        objs.append(_stream(b"BT /F1 24 Tf 72 700 Td (Visible page %d) Tj ET" % n))
        page = len(objs) + 1
        contents = b" ".join(b"%d 0 R" % i for i in [own, *ext, draw])
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 "
            b"%d 0 R >> /XObject << /Fm1 %d 0 R /Im9 %d 0 R /Im8 %d 0 R >> >> /Contents [%s] >>"
            % (font, fm1, im9, im8, contents)
        )
        kids.append(b"%d 0 R" % page)
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), pages)
    path.write_bytes(_write_pdf(objs))
    return path


def planted_pdf(path: Path, o: OtherJob) -> Path:
    """Positive control: the canary drawn by the document itself (a leak the checks must see)."""
    path.write_bytes(canary_pdf())
    return path


def rtf_include(path: Path, o: OtherJob) -> Path:
    """RTF ``INCLUDETEXT`` / ``INCLUDEPICTURE`` fields (absolute, relative, ``file:`` URL, host)."""

    def field(instr: str) -> str:
        return "{\\field{\\*\\fldinst " + instr + "}{\\fldrslt cached}}\\par\n"

    def q(p: str) -> str:
        return '"' + p.replace("\\", "\\\\\\\\") + '"'

    body = "".join(
        [
            field("INCLUDETEXT " + q(o.text.as_posix())),
            field("INCLUDETEXT " + q(o.rel(o.text))),
            field("INCLUDETEXT " + q(HOST)),
            field("INCLUDEPICTURE " + q(o.png.as_uri()) + " \\\\d"),
            field("INCLUDEPICTURE " + q(o.rel(o.png)) + " \\\\d"),
        ]
    )
    path.write_text(
        "{\\rtf1\\ansi{\\fonttbl{\\f0 Helvetica;}}\\f0 Visible RTF text.\\par\n" + body + "}",
        encoding="ascii",
    )
    return path


def planted_rtf(path: Path, o: OtherJob) -> Path:
    path.write_text(
        "{\\rtf1\\ansi{\\fonttbl{\\f0 Helvetica;}}\\f0 " + CANARY_TEXT + "\\par}", encoding="ascii"
    )
    return path


def linked_odt(path: Path, o: OtherJob) -> Path:
    return office.linked_odt(path, o.text, o.png)


def linked_ods(path: Path, o: OtherJob) -> Path:
    return office.linked_ods(path, o.text, o.ods)


def linked_docx(path: Path, o: OtherJob) -> Path:
    return office.linked_docx(path, o.text, o.png)


def webservice_xlsx(path: Path, o: OtherJob) -> Path:
    return office.webservice_xlsx(path, o.text)


def docx_template_altchunk(path: Path, o: OtherJob) -> Path:
    """DOCX ``attachedTemplate`` (with ``linkStyles``) and an external ``altChunk``, both on the
    other job's DOCX, plus a ``subDocument`` relation."""
    r = office._OFFDOC
    rels = (
        f'<Relationship Id="rIdAlt" Type="{r}/aFChunk" Target={quoteattr(o.docx.as_uri())} '
        'TargetMode="External"/>'
        f'<Relationship Id="rIdSub" Type="{r}/subDocument" Target={quoteattr(o.docx.as_uri())} '
        'TargetMode="External"/>'
    )
    body = (
        _para("Visible DOCX text.")
        + '<w:altChunk r:id="rIdAlt"/>'
        + '<w:p><w:subDoc r:id="rIdSub"/></w:p>'
    )
    settings = '<w:attachedTemplate r:id="rIdT"/><w:linkStyles/><w:updateFields w:val="true"/>'
    settings_rels = (
        f'<Relationship Id="rIdT" Type="{r}/attachedTemplate" '
        f'Target={quoteattr(o.docx.as_uri())} TargetMode="External"/>'
    )
    return _docx(path, body, rels, settings, settings_rels)


def xlsx_external_link(path: Path, o: OtherJob) -> Path:
    """XLSX with an ``externalLink`` part to the other job's ODS, a formula through it and no
    cached values."""
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = office._OFFDOC
    ct_base = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{s}"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Visible XLSX text.</t></is></c></row>'
        '<row r="2"><c r="A2"><f>[1]Sheet1!A1</f></c></row>'
        '<row r="3"><c r="A3"><f>INDIRECT("[1]Sheet1!A1")</f></c></row>'
        "</sheetData></worksheet>"
    )
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{s}" xmlns:r="{r}">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        '<externalReferences><externalReference r:id="rId2"/></externalReferences>'
        '<calcPr fullCalcOnLoad="1"/></workbook>'
    )
    external = (
        f'<?xml version="1.0" encoding="UTF-8"?><externalLink xmlns="{s}" xmlns:r="{r}">'
        '<externalBook r:id="rId1"><sheetNames><sheetName val="Sheet1"/></sheetNames>'
        '<sheetDataSet><sheetData sheetId="0" refreshError="1"/></sheetDataSet></externalBook>'
        "</externalLink>"
    )
    ct = office._CT.format(main="xl/workbook.xml", ctype=f"{ct_base}.sheet.main+xml").replace(
        "</Types>",
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{ct_base}.worksheet+xml"/>'
        '<Override PartName="/xl/externalLinks/externalLink1.xml" '
        f'ContentType="{ct_base}.externalLink+xml"/></Types>',
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr(
            "_rels/.rels",
            office._RELS.format(
                f'<Relationship Id="rId1" Type="{r}/officeDocument" Target="xl/workbook.xml"/>'
            ),
        )
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            office._RELS.format(
                f'<Relationship Id="rId1" Type="{r}/worksheet" Target="worksheets/sheet1.xml"/>'
                f'<Relationship Id="rId2" Type="{r}/externalLink" '
                'Target="externalLinks/externalLink1.xml"/>'
            ),
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        zf.writestr("xl/externalLinks/externalLink1.xml", external)
        zf.writestr(
            "xl/externalLinks/_rels/externalLink1.xml.rels",
            office._RELS.format(
                f'<Relationship Id="rId1" Type="{r}/externalLinkPath" '
                f'Target={quoteattr(o.ods.as_uri())} TargetMode="External"/>'
            ),
        )
    return path


def pptx_linked_media(path: Path, o: OtherJob) -> Path:
    """The corpus ``slides.pptx`` with a linked (not embedded) picture of the other job's PNG and
    a linked video of its text file added to slide 1."""
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = office._OFFDOC

    def pic(n: int, rid: str, extra: str = "") -> str:
        return (
            f'<p:pic xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:nvPicPr>'
            f'<p:cNvPr id="{900 + n}" name="Linked {n}"/><p:cNvPicPr/><p:nvPr>{extra}</p:nvPr>'
            f'</p:nvPicPr><p:blipFill><a:blip r:link="{rid}"/><a:stretch><a:fillRect/>'
            f'</a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="{n * 1000000}" y="0"/>'
            '<a:ext cx="900000" cy="900000"/></a:xfrm><a:prstGeom prst="rect"/></p:spPr></p:pic>'
        )

    with (
        zipfile.ZipFile(CORPUS / "office" / "slides.pptx") as zin,
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "ppt/slides/slide1.xml":
                xml = data.decode()
                video = f'<a:videoFile xmlns:a="{a}" xmlns:r="{r}" r:link="rIdLfiVid"/>'
                xml = xml.replace(
                    "</p:spTree>", pic(1, "rIdLfiImg") + pic(2, "rIdLfiImg2", video) + "</p:spTree>"
                )
                data = xml.encode()
            elif info.filename == "ppt/slides/_rels/slide1.xml.rels":
                links = (
                    f'<Relationship Id="rIdLfiImg" Type="{r}/image" '
                    f'Target={quoteattr(o.png.as_uri())} TargetMode="External"/>'
                    f'<Relationship Id="rIdLfiImg2" Type="{r}/image" '
                    f'Target={quoteattr(o.rel(o.png))} TargetMode="External"/>'
                    f'<Relationship Id="rIdLfiVid" Type="{r}/video" '
                    f'Target={quoteattr(o.text.as_uri())} TargetMode="External"/>'
                )
                data = (
                    data.decode().replace("</Relationships>", links + "</Relationships>").encode()
                )
            zout.writestr(info, data)
    return path


def odp_linked_media(path: Path, o: OtherJob) -> Path:
    """An ODP whose page has linked images (other job's PNG, absolute and relative), a linked
    object frame on its text file and a plugin (media) frame, asking for automatic updates."""
    office._MIMES.setdefault("odp", "application/vnd.oasis.opendocument.presentation")

    def frame(n: int, inner: str) -> str:
        return (
            f'<draw:frame draw:name="F{n}" svg:x="{n}cm" svg:y="2cm" svg:width="4cm" '
            f'svg:height="4cm">{inner}</draw:frame>'
        )

    body = (
        '<office:body><office:presentation><draw:page draw:name="page1">'
        + frame(1, "<draw:text-box><text:p>Visible ODP text.</text:p></draw:text-box>")
        + frame(
            6,
            f'<draw:image xlink:href={quoteattr(o.png.as_uri())} xlink:type="simple" '
            'xlink:show="embed" xlink:actuate="onLoad"/>',
        )
        + frame(
            11,
            f'<draw:image xlink:href={quoteattr(o.rel(o.png))} xlink:type="simple" '
            'xlink:show="embed" xlink:actuate="onLoad"/>',
        )
        + frame(
            16,
            f'<draw:object xlink:href={quoteattr(o.ods.as_uri())} xlink:type="simple" '
            'xlink:show="embed" xlink:actuate="onLoad"/>',
        )
        + frame(
            21,
            f'<draw:plugin xlink:href={quoteattr(o.text.as_uri())} xlink:type="simple" '
            'xlink:show="embed" xlink:actuate="onLoad" draw:mime-type="video/mp4"/>',
        )
        + "</draw:page></office:presentation></office:body>"
    )
    return office._odf(path, "odp", body, office._AUTO_UPDATE)


def csv_formulas(path: Path, o: OtherJob) -> Path:
    """CSV whose cells are formulas: ``WEBSERVICE`` on the other job's file and the host file,
    an external reference into the other job's ODS, ``HYPERLINK`` and ``FILTERXML``."""
    rows = [
        "Visible CSV text",
        f'=WEBSERVICE("{o.text.as_uri()}")',
        f'=WEBSERVICE("{o.rel(o.text)}")',
        '=WEBSERVICE("file:///etc/hostname")',
        f"='{o.ods.as_uri()}'#$Sheet1.A1",
        f"=INDIRECT(\"'{o.ods.as_uri()}'#$Sheet1.A1\")",
        f'=FILTERXML(WEBSERVICE("{o.text.as_uri()}");"//x")',
    ]
    path.write_text("".join('"' + c.replace('"', '""') + '"\n' for c in rows), encoding="utf-8")
    return path


def _html_refs(o: OtherJob, end: str = ">") -> str:
    """``end`` closes void elements: ``" />"`` for XHTML."""
    refs = [o.text.as_uri(), str(o.text), o.rel(o.text), "file:///etc/hostname", HOST]
    imgs = [o.png.as_uri(), str(o.png), o.rel(o.png)]
    return (
        "".join(f'<img src="{escape(i)}" alt="x"{end}' for i in imgs)
        + "".join(
            f'<iframe src="{escape(u)}"></iframe><object data="{escape(u)}"></object>'
            f'<embed src="{escape(u)}"{end}'
            for u in refs
        )
        + f'<div style="background:url({escape(o.png.as_uri())})">bg</div>'
    )


def html_lfi(path: Path, o: OtherJob) -> Path:
    css = "".join(f'<link rel="stylesheet" href="{escape(u)}">' for u in (o.text.as_uri(), HOST))
    path.write_text(
        f"<html><head><title>LFI</title>{css}<style>@import url({o.text.as_uri()});</style></head>"
        f"<body><h1>Chapter</h1><p>Visible HTML text.</p>{_html_refs(o)}"
        '<img src="http://lfi.invalid/x.png"></body></html>',
        encoding="utf-8",
    )
    return path


def md_lfi(path: Path, o: OtherJob) -> Path:
    """Markdown: images and links on the other job's files (absolute, relative, ``file:``), raw
    HTML, and an ``include``-style code block attribute that some filters honour."""
    path.write_text(
        "# Chapter\n\nVisible Markdown text.\n\n"
        f"![a]({o.png.as_posix()}) ![b]({o.rel(o.png)}) ![c]({o.png.as_uri()})\n\n"
        f"![d]({o.text.as_posix()}) ![e](/etc/hostname)\n\n"
        f'```{{include="{o.text.as_posix()}"}}\n```\n\n'
        f"{_html_refs(o)}\n",
        encoding="utf-8",
    )
    return path


def fb2_lfi(path: Path, o: OtherJob) -> Path:
    """FB2 with image ``l:href`` values pointing outside the book (no ``<binary>``)."""
    hrefs = [o.png.as_uri(), str(o.png), o.rel(o.png), o.text.as_uri(), "file:///etc/hostname"]
    images = "".join(f"<image l:href={quoteattr(h)}/>" for h in hrefs)
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<FictionBook '
        'xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info><genre>prose</genre>'
        "<author><first-name>Jane</first-name><last-name>Example</last-name></author>"
        f"<book-title>LFI</book-title><lang>en</lang><coverpage>{images}</coverpage>"
        "</title-info></description><body><section><title><p>Chapter</p></title>"
        f"<p>Visible FB2 text.</p>{images}</section></body></FictionBook>\n",
        encoding="utf-8",
    )
    return path


def fb2_entity(path: Path, o: OtherJob) -> Path:
    """FB2 with external XML entities on the other job's file and the host file."""
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<!DOCTYPE FictionBook [<!ENTITY other SYSTEM "{o.text.as_uri()}">'
        '<!ENTITY host SYSTEM "file:///etc/hostname">]>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description>'
        "<title-info><genre>prose</genre><book-title>LFI</book-title><lang>en</lang>"
        "</title-info></description><body><section><p>Visible FB2 text.</p>"
        "<p>&other;</p><p>&host;</p></section></body></FictionBook>\n",
        encoding="utf-8",
    )
    return path


def epub_lfi(path: Path, o: OtherJob) -> Path:
    """The corpus EPUB with chapter 1 replaced by references outside the book, and an OPF
    manifest item whose href is the other job's file."""
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>x</title>"
        f'<link rel="stylesheet" href="{escape(o.text.as_uri())}"/></head>'
        f"<body><p>Visible EPUB text.</p>{_html_refs(o, ' />')}"
        "</body></html>"
    )
    with zipfile.ZipFile(CORPUS / "ebook" / "book.epub") as zin, zipfile.ZipFile(path, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename.endswith(".xhtml") and b"Chapter 1" in data:
                data = xhtml.encode()
            elif info.filename.endswith(".opf"):
                item = f'<item id="lfi" href={quoteattr(o.text.as_uri())} media-type="text/css"/>'
                data = data.replace(b"</manifest>", item.encode() + b"</manifest>")
            zout.writestr(info, data)
    return path


def cbz_symlink(path: Path, o: OtherJob) -> Path:
    """A CBZ whose second and third pages are symlinks to the other job's PNG and the host file
    (archive LFI: an unpacker that follows links copies them into the PDF)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("001.png", canary_png(bytes(range(40, 52)), b"own page"))
        for name, target in (("002.png", str(o.png)), ("003.png", HOST)):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return path


def djvu_incl(path: Path, o: OtherJob) -> Path:
    """A one-page DjVu whose ``INCL`` chunks name the other job's DjVu component (absolute,
    relative, ``file:`` URL) and the host file: djvulibre loads included files by id."""
    ids = [str(o.djvi), o.rel(o.djvi), o.djvi.as_uri(), HOST]
    path.write_bytes(djvu_page([_iff(b"INCL", i.encode()) for i in ids]))
    return path


def planted_djvu(path: Path, o: OtherJob) -> Path:
    """Positive control: the canary as the page's own hidden text."""
    path.write_bytes(djvu_page([_iff(b"TXTa", _djvu_txta(CANARY_TEXT, 400, 400))]))
    return path


Builder = Callable[[Path, OtherJob], Path]


@dataclass(frozen=True)
class Sample:
    name: str
    fmt: str
    build: Builder
    what: str
    raster: bool = False  # references are never drawn: compare pixels with the control run
    max_images: int | None = None  # images a PDF output may hold (None: not checked)


SAMPLES: tuple[Sample, ...] = (
    Sample(
        "lfi_pdf",
        "pdf",
        lfi_pdf,
        "external /F content and image streams, /FS /URL, reference XObject /Ref",
        raster=True,
    ),
    Sample(
        "linked_odt", "odt", linked_odt, "linked sections and image, LinkUpdateMode=2", max_images=0
    ),
    Sample(
        "linked_ods",
        "ods",
        linked_ods,
        "WEBSERVICE, external reference, LinkUpdateMode=2",
        max_images=0,
    ),
    Sample(
        "linked_docx", "docx", linked_docx, "external image r:link, INCLUDETEXT field", max_images=0
    ),
    Sample(
        "docx_template_altchunk",
        "docx",
        docx_template_altchunk,
        "attachedTemplate + linkStyles, external altChunk, subDocument",
        max_images=0,
    ),
    Sample("webservice_xlsx", "xlsx", webservice_xlsx, "WEBSERVICE formulas", max_images=0),
    Sample(
        "xlsx_external_link",
        "xlsx",
        xlsx_external_link,
        "externalLink part, fullCalcOnLoad",
        max_images=0,
    ),
    Sample("pptx_linked_media", "pptx", pptx_linked_media, "linked picture and video"),
    Sample(
        "odp_linked_media",
        "odp",
        odp_linked_media,
        "linked images, object and media frames, LinkUpdateMode=2",
        max_images=0,
    ),
    Sample("rtf_include", "rtf", rtf_include, "INCLUDETEXT / INCLUDEPICTURE fields", max_images=0),
    Sample(
        "csv_formulas",
        "csv",
        csv_formulas,
        "WEBSERVICE, external reference, INDIRECT, FILTERXML",
        max_images=0,
    ),
    Sample("html_lfi", "html", html_lfi, "img/iframe/object/embed/link/@import/CSS url()"),
    Sample("md_lfi", "md", md_lfi, "image paths, raw HTML, include attribute"),
    Sample("fb2_lfi", "fb2", fb2_lfi, "image l:href outside the book"),
    Sample("fb2_entity", "fb2", fb2_entity, "external XML entities"),
    Sample("epub_lfi", "epub", epub_lfi, "XHTML references and OPF item outside the book"),
    Sample("cbz_symlink", "cbz", cbz_symlink, "symlink entries", raster=True),
    Sample("djvu_incl", "djvu", djvu_incl, "INCL chunks naming outside files", raster=True),
)

# Positive controls: the same canary inside the document, so a run must report it.
PLANTED: tuple[Sample, ...] = (
    Sample("planted_pdf", "pdf", planted_pdf, "canary drawn by the page itself"),
    Sample("planted_rtf", "rtf", planted_rtf, "canary as document text"),
    Sample("planted_djvu", "djvu", planted_djvu, "canary as the page's hidden text"),
)

# LibreOffice binary formats: made at test time by office.convert from these samples.
DERIVED = {"doc": "linked_odt", "xls": "linked_ods", "ppt": "odp_linked_media"}


# --------------------------------------------------------------------------- checks


def _texts(data: bytes, depth: int = 0) -> list[bytes]:
    """``data`` and everything readable inside it: zip members (nested), PDF streams and text."""
    found = [data]
    if depth > 3:
        return found
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.file_size < 64 << 20:
                        found += _texts(zf.read(info), depth + 1)
        except (zipfile.BadZipFile, OSError, NotImplementedError):
            pass
    elif data.lstrip()[:5] == b"%PDF-":
        found += _pdf_texts(data)
    return found


def _pdf_texts(data: bytes) -> list[bytes]:
    found = []
    try:
        import pikepdf

        for password in ("", "example-open"):
            try:
                with pikepdf.open(io.BytesIO(data), password=password) as pdf:
                    for obj in pdf.objects:
                        if isinstance(obj, pikepdf.Stream):
                            try:
                                found.append(obj.read_bytes())
                            except pikepdf.PdfError:
                                found.append(obj.read_raw_bytes())
                    for att in pdf.attachments.values():
                        found.append(att.get_file().read_bytes())
                break
            except pikepdf.PasswordError:
                continue
    except ImportError:
        pass
    try:
        import pypdfium2

        for password in (None, "example-open"):
            try:
                doc = pypdfium2.PdfDocument(data, password=password)
            except pypdfium2.PdfiumError:
                continue
            try:
                for page in doc:
                    found.append(page.get_textpage().get_text_range().encode())
            finally:
                doc.close()
            break
    except ImportError:
        pass
    return found


def host_name() -> bytes:
    try:
        return Path(HOST).read_bytes().strip()
    except OSError:
        return b""


def leaks(data: bytes) -> list[str]:
    """Which canaries ``data`` (an output file's bytes) contains, anywhere inside it."""
    text = {
        m.decode(): [m, m.decode().encode("utf-16-le"), m.decode().encode("utf-16-be")]
        for m in MARKERS
    }
    host = host_name()
    found = set()
    for blob in _texts(data):
        upper = blob.upper()  # OCR and some writers change the case
        found |= {name for name, forms in text.items() if any(f.upper() in upper for f in forms)}
        if PNG_IDAT in blob:
            found.add("png")
        if host and host in blob:
            found.add("hostname")
    return sorted(found)


def pdf_image_count(data: bytes) -> int:
    import pypdfium2
    import pypdfium2.raw as pdfium_c

    doc = pypdfium2.PdfDocument(data)
    try:
        return sum(
            len(list(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=5)))
            for page in doc
        )
    finally:
        doc.close()


def rasters(data: bytes) -> list[bytes]:
    """The pixels of an output: PDF pages rendered at 36 dpi, images decoded, zip members too.
    Calibre's generated ``cover_image.*`` is skipped: it draws only the title, in a randomly
    picked style, so two runs of the same file differ (checked in the ebook image, task 64)."""
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [i for i in zf.infolist() if not i.filename.startswith("cover_image.")]
            return [px for i in members for px in rasters(zf.read(i))]
    if data.lstrip()[:5] == b"%PDF-":
        import pypdfium2

        try:
            doc = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError:
            return []
        try:
            return [page.render(scale=0.5, grayscale=True).to_pil().tobytes() for page in doc]
        finally:
            doc.close()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            frames = []
            for n in range(getattr(im, "n_frames", 1)):
                im.seek(n)
                frames.append(im.convert("L").tobytes())
            return frames
    except Exception:  # noqa: BLE001 - not an image: no pixels to compare
        return []


def build_all(dest: Path) -> list[Path]:
    """Write every sample to ``dest`` with a stand-in other job under ``dest/other``."""
    other = OtherJob.create(dest / "other-job" / "in", dest)
    return [s.build(dest / f"{s.name}.{s.fmt}", other) for s in (*SAMPLES, *PLANTED)]


if __name__ == "__main__":
    for p in build_all(Path(sys.argv[1] if len(sys.argv) > 1 else "lfi-samples").resolve()):
        print(p)
