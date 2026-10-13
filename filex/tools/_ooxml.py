"""OOXML external-reference pre-check (plan task 64, review H2).

A DOCX, XLSX or PPTX names the files it uses in its relationship parts (``_rels/.rels``,
``word/_rels/document.xml.rels``, ``xl/externalLinks/_rels/*.rels``...; ECMA-376 Part 2, 9.3). A
relationship with ``TargetMode="External"`` points outside the package. Calibre's DOCX input
reads an external image (``<a:blip r:link>``) whose target is a ``file:`` URI and embeds the
file, so a DOCX could pull another job's input into its output. Every engine that reads OOXML
(Calibre, Pandoc, LibreOffice) gets the file only after :func:`check_links` passes; Pandoc and
LibreOffice did not follow these links in our tests, so for them it is defence in depth.

Refused, as ``INVALID_INPUT``:

- an external relationship of any type but ``hyperlink`` whose target is not an ``http(s)``
  URL: ``file:`` URIs, absolute, drive or UNC paths, relative paths (they resolve beside the
  document on disk), other schemes. Images, OLE objects, packages, sub-documents, templates,
  frames, external workbooks, altChunks, media and unknown types all load content, and the
  worker has no network, so a web target fetches nothing;
- an internal target that leaves the package: ``file:``, a drive or UNC path, or ``..``
  climbing above the package root (resolved from the source part's folder).

Every external ``hyperlink`` passes, whatever its target, ``file:``, UNC and ``..`` paths
included (owner decision 2026-10-11): a hyperlink is only a link that no engine loads, and office
documents often link to ``\\\\server\\share`` files. Calibre 9.16 logged such targets as not found
and embedded nothing (docs/reviews/final-security-review.md, L-3).

Targets are judged after percent-decoding (repeated, so double encoding does not hide a
scheme), with ``\\`` read as ``/``, whitespace and control characters dropped and case ignored.
``TargetMode`` is compared the same way. Relationship elements and their attributes are matched
by local name, so a missing or different namespace does not hide one. Relationship parts are
found by name (``_rels/*.rels`` in any folder, any case, ``\\`` read as ``/``) and parsed with defusedxml; one over
``MAX_RELS_BYTES`` is refused rather than parsed.

Call it after :func:`filex.tools._archive.check`, which bounds the package.

ODF (``xlink:href`` in content.xml) is not checked here: only LibreOffice reads ODF in FileX,
and its profile never updates links (tests/tools/test_office_to_pdf.py).
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from filex.registry import StepError
from filex.tools._xml import parse_xml

LINKS = "This document links to files outside it, which FileX does not allow."
MAX_RELS_BYTES = 32 * 2**20

_RELS_PART = re.compile(r"(?:^|/)_rels/[^/]*\.rels$", re.IGNORECASE)
_DROP = re.compile(r"[\x00-\x20\x7f]+")
_WEB = re.compile(r"https?://[^/]")
_DRIVE = re.compile(r"[a-z]:")
_DOTDOT = re.compile(r"(?:^|/)\.\.(?:[/?#;]|$)")


def check_links(path: Path) -> None:
    """Raise INVALID_INPUT when the package at ``path`` refers to files outside it (module
    docstring)."""
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")  # some readers map \ to / (review L-3)
            if not _RELS_PART.search(name):
                continue
            if info.file_size > MAX_RELS_BYTES:
                raise StepError(
                    "INVALID_INPUT", "This document's relationship list is too large to check."
                )
            # "word/_rels/document.xml.rels" describes word/document.xml: targets start at word/
            source_dir = posixpath.dirname(posixpath.dirname(name))
            root = parse_xml(zf.read(info), "This document")
            for el in root.iter():
                if _local(el.tag) == "relationship" and _refused(el.attrib, source_dir):
                    raise StepError("INVALID_INPUT", LINKS)


def _local(name: object) -> str:
    return str(name).rsplit("}", 1)[-1].casefold()


def _refused(attrib: dict[str, str], source_dir: str) -> bool:
    attrs = {_local(k): v for k, v in attrib.items()}
    external = _norm(attrs.get("targetmode", "")) == "external"
    hyperlink = _norm(attrs.get("type", "")).endswith("/hyperlink")
    targets = [v for k, v in attrib.items() if _local(k) == "target"]
    for raw in targets:
        t = _norm(raw)
        if not external:
            bad = _leaves_package(t, source_dir)
        elif hyperlink:
            bad = (
                False  # a hyperlink is only a link; no engine loads it (owner decision 2026-10-11)
            )
        else:
            bad = not _WEB.match(t) or "file:" in t
        if bad:
            return True
    return False


def _norm(value: str) -> str:
    """Percent-decoded until stable, ``\\`` as ``/``, no whitespace or controls, lower case."""
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return _DROP.sub("", value).replace("\\", "/").casefold()


def _leaves_package(t: str, source_dir: str) -> bool:
    if "file:" in t or t.startswith("//") or _DRIVE.match(t):
        return True
    path = t.split("#", 1)[0]
    parts = path.split("/") if path.startswith("/") else [*source_dir.split("/"), *path.split("/")]
    depth = 0
    for p in parts:
        if p == "..":
            depth -= 1
            if depth < 0:
                return True
        elif p not in ("", "."):
            depth += 1
    return False
