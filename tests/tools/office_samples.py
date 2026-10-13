"""Hostile Office samples for the LibreOffice tools, built in the test (stdlib only).

Each builder writes a small ODF or OOXML package that tries to run a macro or pull a local file
into the output (review H2: linked images and sections, Calc external references,
``WEBSERVICE``). ``secret`` is the path of a file outside the step's work dir, standing in for
another job's file; the tests check that its content never reaches the output.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pytest

TEMPLATE = Path("/opt/filex/lo-profile")
needs_lo = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.is_dir(),
    reason="needs LibreOffice and the profile template (worker-office image)",
)

_ODF = "urn:oasis:names:tc:opendocument:xmlns"
_NS = (
    f'xmlns:office="{_ODF}:office:1.0" xmlns:text="{_ODF}:text:1.0" '
    f'xmlns:table="{_ODF}:table:1.0" xmlns:draw="{_ODF}:drawing:1.0" '
    f'xmlns:svg="{_ODF}:svg-compatible:1.0" xmlns:of="{_ODF}:of:1.2" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:script="'
    f'{_ODF}:script:1.0" xmlns:ooo="http://openoffice.org/2004/office" office:version="1.2"'
)
_MIMES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
}


def _odf(path: Path, kind: str, body: str, extra: dict[str, str] | None = None) -> Path:
    extra = extra or {}
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<office:document-content {_NS}>{body}</office:document-content>"
    )
    entries = "".join(
        f'<manifest:file-entry manifest:full-path="{n}" manifest:media-type="text/xml"/>'
        for n in ["content.xml", *extra]
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest='
        '"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{_MIMES[kind]}"/>'
        f"{entries}</manifest:manifest>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), _MIMES[kind], compress_type=zipfile.ZIP_STORED)
        zf.writestr("content.xml", content)
        zf.writestr("META-INF/manifest.xml", manifest)
        for name, data in extra.items():
            zf.writestr(name, data)
    return path


def macro_odt(path: Path, marker: Path) -> Path:
    """An ODT whose Basic macro runs on load: it replaces the text with "MACRO RAN" and writes
    ``marker``."""
    module = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" '
        'script:language="StarBasic">'
        + escape(
            "Sub Main\n"
            '  ThisComponent.Text.setString("MACRO RAN")\n'
            f'  Open "{marker.as_posix()}" For Output As #1\n'
            '  Print #1, "ran"\n'
            "  Close #1\n"
            "End Sub\n"
        )
        + "</script:module>"
    )
    libraries = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<library:libraries xmlns:library="http://openoffice.org/2000/library" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><library:library library:name="Standard" '
        'xlink:href="Basic/Standard/script-lb.xml/" xlink:type="simple" library:link="false"/>'
        "</library:libraries>"
    )
    library = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<library:library xmlns:library="http://openoffice.org/2000/library" '
        'library:name="Standard" library:readonly="false" library:passwordprotected="false">'
        '<library:element library:name="Module1"/></library:library>'
    )
    href = "vnd.sun.star.script:Standard.Module1.Main?language=Basic&amp;location=document"
    body = (
        "<office:scripts><office:event-listeners>"
        '<script:event-listener script:language="ooo:script" script:event-name="dom:load" '
        f'xlink:href="{href}"/></office:event-listeners></office:scripts>'
        "<office:body><office:text><text:p>Plain text before any macro.</text:p>"
        "</office:text></office:body>"
    )
    return _odf(
        path,
        "odt",
        body,
        {
            "Basic/script-lc.xml": libraries,
            "Basic/Standard/script-lb.xml": library,
            "Basic/Standard/Module1.xml": module,
        },
    )


def linked_odt(path: Path, secret: Path, image: Path) -> Path:
    """An ODT with a section linked to ``secret`` (a text file), a linked image and a link to
    /etc/hostname, whose own settings ask for automatic link updates (review M-2)."""
    body = (
        "<office:body><office:text><text:p>Linked document.</text:p>"
        '<text:section text:name="S1"><text:section-source '
        f"xlink:href={quoteattr(secret.as_uri())} "
        'text:filter-name="Text"/><text:p>cached</text:p></text:section>'
        '<text:section text:name="S2"><text:section-source xlink:href="file:///etc/hostname" '
        'text:filter-name="Text"/><text:p>cached</text:p></text:section>'
        '<text:p><draw:frame draw:name="I1" svg:width="4cm" svg:height="4cm">'
        f'<draw:image xlink:href={quoteattr(image.as_uri())} xlink:type="simple" '
        'xlink:show="embed" xlink:actuate="onLoad"/></draw:frame></text:p>'
        "</office:text></office:body>"
    )
    return _odf(path, "odt", body, _AUTO_UPDATE)


# A hostile document asks for automatic link updates in its own settings.
_AUTO_UPDATE = {
    "settings.xml": (
        '<?xml version="1.0" encoding="UTF-8"?><office:document-settings '
        f'xmlns:office="{_ODF}:office:1.0" xmlns:config="{_ODF}:config:1.0" '
        'office:version="1.2"><office:settings><config:config-item-set '
        'config:name="ooo:configuration-settings"><config:config-item '
        'config:name="LinkUpdateMode" config:type="short">2</config:config-item>'
        "</config:config-item-set></office:settings></office:document-settings>"
    )
}


def _cell_formula(formula: str) -> str:
    return (
        f"<table:table-cell table:formula={quoteattr('of:' + formula)}>"
        "<text:p></text:p></table:table-cell>"
    )


def linked_ods(path: Path, secret: Path, secret_ods: Path) -> Path:
    """An ODS with no cached results: WEBSERVICE on /etc/hostname and ``secret``, and an
    external reference into ``secret_ods``."""
    rows = [
        f'=COM.MICROSOFT.WEBSERVICE("{secret.as_uri()}")',
        '=COM.MICROSOFT.WEBSERVICE("file:///etc/hostname")',
        f"=['{secret_ods.as_uri()}'#$Sheet1.A1]",
        '=COM.MICROSOFT.WEBSERVICE("http://example.invalid/")',
    ]
    cells = "".join(f"<table:table-row>{_cell_formula(f)}</table:table-row>" for f in rows)
    body = (
        '<office:body><office:spreadsheet><table:table table:name="Sheet1">'
        f"{cells}</table:table></office:spreadsheet></office:body>"
    )
    return _odf(path, "ods", body, _AUTO_UPDATE)


def plain_ods(path: Path, text: str) -> Path:
    body = (
        '<office:body><office:spreadsheet><table:table table:name="Sheet1"><table:table-row>'
        f'<table:table-cell office:value-type="string"><text:p>{escape(text)}</text:p>'
        "</table:table-cell></table:table-row></table:table></office:spreadsheet></office:body>"
    )
    return _odf(path, "ods", body)


_CT = (
    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns='
    '"http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/{main}" ContentType="{ctype}"/></Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
    '"http://schemas.openxmlformats.org/package/2006/relationships">{}</Relationships>'
)
_OFFDOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def linked_docx(path: Path, secret: Path, image: Path) -> Path:
    """A DOCX with an externally linked image and an INCLUDETEXT field on ``secret``."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    drawing = (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="1440000" cy="1440000"/>'
        '<wp:docPr id="1" name="Linked"/><a:graphic><a:graphicData '
        f'uri="{pic}"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="Linked"/><pic:cNvPicPr/>'
        '</pic:nvPicPr><pic:blipFill><a:blip r:link="rIdImg"/><a:stretch><a:fillRect/>'
        '</a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="1440000" cy="1440000"/></a:xfrm><a:prstGeom prst="rect"/></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )
    field = (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve">'
        f' INCLUDETEXT "{escape(secret.as_posix())}" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>cached</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}" xmlns:a="{a}" '
        f'xmlns:pic="{pic}" xmlns:wp="{wp}" xmlns:r="{_OFFDOC}"><w:body>'
        f"<w:p><w:r><w:t>Linked document.</w:t></w:r></w:p><w:p>{drawing}</w:p>"
        f"<w:p>{field}</w:p></w:body></w:document>"
    )
    rels = _RELS.format(
        f'<Relationship Id="rIdImg" Type="{_OFFDOC}/image" '
        f'Target={quoteattr(image.as_uri())} TargetMode="External"/>'
    )
    ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CT.format(main="word/document.xml", ctype=ctype))
        zf.writestr(
            "_rels/.rels",
            _RELS.format(
                f'<Relationship Id="rId1" Type="{_OFFDOC}/officeDocument" '
                'Target="word/document.xml"/>'
            ),
        )
        zf.writestr("word/document.xml", document)
        zf.writestr("word/_rels/document.xml.rels", rels)
    return path


def webservice_xlsx(path: Path, secret: Path) -> Path:
    """An XLSX with WEBSERVICE formulas on ``secret`` and /etc/hostname, no cached values."""
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cells = "".join(
        f'<row r="{i}"><c r="A{i}"><f>{escape(f)}</f></c></row>'
        for i, f in enumerate(
            [f'WEBSERVICE("{secret.as_uri()}")', 'WEBSERVICE("file:///etc/hostname")'], 1
        )
    )
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{s}">'
        f"<sheetData>{cells}</sheetData></worksheet>"
    )
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{s}" xmlns:r="{_OFFDOC}">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    sheet_ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            _CT.format(main="xl/workbook.xml", ctype=ctype).replace(
                "</Types>",
                f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{sheet_ct}"/>'
                "</Types>",
            ),
        )
        zf.writestr(
            "_rels/.rels",
            _RELS.format(
                f'<Relationship Id="rId1" Type="{_OFFDOC}/officeDocument" '
                'Target="xl/workbook.xml"/>'
            ),
        )
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            _RELS.format(
                f'<Relationship Id="rId1" Type="{_OFFDOC}/worksheet" '
                'Target="worksheets/sheet1.xml"/>'
            ),
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def tiny_png(path: Path) -> Path:
    """A valid 2x2 PNG (stdlib zlib), standing in for another job's image."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    raw = b"".join(b"\0" + b"\xff\x00\x00" * 2 for _ in range(2))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def soffice_processes() -> list[str]:
    """/proc stat lines of live soffice / soffice.bin / oosplash processes (Linux /proc). Zombies do not
    count: in the test container pytest is PID 1 and does not reap orphans (the worker's
    runner does)."""
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                stat = Path(f"/proc/{name}/stat").read_text()
            except OSError:
                continue
            comm, state = stat[stat.index("(") + 1 : stat.rindex(")")], stat.split(")")[-1][1]
            if comm.startswith(("soffice", "oosplash")) and state != "Z":
                found.append(stat.strip())
    return found


def pdf_text(path: Path) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def pdf_image_count(path: Path) -> int:
    import pymupdf

    with pymupdf.open(path) as doc:
        return sum(len(page.get_images()) for page in doc)
