"""ebook.convert (plan task 51). Pure parts run everywhere; real conversions need Calibre and run
in the worker-ebook image (``needs_calibre``)."""

import os
import time
import zipfile
from pathlib import Path

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
    mobi_with_encryption,
    needs_calibre,
    scratch_copy,
    write,
)

from filex.formats import sniff
from filex.registry import StepError, ToolOptions
from filex.tools.ebook import _books, ebook_convert
from filex.tools.ebook.ebook_convert import (
    EDGES,
    SOURCES,
    TARGETS,
    estimated_pages,
    fb2_text_bytes,
    memory_limit_mb,
    pdf_page_cap,
    run,
)

IDPF_FONT = "http://www.idpf.org/2008/embedding"
AES = "http://www.w3.org/2001/04/xmlenc#aes128-cbc"


def fails(code, fn, *args):
    with pytest.raises(StepError) as e:
        fn(*args)
    assert e.value.code == code, e.value.message
    return e.value.message


# --- pure logic


def test_edges_cover_every_pair_but_html_targets_and_mark_pdf_sources_and_txt_lossy():
    pairs = {(e.src, e.dst) for e in EDGES}
    assert len(pairs) == len(EDGES) == 8 * 7 - 7  # html is a source only
    assert ("epub", "mobi") in pairs and ("fb2", "azw3") in pairs and ("html", "epub") in pairs
    assert not any(d == "html" for _, d in pairs)
    assert {(e.src, e.dst) for e in EDGES if e.lossy} == {
        (a, b) for a, b in pairs if a == "pdf" or b == "txt"
    }


def test_a_missing_target_is_an_options_error(step_ctx, tmp_path):
    src = scratch_copy(EBOOK / "book.epub", tmp_path / "in", "epub")
    fails("INVALID_OPTIONS", run, step_ctx, src, ToolOptions())


def test_a_target_without_an_edge_is_refused(step_ctx, tmp_path):
    step_ctx.target = "html"
    src = scratch_copy(EBOOK / "book.epub", tmp_path / "in", "epub")
    fails("INVALID_OPTIONS", run, step_ctx, src, ToolOptions())


@pytest.mark.parametrize(
    "extra",
    [
        {"META-INF/rights.xml": b"<rights/>"},
        {"META-INF/sinf.xml": b"<sinf/>"},
        {"META-INF/encryption.xml": encryption_xml(IDPF_FONT, AES)},
    ],
    ids=["adobe-rights", "apple-sinf", "aes-content"],
)
def test_epub_with_drm_is_refused_before_calibre(step_ctx, tmp_path, extra):
    step_ctx.target = "mobi"
    src = epub_with(tmp_path / "0.epub", extra)
    msg = fails("DRM_PROTECTED", run, step_ctx, src, ToolOptions())
    assert "DRM" in msg


def test_epub_with_only_font_obfuscation_is_not_drm(tmp_path):
    src = epub_with(tmp_path / "0.epub", {"META-INF/encryption.xml": encryption_xml(IDPF_FONT)})
    _books.epub_drm(src, "It")  # no error


def test_encryption_xml_with_entities_is_refused(tmp_path):
    bomb = b'<?xml version="1.0"?><!DOCTYPE e [<!ENTITY a "aaaa">]><encryption>&a;</encryption>'
    src = epub_with(tmp_path / "0.epub", {"META-INF/encryption.xml": bomb})
    fails("INVALID_INPUT", _books.epub_drm, src, "It")


@pytest.mark.parametrize("fmt", ["mobi", "azw3"])
def test_mobi_and_azw3_with_drm_are_refused(step_ctx, tmp_path, fmt):
    step_ctx.target = "epub"
    src = mobi_with_encryption(EBOOK / f"book.{fmt}", tmp_path / f"0.{fmt}")
    assert sniff(src) == fmt
    fails("DRM_PROTECTED", run, step_ctx, src, ToolOptions())


@pytest.mark.parametrize("fmt", ["mobi", "azw3"])
def test_corpus_mobi_and_azw3_have_no_drm(fmt):
    _books.mobi_drm(EBOOK / f"book.{fmt}", "It")


def test_traversal_epub_is_refused_before_calibre(step_ctx, tmp_path):
    step_ctx.target = "pdf"
    src = tmp_path / "0.epub"
    with zipfile.ZipFile(EBOOK / "book.epub") as zin, zipfile.ZipFile(src, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info))
        zout.writestr("../../evil.xhtml", b"<html/>")
    assert "unsafe name" in fails("INVALID_INPUT", run, step_ctx, src, ToolOptions())


def test_zip_bomb_epub_is_refused_before_calibre(step_ctx, tmp_path):
    step_ctx.target = "mobi"
    src = tmp_path / "0.epub"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip")
        zf.writestr("OEBPS/bomb.xhtml", b"\0" * (50 * 2**20))
    assert "zip bomb" in fails("INVALID_INPUT", run, step_ctx, src, ToolOptions())


def test_fb2_with_entities_is_refused_before_calibre(step_ctx, tmp_path):
    step_ctx.target = "epub"
    src = write(tmp_path, "fb2", '<?xml version="1.0"?><!DOCTYPE F [<!ENTITY x "y">]>'
                '<FictionBook>&x;</FictionBook>')  # fmt: skip
    fails("INVALID_INPUT", run, step_ctx, src, ToolOptions())


def test_encrypted_pdf_is_refused_before_calibre(step_ctx, tmp_path):
    step_ctx.target = "epub"
    src = scratch_copy(CORPUS / "pdf" / "encrypted_aes256.pdf", tmp_path, "pdf")
    fails("ENCRYPTED_PDF", run, step_ctx, src, ToolOptions())


def test_fb2_text_bytes_skips_binary_sections_even_across_chunks(tmp_path):
    body = b"<p>" + b"t" * 100 + b"</p>"
    binary = b'<binary id="a">' + b"Q" * 5000 + b"</binary>"
    data = b"<FictionBook>" + body + binary + body + b"</FictionBook>"
    src = write(tmp_path, "fb2", data)
    for chunk in (7, 16, 1000, 1 << 20):
        assert fb2_text_bytes(src, chunk) == len(data) - len(binary), chunk


def test_estimated_pages_from_text_size(tmp_path):
    txt = write(tmp_path, "txt", "x" * (ebook_convert.BYTES_PER_PAGE * 10))
    assert estimated_pages(txt, "txt") == 10
    assert 0 < estimated_pages(EBOOK / "book.epub", "epub") < 20
    assert 0 < estimated_pages(EBOOK / "book.mobi", "mobi") < 20
    assert 0 < estimated_pages(EBOOK / "book.fb2", "fb2") < 20
    assert 0 < estimated_pages(CORPUS / "office" / "report_20p.docx", "docx") < 200


def test_pdf_page_cap_follows_the_memory_limit():
    assert pdf_page_cap(2000, None) == 2000
    assert 250 <= pdf_page_cap(2000, 1024) <= 300  # FILEX_MEM_EBOOK=1g
    assert pdf_page_cap(2000, 4096) > 1000
    assert pdf_page_cap(100, 4096) == 100
    assert pdf_page_cap(2000, 200) == 0


def test_memory_limit_reads_cgroup_v2_and_v1(tmp_path):
    v2, v1 = tmp_path / "memory.max", tmp_path / "limit_in_bytes"
    v2.write_text("1073741824\n")
    assert memory_limit_mb((v2, v1)) == 1024
    v2.write_text("max\n")
    assert memory_limit_mb((v2, v1)) is None
    v2.unlink()
    v1.write_text(str(2**63 - 4096))
    assert memory_limit_mb((v2, v1)) is None
    assert memory_limit_mb((tmp_path / "none",)) is None


@pytest.mark.parametrize(("container_mb", "pages", "admin_hint"), [(None, 250, False),
                         (4096, 250, False), (600, 150, True)])  # fmt: skip
def test_a_long_book_is_refused_for_pdf_before_calibre(
    step_ctx, tmp_path, monkeypatch, container_mb, pages, admin_hint
):
    """The cap follows the smaller of RLIMIT_DATA (MEMORY_MB) and the container's limit; more
    container memory does not lift it, so the admin hint shows only when the container binds."""
    monkeypatch.setattr(ebook_convert, "memory_limit_mb", lambda: container_mb)
    step_ctx.target = "pdf"
    src = write(tmp_path, "txt", "word " * (ebook_convert.BYTES_PER_PAGE * pages // 5))
    msg = fails("TOO_MANY_PAGES", run, step_ctx, src, ToolOptions())
    assert f"about {pages} pages" in msg and "EPUB" in msg
    assert ("FILEX_MEM_EBOOK" in msg) == admin_hint


def test_a_book_under_the_cap_is_not_refused_for_pdf(step_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(ebook_convert, "memory_limit_mb", lambda: None)
    called = []
    monkeypatch.setattr(ebook_convert._engine, "run", lambda *a, **k: called.append(a) or (1, ""))
    step_ctx.target = "pdf"
    src = write(tmp_path, "txt", "word " * (ebook_convert.BYTES_PER_PAGE * 200 // 5))
    fails("ENGINE_FAILED", run, step_ctx, src, ToolOptions())  # reached Calibre (stubbed)
    assert called


@pytest.mark.parametrize(
    ("log", "code"),
    [
        ("calibre.ebooks.DRMError: This file is locked with DRM", "DRM_PROTECTED"),
        ("MemoryError", "ENGINE_MEMORY"),
        ("Traceback ... ValueError: not an ebook", "ENGINE_FAILED"),
    ],
)
def test_failure_mapping(log, code):
    assert ebook_convert.failure(1, log, "It").code == code


# --- real conversions (worker-ebook image)


def sample(tmp_path: Path, fmt: str) -> Path:
    src_dir = tmp_path / "in"
    match fmt:
        case "docx":
            return scratch_copy(CORPUS / "office" / "report.docx", src_dir, fmt)
        case "pdf":
            return scratch_copy(CORPUS / "pdf" / "text_1p.pdf", src_dir, fmt)
        case "txt":
            return write(src_dir, "txt", f"Chapter 1\n\n{CYRILLIC}\n\nThe quick brown fox.\n")
        case "html":
            return write(src_dir, "html", f"<html><body><h1>Chapter</h1><p>{CYRILLIC}</p>"
                         "</body></html>")  # fmt: skip
        case _:
            return scratch_copy(EBOOK / f"book.{fmt}", src_dir, fmt)


def convert(step_ctx, src: Path, target: str) -> Path:
    step_ctx.target = target
    (out,) = run(step_ctx, src, ToolOptions())
    assert out.parent == step_ctx.out_dir and out.stat().st_size > 0
    return out


@needs_calibre
@pytest.mark.parametrize(("src_fmt", "dst"), sorted((e.src, e.dst) for e in EDGES))
def test_every_pair_converts(step_ctx, tmp_path, src_fmt, dst):
    out = convert(step_ctx, sample(tmp_path, src_fmt), dst)
    assert sniff(out, f"x.{dst}") == dst


@needs_calibre
def test_cyrillic_fb2_keeps_its_text_through_epub_to_pdf(step_ctx, tmp_path):
    src = write(tmp_path / "in", "fb2", FB2_CP1251.encode("cp1251"))
    epub = convert(step_ctx, src, "epub")
    assert CYRILLIC in book_text(epub)
    step_ctx.out_dir = tmp_path / "out2"
    step_ctx.out_dir.mkdir()
    pdf = convert(step_ctx, scratch_copy(epub, tmp_path / "in2", "epub"), "pdf")
    assert "французских" in book_text(pdf)


@needs_calibre
@pytest.mark.parametrize("dst", ["mobi", "azw3"])
def test_mobi_and_azw3_outputs_reopen_in_calibre(step_ctx, tmp_path, dst):
    out = convert(step_ctx, sample(tmp_path, "epub"), dst)
    step_ctx.out_dir = tmp_path / "back"
    step_ctx.out_dir.mkdir()
    txt = convert(step_ctx, scratch_copy(out, tmp_path / "in2", dst), "txt")
    assert "quick brown fox" in book_text(txt)


@needs_calibre
@pytest.mark.parametrize("dst", TARGETS)
def test_html_with_local_file_references_leaks_nothing(step_ctx, tmp_path, dst):
    out = convert(step_ctx, write(tmp_path / "in", "html", LFI_HTML), dst)
    assert not leaked(out)
    assert dst in ("mobi", "azw3") or "Visible text" in book_text(out) or dst == "docx"


@needs_calibre
def test_epub_referencing_host_files_leaks_nothing_into_pdf(step_ctx, tmp_path):
    xhtml = (
        b'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
        b'<head><title>x</title><link rel="stylesheet" href="file:///etc/passwd"/></head>'
        b'<body><p>Visible text.</p><img src="file:///etc/passwd"/>'
        b'<iframe src="file:///etc/passwd"></iframe></body></html>'
    )
    src = tmp_path / "in" / "0.epub"
    src.parent.mkdir()
    with zipfile.ZipFile(EBOOK / "book.epub") as zin, zipfile.ZipFile(src, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename.endswith(".xhtml") and b"Chapter 1" in data:
                data = xhtml
            zout.writestr(info, data)
    for dst in ("pdf", "docx", "txt"):
        step_ctx.out_dir = tmp_path / f"out-{dst}"
        step_ctx.out_dir.mkdir()
        assert not leaked(convert(step_ctx, src, dst))


@needs_calibre
def test_a_hung_calibre_is_killed_at_its_timeout(step_ctx, tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    script = fake / "ebook-convert"
    script.write_text("#!/bin/sh\nsleep 300 &\nexec sleep 300\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ebook_convert, "ENGINE_TIMEOUT_S", 1)
    step_ctx.target = "mobi"
    started = time.monotonic()
    assert "too long" in fails(
        "ENGINE_FAILED", run, step_ctx, sample(tmp_path, "epub"), ToolOptions()
    )
    assert time.monotonic() - started < 10
    time.sleep(0.3)
    assert not [p for p in _sleepers()], "the engine's process group survived"
    monkeypatch.undo()  # the same process keeps serving
    step_ctx.out_dir = tmp_path / "again"
    step_ctx.out_dir.mkdir()
    assert sniff(convert(step_ctx, sample(tmp_path / "2", "fb2"), "epub")) == "epub"


def _sleepers() -> list[int]:
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if cmd.startswith(b"sleep\0300"):
            found.append(int(pid))
    return found


def test_sources_and_targets_are_registered_formats():
    from filex.formats import FORMATS

    assert set(SOURCES) | set(TARGETS) <= set(FORMATS)
