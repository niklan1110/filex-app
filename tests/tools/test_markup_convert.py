"""markup.convert (plan task 51). Pure parts run everywhere; real conversions need Pandoc and run
in the worker-ebook image (``needs_pandoc``)."""

import os
import time
import zipfile

import pytest
from conftest import CORPUS
from ebook_samples import (
    CYRILLIC,
    EBOOK,
    FB2_CP1251,
    LFI_HTML,
    book_text,
    encryption_xml,
    epub_with,
    leaked,
    needs_pandoc,
    scratch_copy,
    write,
)

from filex.formats import sniff
from filex.registry import StepError, ToolOptions
from filex.tools.ebook import markup_convert
from filex.tools.ebook.markup_convert import EDGES, command, drop_images, run, utf8


def fails(code, fn, *args):
    with pytest.raises(StepError) as e:
        fn(*args)
    assert e.value.code == code, e.value.message
    return e.value.message


# --- pure logic


def test_edges_and_lossy_txt():
    pairs = {(e.src, e.dst) for e in EDGES}
    assert len(pairs) == len(EDGES) == 5 * 5
    assert ("md", "epub") in pairs and ("fb2", "md") in pairs and ("docx", "txt") in pairs
    assert not any(s == "txt" for s, _ in pairs)
    assert {(e.src, e.dst) for e in EDGES if e.lossy} == {(a, b) for a, b in pairs if b == "txt"}


def test_every_command_runs_sandboxed_with_a_heap_cap(tmp_path):
    for e in EDGES:
        args = command(tmp_path / "in.x", e.src, e.dst, tmp_path / "out.y")
        assert args[:5] == ["pandoc", "+RTS", f"-M{markup_convert.HEAP_MB}m", "-RTS", "--sandbox"]
        assert not {"--filter", "--lua-filter", "--template", "--resource-path"} & set(args)
        assert ("--embed-resources" in args) == (e.dst == "html")
        assert ("--standalone" in args) == (e.dst in ("html", "txt"))  # txt: the title block


def test_drop_images_keeps_alt_text_and_removes_emptied_paragraphs():
    def img(*alt):
        return {"t": "Image", "c": [["", [], []], list(alt), ["media/a.png", ""]]}

    cat = {"t": "Str", "c": "cat"}
    blocks = [
        {"t": "Para", "c": [img()]},
        {"t": "Para", "c": [{"t": "Str", "c": "A"}, img(cat)]},
        {"t": "BulletList", "c": [[{"t": "Plain", "c": [img()]}]]},
    ]
    assert drop_images(blocks) == [
        {"t": "Para", "c": [{"t": "Str", "c": "A"}, cat]},
        {"t": "BulletList", "c": [[]]},
    ]


@pytest.mark.parametrize(
    ("data", "fmt"),
    [
        (CYRILLIC.encode("utf-8"), "md"),
        (b"\xef\xbb\xbf" + CYRILLIC.encode("utf-8"), "md"),
        (CYRILLIC.encode("utf-16"), "html"),
        (CYRILLIC.encode("cp1251"), "md"),
    ],
)
def test_text_inputs_are_re_encoded_as_utf8(data, fmt):
    assert utf8(data, fmt, "It") == CYRILLIC.encode("utf-8")


def test_fb2_is_re_encoded_from_its_xml_declaration():
    out = utf8(FB2_CP1251.encode("cp1251"), "fb2", "It").decode("utf-8")
    assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>') and CYRILLIC in out


def test_fb2_with_an_unknown_encoding_is_invalid():
    fails("INVALID_INPUT", utf8, b'<?xml version="1.0" encoding="x-nope"?><a/>', "fb2", "It")


def test_target_checks(step_ctx, tmp_path):
    src = write(tmp_path, "md", "# Hi\n")
    fails("INVALID_OPTIONS", run, step_ctx, src, ToolOptions())
    step_ctx.target = "md"
    fails("INVALID_OPTIONS", run, step_ctx, src, ToolOptions())


def test_drm_epub_is_refused_before_pandoc(step_ctx, tmp_path):
    step_ctx.target = "md"
    aes = "http://www.w3.org/2001/04/xmlenc#aes128-cbc"
    src = epub_with(tmp_path / "0.epub", {"META-INF/encryption.xml": encryption_xml(aes)})
    fails("DRM_PROTECTED", run, step_ctx, src, ToolOptions())


def test_traversal_epub_is_refused_before_pandoc(step_ctx, tmp_path):
    step_ctx.target = "html"
    src = tmp_path / "0.epub"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip")
        zf.writestr("/etc/evil.xhtml", b"<html/>")
    fails("INVALID_INPUT", run, step_ctx, src, ToolOptions())


# --- real conversions (worker-ebook image)


def sample(tmp_path, fmt):
    d = tmp_path / "in"
    match fmt:
        case "md":
            return write(d, "md", f"# Chapter 1\n\n{CYRILLIC}\n\nThe *quick* brown fox.\n")
        case "html":
            return write(d, "html", f"<html><body><h1>Chapter</h1><p>{CYRILLIC}</p></body></html>")
        case "docx":
            return scratch_copy(CORPUS / "office" / "report.docx", d, "docx")
        case "fb2":
            return write(d, "fb2", FB2_CP1251.encode("cp1251"))
        case _:
            return scratch_copy(EBOOK / f"book.{fmt}", d, fmt)


def convert(step_ctx, src, target):
    step_ctx.target = target
    (out,) = run(step_ctx, src, ToolOptions())
    assert out.stat().st_size > 0
    return out


@needs_pandoc
@pytest.mark.parametrize(("src_fmt", "dst"), sorted((e.src, e.dst) for e in EDGES))
def test_every_pair_converts(step_ctx, tmp_path, src_fmt, dst):
    out = convert(step_ctx, sample(tmp_path, src_fmt), dst)
    assert sniff(out, f"x.{dst}") == dst


@needs_pandoc
@pytest.mark.parametrize("src_fmt", ["md", "html", "fb2"])
def test_cyrillic_text_survives(step_ctx, tmp_path, src_fmt):
    out = convert(step_ctx, sample(tmp_path, src_fmt), "epub")
    assert CYRILLIC in book_text(out)


@needs_pandoc
def test_docx_to_txt_keeps_the_title_and_no_image_brackets(step_ctx, tmp_path):
    text = convert(step_ctx, sample(tmp_path, "docx"), "txt").read_text(encoding="utf-8")
    assert text.startswith("FileX DOCX sample\n")  # w:pStyle Title: metadata to Pandoc
    assert "Section 1" in text and "[]" not in text


@needs_pandoc
def test_txt_shows_images_as_their_alt_text(step_ctx, tmp_path):
    md = "# T\n\nSee ![a cat](x.png) and ![](y.png) here, [kept] as is.\n"
    text = convert(step_ctx, write(tmp_path / "in", "md", md), "txt").read_text(encoding="utf-8")
    assert "See a cat and here, [kept] as is." in " ".join(text.split())


@needs_pandoc
def test_html_output_embeds_the_epub_images(step_ctx, tmp_path):
    out = convert(step_ctx, sample(tmp_path, "epub"), "html")
    assert "data:image/" in out.read_text(encoding="utf-8")


@needs_pandoc
@pytest.mark.parametrize("dst", ["md", "docx", "epub", "fb2", "txt"])
def test_html_with_local_file_references_leaks_nothing(step_ctx, tmp_path, dst):
    out = convert(step_ctx, write(tmp_path / "in", "html", LFI_HTML), dst)
    assert not leaked(out)


@needs_pandoc
@pytest.mark.parametrize("dst", ["html", "docx", "epub"])
def test_markdown_with_local_file_references_leaks_nothing(step_ctx, tmp_path, dst):
    md = "# T\n\n![a](file:///etc/passwd) ![b](/etc/passwd) ![c](../../../etc/passwd)\n"
    out = convert(step_ctx, write(tmp_path / "in", "md", md), dst)
    assert not leaked(out)


@needs_pandoc
def test_a_hung_pandoc_is_killed_at_its_timeout(step_ctx, tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "pandoc").write_text("#!/bin/sh\nexec sleep 300\n")
    (fake / "pandoc").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(markup_convert, "ENGINE_TIMEOUT_S", 1)
    step_ctx.target = "html"
    started = time.monotonic()
    fails("ENGINE_FAILED", run, step_ctx, write(tmp_path / "in", "md", "# Hi\n"), ToolOptions())
    assert time.monotonic() - started < 10
