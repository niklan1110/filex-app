"""Archive pre-check, safe unpack and safe XML (filex/tools/_archive.py, _xml.py; plan task 63).

Zip cases run everywhere (stdlib). RAR cases need libarchive (``libarchive-c`` plus the system
libarchive13), so they run in the worker-ebook image and are skipped elsewhere.
"""

import ast
import os
import zipfile
from pathlib import Path

import pytest

from filex.registry import StepError
from filex.tools import _archive
from filex.tools._archive import ArchiveLimits, check, safe_name, unpack
from filex.tools._xml import parse_xml

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "corpus"
RAR = Path(__file__).parent / "archive_fixtures"


def make_zip(path: Path, entries: dict[str, bytes], **attrs: int) -> Path:
    """A zip of ``entries``; ``attrs`` maps an entry name to its Unix mode (external_attr)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if name in attrs:
                info.external_attr = attrs[name] << 16
            zf.writestr(info, data)
    return path


def refused(fn, *args, **kwargs) -> str:
    with pytest.raises(StepError) as e:
        fn(*args, **kwargs)
    assert e.value.code == "INVALID_INPUT"
    return e.value.message


# --- names


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd",
        "\\\\server\\share\\x.jpg",
        "C:/Windows/x.jpg",
        "c:x.jpg",
        "../outside.txt",
        "a/../../b.jpg",
        "a\\..\\..\\b.jpg",
        "a\x00b.jpg",
        "a\nb.jpg",
        "a\x7fb.jpg",
        "a\x85b.jpg",
        "",
        "./",
    ],
)
def test_unsafe_names_are_rejected(raw):
    assert safe_name(raw) is None


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("page 001.jpg", "page 001.jpg"),
        ("a\\b\\c.jpg", "a/b/c.jpg"),
        ("./a//b.jpg", "a/b.jpg"),
        ("e\u0301.jpg", "\u00e9.jpg"),  # NFC
        ("dir/", "dir"),
        ("..hidden.jpg", "..hidden.jpg"),  # not a ".." part
    ],
)
def test_safe_names_are_normalised(raw, clean):
    assert safe_name(raw) == clean


# --- refusals (zip)


def test_corpus_traversal_cbz_is_refused_before_anything_is_written(tmp_path):
    dest = tmp_path / "x"
    msg = refused(unpack, CORPUS / "odd" / "zip_traversal.cbz", dest, '"c.cbz"')
    assert "unsafe name" in msg and '"c.cbz"' in msg
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "outside.txt").exists()


def test_more_entries_than_the_limit_are_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {f"{i}.txt": b"x" for i in range(4)})
    assert "more than 3 files" in refused(check, z, "It", limits=ArchiveLimits(entries=3))


def test_generated_many_entries_cbz_is_refused():
    path = CORPUS / "generated" / "many_entries.cbz"
    if not path.is_file():
        pytest.skip("run scripts/make_corpus.py --large")
    assert "more than 5000 files" in refused(check, path, "It")


def test_generated_high_ratio_epub_is_refused_from_its_headers():
    path = CORPUS / "generated" / "high_ratio.epub"
    if not path.is_file():
        pytest.skip("run scripts/make_corpus.py --large")
    assert "zip bomb" in refused(check, path, "It")


def test_declared_total_over_the_limit_is_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"a.bin": os.urandom(600), "b.bin": os.urandom(600)})
    assert "more than" in refused(check, z, "It", limits=ArchiveLimits(unpacked=1000))


def test_ratio_over_200_for_a_big_entry_is_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"zeros.bin": bytes(3 * 2**20)})  # ~1000:1
    assert "200:1" in refused(check, z, "It")


def test_high_ratio_small_entry_is_allowed(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"zeros.txt": bytes(2**19)})  # under the 1 MB floor
    assert [e.name for e in check(z, "It")] == ["zeros.txt"]


@pytest.mark.parametrize("mode", [0o120777, 0o020644, 0o060644, 0o010644, 0o140755])
def test_symlink_and_device_entries_are_refused(tmp_path, mode):
    z = make_zip(tmp_path / "a.zip", {"ok.txt": b"x", "link": b"/etc/passwd"}, link=mode)
    assert "link or device" in refused(check, z, "It")


def test_case_collision_is_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"Page.jpg": b"1", "page.JPG": b"2"})
    assert "same name" in refused(check, z, "It")


def test_nfc_collision_is_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"e\u0301.jpg": b"1", "\u00e9.jpg": b"2"})
    assert "same name" in refused(check, z, "It")


def test_encrypted_entry_is_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"a.txt": b"secret"})
    data = bytearray(z.read_bytes())
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 8] |= 1  # central directory general-purpose flag: encrypted
    z.write_bytes(bytes(data))
    assert "password-protected" in refused(check, z, "It")


@pytest.mark.parametrize(
    "inner",
    [
        b"PK\x03\x04" + bytes(40),
        b"Rar!\x1a\x07\x00" + bytes(40),
        b"7z\xbc\xaf\x27\x1c" + bytes(40),
        b"\x1f\x8b\x08" + bytes(40),
        bytes(257) + b"ustar\x0000" + bytes(250),
    ],
)
def test_nested_archives_are_refused_unless_allowed(tmp_path, inner):
    z = make_zip(tmp_path / "a.zip", {"inner.bin": inner})
    assert "another archive" in refused(check, z, "It")
    assert [e.name for e in check(z, "It", allow_nested=True)] == ["inner.bin"]


def test_lying_declared_size_is_refused(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"a.txt": b"hello world" * 100})
    data = bytearray(z.read_bytes())
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 24 : cd + 28] = (10).to_bytes(4, "little")  # uncompressed size: 10 bytes
    z.write_bytes(bytes(data))
    refused(check, z, "It")


def test_actual_bytes_over_the_limit_stop_in_the_middle_of_an_entry(tmp_path):
    """Declared sizes can be missing (RAR) or wrong; the copy loop counts what it reads."""
    limits = ArchiveLimits(unpacked=5 * _archive.CHUNK)
    budget = _archive._Budget("It", limits, None)
    endless = iter(lambda: os.urandom(_archive.CHUNK), b"")
    target = tmp_path / "out"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0))
    try:
        assert "more than" in refused(budget.pump, endless, fd, None, False)
    finally:
        os.close(fd)
    assert target.stat().st_size <= limits.unpacked


def test_actual_ratio_over_the_limit_stops_reading():
    budget = _archive._Budget("It", ArchiveLimits(), 1000)  # whole-archive ratio (RAR)
    zeros = iter(lambda: bytes(_archive.CHUNK), b"")
    assert "200:1" in refused(budget.pump, zeros, None, None, False)
    assert budget.total <= 2**20 + _archive.CHUNK


def test_damaged_zip_is_refused(tmp_path):
    good = (CORPUS / "ebook" / "comic.cbz").read_bytes()
    bad = tmp_path / "bad.cbz"
    bad.write_bytes(good[: len(good) // 2])
    refused(check, bad, "It")


def test_a_file_that_is_no_archive_is_refused():
    assert "not a zip or RAR" in refused(check, CORPUS / "pdf" / "text_10p.pdf", "It")


# --- normal files and unpacking


@pytest.mark.parametrize(
    ("rel", "nested"),
    [
        ("ebook/comic.cbz", False),
        ("ebook/book.epub", False),
        ("office/report.docx", True),
        ("office/budget.xlsx", True),
        ("office/slides.pptx", True),
        ("office/letter.odt", True),
    ],
)
def test_normal_corpus_archives_pass(rel, nested):
    assert check(CORPUS / rel, "It", allow_nested=nested)


def test_unpack_writes_generated_names_in_archive_order(tmp_path):
    dest = tmp_path / "pages"
    out = unpack(
        CORPUS / "ebook" / "comic.cbz", dest, "It", select=lambda e: e.name.endswith(".png")
    )
    assert [u.entry.name for u in out] == [f"page_00{i}.png" for i in range(1, 6)]
    assert [u.path.name for u in out] == [f"000{i}.png" for i in range(1, 6)]
    assert sorted(p.name for p in dest.iterdir()) == [u.path.name for u in out]
    with zipfile.ZipFile(CORPUS / "ebook" / "comic.cbz") as zf:
        assert out[0].path.read_bytes() == zf.read("page_001.png")


def test_unpack_keeps_only_plain_extensions(tmp_path):
    z = make_zip(
        tmp_path / "a.zip",
        {"A.JPG": b"1", "b.tar.gz!": b"2", "c.verylongext": b"3", "noext": b"4", "d/": b""},
    )
    out = unpack(z, tmp_path / "d", "It", allow_nested=True)
    assert [u.path.name for u in out] == ["0001.jpg", "0002", "0003", "0004"]


def test_unpack_refuses_a_directory_that_is_not_empty(tmp_path):
    (tmp_path / "planted").write_text("x")
    with pytest.raises(ValueError):
        unpack(CORPUS / "ebook" / "comic.cbz", tmp_path, "It")


def test_unpack_counts_selected_files_against_max_pages(tmp_path):
    with pytest.raises(StepError) as e:
        unpack(CORPUS / "ebook" / "comic.cbz", tmp_path / "d", "It", max_pages=4)
    assert e.value.code == "TOO_MANY_PAGES"
    assert not any((tmp_path / "d").iterdir())


def test_unpack_refusal_mid_archive_removes_the_partial_file(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"a.txt": b"ok", "b.zip": b"PK\x03\x04" + bytes(10)})
    dest = tmp_path / "d"
    refused(unpack, z, dest, "It")
    assert [p.name for p in dest.iterdir()] == ["0001.txt"]  # b.zip was never kept


# --- RAR (libarchive; worker-ebook image)


def _libarchive() -> bool:
    try:
        import libarchive  # noqa: F401
    except (ImportError, OSError, TypeError):  # no package, or no libarchive.so (Windows)
        return False
    return True


needs_libarchive = pytest.mark.skipif(not _libarchive(), reason="needs libarchive (worker-ebook)")


@needs_libarchive
def test_rar5_solid_archive_unpacks(tmp_path):
    out = unpack(RAR / "rar5_solid.rar", tmp_path / "d", "It")
    assert [u.entry.name for u in out] == ["test.bin"] + [f"test{i}.bin" for i in range(1, 7)]
    assert [u.path.stat().st_size for u in out] == [1200] + [4096] * 6


@needs_libarchive
def test_rar5_stored_archive_passes_the_check():
    assert [e.name for e in check(RAR / "rar5_stored.rar", "It")] == ["helloworld.txt"]


@needs_libarchive
@pytest.mark.parametrize("name", ["rar4_symlink.rar", "rar5_symlink.rar", "rar5_hardlink.rar"])
def test_rar_links_are_refused_from_the_listing(tmp_path, name):
    dest = tmp_path / "d"
    assert "link or device" in refused(unpack, RAR / name, dest, "It")
    assert list(dest.iterdir()) == []


@needs_libarchive
def test_rar_entry_limit_and_size_limit(tmp_path):
    path = RAR / "rar5_solid.rar"
    assert "more than 3 files" in refused(check, path, "It", limits=ArchiveLimits(entries=3))
    msg = refused(check, path, "It", limits=ArchiveLimits(unpacked=10_000))
    assert "more than" in msg


@needs_libarchive
def test_truncated_rar_is_refused(tmp_path):
    bad = tmp_path / "bad.rar"
    bad.write_bytes((RAR / "rar5_solid.rar").read_bytes()[:300])
    refused(unpack, bad, tmp_path / "d", "It")


# --- XML


XXE = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]>
<r>&x;</r>"""
LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>
<r>&b;</r>"""
XHTML = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hi</p></body></html>"""


@pytest.mark.parametrize("data", [XXE, LAUGHS], ids=["external-entity", "billion-laughs"])
def test_xml_entities_are_never_expanded(data):
    assert "entities" in refused(parse_xml, data, '"book.fb2"')


def test_xml_with_a_plain_doctype_parses():
    root = parse_xml(XHTML, "It")
    assert root.tag == "{http://www.w3.org/1999/xhtml}html"


def test_malformed_xml_is_invalid_input():
    assert "well-formed" in refused(parse_xml, b"<r><unclosed></r>", "It")


_XML_MODULES = ("xml.etree", "xml.dom", "xml.sax", "xml.parsers", "lxml")


def test_no_module_parses_xml_without_defusedxml():
    """Input XML goes through filex.tools._xml.parse_xml (review H2 fix 3)."""
    found = []
    for path in sorted((ROOT / "filex").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            if any(n == m or n.startswith(m + ".") for n in names for m in _XML_MODULES):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert found == [], "parse input XML with filex.tools._xml.parse_xml"
