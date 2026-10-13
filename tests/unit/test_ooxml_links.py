"""OOXML external-reference pre-check (task 64: Calibre's DOCX input embeds an external r:link
image, so a DOCX naming another job's file leaked it)."""

import os
import zipfile
from pathlib import Path

import pytest

from filex.registry import StepError
from filex.tools import _ooxml

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "office"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MS = "http://schemas.microsoft.com/office/2007/relationships"
NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MESSAGE = "This document links to files outside it, which FileX does not allow."


def rel(target: str, kind: str = f"{R}/image", mode: str | None = "External") -> str:
    m = f' TargetMode="{mode}"' if mode else ""
    return f'<Relationship Id="rId9" Type="{kind}" Target="{target}"{m}/>'


def package(tmp_path: Path, *rels: str, part: str = "word/_rels/document.xml.rels") -> Path:
    """A minimal DOCX whose ``part`` holds ``rels``."""
    path = tmp_path / "0.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        if part != "_rels/.rels":
            zf.writestr("_rels/.rels", f'<Relationships xmlns="{NS}"/>')
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr(part, f'<Relationships xmlns="{NS}">{"".join(rels)}</Relationships>')
    return path


def refused(path: Path) -> str:
    with pytest.raises(StepError) as e:
        _ooxml.check_links(path)
    assert e.value.code == "INVALID_INPUT"
    return e.value.message


@pytest.mark.parametrize(
    "target",
    [
        "file:///data/jobs/x/in/1.png",  # the leak: Calibre embeds it
        "FILE:///data/jobs/x/in/1.png",
        "File://localhost/data/jobs/x/in/1.png",
        "file://server/share/1.png",
        "file:/data/x.png",
        "%66ile:///data/x.png",  # percent-encoded scheme
        "file%3A%2F%2F%2Fdata%2Fx.png",
        "%2566ile:///data/x.png",  # encoded twice
        " file:///data/x.png",
        "fi&#9;le:///data/x.png",  # a tab inside the scheme
        "/data/jobs/x/in/1.png",
        "/etc/hostname",
        "C:\\Users\\x\\1.png",
        "c:/x.png",
        "\\\\server\\share\\1.png",  # UNC
        "//server/share/1.png",
        "../../jobs/x/in/1.png",
        "..\\..\\jobs\\x\\in\\1.png",
        "%2e%2e/%2e%2e/x.png",
        "1.png",  # beside the document: still a local file
        "media/1.png",
        "mhtml:file:///data/x.mht",
        "vnd.sun.star.expand:$BRAND_BASE_DIR/x.png",
        "smb://server/share/1.png",
    ],
)
def test_external_image_on_a_local_file_is_refused(tmp_path, target):
    assert refused(package(tmp_path, rel(target))) == MESSAGE


@pytest.mark.parametrize(
    "kind",
    [
        f"{R}/oleObject",
        f"{R}/package",
        f"{R}/subDocument",
        f"{R}/attachedTemplate",
        f"{R}/frame",
        f"{R}/externalLinkPath",
        f"{MS}/externalLinkLong",
        f"{R}/aFChunk",
        f"{R}/video",
        f"{R}/audio",
        f"{MS}/media",
        f"{R}/mailMergeSource",
        "http://example.com/a-type-nobody-knows",
    ],
)
def test_every_external_type_that_is_not_a_hyperlink_must_be_on_the_web(tmp_path, kind):
    assert refused(package(tmp_path, rel("file:///data/x.docx", kind))) == MESSAGE
    assert refused(package(tmp_path, rel("x.docx", kind))) == MESSAGE
    _ooxml.check_links(package(tmp_path, rel("https://example.com/x.docx", kind)))


@pytest.mark.parametrize(
    "target",
    [
        "file:///etc/hostname",
        "FILE://server/share/x.pdf",
        "/data/jobs/x/in/3.pdf",
        "\\\\server\\share\\x.pdf",
        "C:\\x.pdf",
        "../x.pdf",
        "docs/..\\..\\x.pdf",
        "%2E%2E%2Fx.pdf",
    ],
)
def test_hyperlink_to_a_local_file_passes_because_no_engine_loads_it(tmp_path, target):
    # Owner decision (2026-10-11): office documents often link to \\server\share files; a
    # hyperlink is only text, so refusing the document would block ordinary files for no gain.
    # Relationship types that LOAD content (image, oleObject, ...) stay refused above.
    _ooxml.check_links(package(tmp_path, rel(target, f"{R}/hyperlink")))


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/a?b=c#d",
        "HTTP://example.com/",
        "mailto:someone@example.com",
        "other.docx",
        "docs/other.docx",
        "#Bookmark",
        "tel:+123",
    ],
)
def test_web_and_relative_hyperlinks_pass(tmp_path, target):
    _ooxml.check_links(package(tmp_path, rel(target, f"{R}/hyperlink")))


def test_remote_images_pass(tmp_path):
    _ooxml.check_links(package(tmp_path, rel("https://example.com/x.png"), rel("http://e.x/y")))


@pytest.mark.parametrize("mode", ["external", "EXTERNAL", " External "])
def test_target_mode_is_read_without_case_or_spaces(tmp_path, mode):
    assert refused(package(tmp_path, rel("/data/x.png", mode=mode))) == MESSAGE


def test_one_local_link_among_web_links_is_refused(tmp_path):
    rels = [rel("https://example.com/x.png"), rel("other.docx", f"{R}/hyperlink")]
    assert refused(package(tmp_path, *rels, rel("file:///data/x.png"))) == MESSAGE


@pytest.mark.parametrize(
    "part",
    [
        "_rels/.rels",
        "word/_rels/settings.xml.rels",
        "word/_rels/header1.xml.rels",
        "xl/externalLinks/_rels/externalLink1.xml.rels",
        "ppt/slides/_rels/slide1.xml.rels",
        "WORD/_RELS/DOCUMENT.XML.RELS",
    ],
)
def test_every_relationships_part_is_read(tmp_path, part):
    assert refused(package(tmp_path, rel("file:///data/x.png"), part=part)) == MESSAGE


@pytest.mark.parametrize(
    "part", ["word\\_rels\\document.xml.rels", "word/_rels\\settings.xml.rels"]
)
def test_relationships_part_named_with_backslashes_is_read(tmp_path, monkeypatch, part):
    # Security review L-3: a reader that maps \ to / sees word/_rels/...; the check must too.
    # zipfile turns os.sep into / on Windows only, so pin os.sep to keep the raw name here too.
    monkeypatch.setattr(os, "sep", "/")
    assert refused(package(tmp_path, rel("file:///data/x.png"), part=part)) == MESSAGE


def test_relationship_without_the_package_namespace_is_read(tmp_path):
    path = tmp_path / "0.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/_rels/document.xml.rels", f"<Relationships>{rel('/x.png')}</Relationships>"
        )
    assert refused(path) == MESSAGE


@pytest.mark.parametrize(
    "target", ["../../../etc/hostname", "../../x.png", "file:///etc/hostname", "C:/x.png", "//s/x"]
)
def test_internal_target_outside_the_package_is_refused(tmp_path, target):
    assert refused(package(tmp_path, rel(target, mode=None))) == MESSAGE


@pytest.mark.parametrize(
    "target", ["media/image1.png", "/word/media/image1.png", "../customXml/item1.xml", "./a.xml"]
)
def test_internal_target_inside_the_package_passes(tmp_path, target):
    _ooxml.check_links(package(tmp_path, rel(target, mode=None)))


def test_entities_in_relationships_are_refused(tmp_path):
    path = tmp_path / "0.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/_rels/document.xml.rels",
            '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><Relationships>&x;'
            "</Relationships>",
        )
    with pytest.raises(StepError) as e:
        _ooxml.check_links(path)
    assert e.value.code == "INVALID_INPUT"


def test_oversized_relationships_part_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(_ooxml, "MAX_RELS_BYTES", 100)
    path = package(tmp_path, *[rel("https://example.com/x.png")] * 10)
    assert "too large" in refused(path)


@pytest.mark.parametrize("name", sorted(p.name for p in CORPUS.glob("*.*x") if p.is_file()))
def test_corpus_documents_pass(name):
    _ooxml.check_links(CORPUS / name)


# --- the engines' entry points run the check before the engine starts


def _linked_docx(tmp_path: Path) -> Path:
    return package(tmp_path, rel("file:///data/jobs/x/in/1.png"))


def test_calibre_and_pandoc_docx_inputs_are_checked(tmp_path):
    from filex.tools.ebook import _books

    with pytest.raises(StepError) as e:
        _books.check_input(None, _linked_docx(tmp_path), "docx", '"a.docx"')
    assert (e.value.code, e.value.message) == ("INVALID_INPUT", MESSAGE)


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx"])
def test_libreoffice_inputs_are_checked(tmp_path, fmt):
    from filex.tools.office import _lo

    src = _linked_docx(tmp_path).rename(tmp_path / f"0.{fmt}")
    with pytest.raises(StepError) as e:
        _lo.convert(src, "pdf", "pdf", tmp_path, '"a"', 10)
    assert (e.value.code, e.value.message) == ("INVALID_INPUT", MESSAGE)
