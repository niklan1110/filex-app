"""scripts/ci/check_imports.py: the runtime-dependency scan run on bare worker images."""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[2] / "scripts" / "ci" / "check_imports.py"
_spec = importlib.util.spec_from_file_location("check_imports", _PATH)
ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci)


@pytest.fixture(scope="module")
def reqs():
    return {g: ci.requirements(ci.group_entries(g)) for g in ("core", "office", "ocr", "ebook")}


def test_office_needs_helpers_imported_inside_functions(reqs):
    # The bug this exists for: _lo -> _ooxml -> _xml imports defusedxml inside parse_xml().
    assert "filex.tools._xml:parse_xml" in reqs["office"]["defusedxml"]
    assert "filex.tools.office.office_to_pdf:_check_pages" in reqs["office"]["pikepdf"]


def test_every_group_needs_the_worker_deps(reqs):
    for r in reqs.values():
        assert {"redis", "pydantic"} <= r.keys()
        assert not r.keys() & {"filex", "os", "json", "__future__"}  # stdlib and own code


def test_helpers_are_followed_only_as_far_as_used(reqs):
    # comic.to-pdf uses image_to_pdf._check, not run(), which needs pillow_heif (core only).
    assert "pillow_heif" in reqs["core"]
    assert "pillow_heif" not in reqs["ebook"]
    # office uses _pdf.label/parse_ranges, never the pdfium or MuPDF openers.
    assert not reqs["office"].keys() & {"pypdfium2", "pymupdf"}


def test_missing_reports_import_failures():
    bad = ci.missing({"json": {"x"}, "filex_no_such_module": {"y"}})
    assert list(bad) == ["filex_no_such_module"]
    assert "ModuleNotFoundError" in bad["filex_no_such_module"]


def test_main_fails_on_unknown_group():
    with pytest.raises(SystemExit, match="no tools"):
        ci.main(["nosuchgroup"])


def test_api_group_scans_the_api_not_the_engines():
    r = ci.requirements(ci.group_entries("api"))
    assert {"fastapi", "python_multipart", "redis"} <= r.keys()
    # Engines are imported inside the tools' run(), which never runs in the API image.
    assert not r.keys() & {"pikepdf", "pymupdf", "pypdfium2", "reportlab"}
