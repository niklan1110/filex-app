"""office.to-pdf (plan task 34).

The pure parts (filters, options, error mapping, the run's file handling with a fake soffice)
run everywhere. Real conversions, the macro and local-file-inclusion cases (review H2) and the
timeout kill need LibreOffice and run in the worker-office image (``needs_lo``).
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS
from office_samples import (
    linked_docx,
    linked_ods,
    linked_odt,
    macro_odt,
    needs_lo,
    pdf_image_count,
    pdf_text,
    plain_ods,
    soffice_processes,
    tiny_png,
    webservice_xlsx,
)
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.office import _lo
from filex.tools.office.office_to_pdf import Options, export_options, run

OFFICE = CORPUS / "office"


# --- pure logic


def test_default_options_add_no_filter_options():
    assert export_options(Options()) == {}
    assert _lo.pdf_filter("docx", {}) == "pdf:writer_pdf_Export"


def test_options_become_typed_json_filter_options():
    opts = Options(pdfa="2b", tagged=True, pages="8-, 1-3, 5, -2")
    assert export_options(opts) == {
        "SelectPdfVersion": 2,
        "UseTaggedPDF": True,
        "PageRange": "8-,1-3,5,1-2",
    }
    name, _, options = _lo.pdf_filter("xlsx", export_options(opts)).partition("{")
    assert name == "pdf:calc_pdf_Export:"
    assert json.loads("{" + options) == {
        "SelectPdfVersion": {"type": "long", "value": "2"},
        "UseTaggedPDF": {"type": "boolean", "value": "true"},
        "PageRange": {"type": "string", "value": "8-,1-3,5,1-2"},
    }


@pytest.mark.parametrize(
    ("fmt", "export"),
    [("odt", "writer_pdf_Export"), ("ods", "calc_pdf_Export"), ("pptx", "impress_pdf_Export")],
)
def test_the_export_filter_follows_the_input_family(fmt, export):
    assert _lo.pdf_filter(fmt, {}) == f"pdf:{export}"


def test_bad_options_are_rejected():
    with pytest.raises(ValidationError):
        Options(pdfa="4")
    with pytest.raises(ValidationError):
        Options(pages="5-3")


def test_the_command_forces_the_input_filter_and_a_private_profile(tmp_path):
    src = tmp_path / "0.docx"
    src.write_bytes(b"x")
    profile, out = tmp_path / "work" / "profile", tmp_path / "work" / "out"
    args = _lo.command(src, "docx", "pdf:writer_pdf_Export", profile, out)
    assert args[0] == "soffice"
    assert {"--headless", "--norestore", "--nolockcheck", "--nodefault"} <= set(args)
    assert f"-env:UserInstallation={profile.as_uri()}" in args
    assert "--infilter=MS Word 2007 XML" in args
    assert not any(a.startswith("--accept") for a in args)
    assert args[-3:] == ["--outdir", str(out), str(src)]


@pytest.mark.parametrize(
    ("log", "page_range", "code"),
    [
        ("Error: source file could not be loaded", False, "INVALID_INPUT"),
        (
            "Error: Please verify input parameters... (SfxBaseModel::impl_store",
            True,
            "INVALID_OPTIONS",
        ),
        (
            "Error: Please verify input parameters... (SfxBaseModel::impl_store",
            False,
            "ENGINE_FAILED",
        ),
        ("Segmentation fault", False, "ENGINE_FAILED"),
    ],
)
def test_failures_map_to_our_own_codes_and_messages(log, page_range, code):
    e = _lo.failure(log, '"a.docx"', page_range)
    assert e.code == code
    assert e.message.startswith('"a.docx"')
    assert "impl_store" not in e.message and "Error" not in e.message


def test_the_runner_named_suffix_decides_the_format(tmp_path):
    assert _lo.format_of(Path("0.xlsx")) == "xlsx"
    with pytest.raises(StepError) as e:
        _lo.format_of(Path("0.pdf"))
    assert e.value.code == "INVALID_INPUT"


def _blank_pdf(tmp_path: Path, pages: int) -> bytes:
    path = tmp_path / f"blank{pages}.pdf"
    with pikepdf.new() as pdf:
        for _ in range(pages):
            pdf.add_blank_page()
        pdf.save(path)
    return path.read_bytes()


@pytest.fixture
def fake_lo(tmp_path, monkeypatch):
    """A profile template and a fake soffice that writes ``result`` (or nothing) and logs."""
    template = tmp_path / "template"
    (template / "user").mkdir(parents=True)
    (template / "user" / "registrymodifications.xcu").write_text("<oor:items/>")
    monkeypatch.setattr(_lo, "TEMPLATE", template)
    work_root = tmp_path / "scratch"
    work_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(work_root))
    calls = []

    def fake(args, work, timeout_s, what):
        calls.append(args)
        assert work.parent == work_root  # a private work dir under TMPDIR
        assert f"-env:UserInstallation={(work / 'profile').as_uri()}" in args
        assert (work / "profile" / "user" / "registrymodifications.xcu").is_file()  # a copy
        out = Path(args[args.index("--outdir") + 1])
        if fake.result is not None:
            (out / (Path(args[-1]).stem + ".pdf")).write_bytes(fake.result)
        return fake.log

    fake.result, fake.log, fake.calls, fake.root = _blank_pdf(tmp_path, 2), "", calls, work_root
    monkeypatch.setattr(_lo, "run_soffice", fake)
    return fake


def test_run_moves_the_result_and_leaves_no_work_dir(step_ctx, fake_lo):
    (out,) = run(step_ctx, OFFICE / "report.docx", Options(tagged=True))
    assert out == step_ctx.out_dir / "converted.pdf"
    assert out.read_bytes() == fake_lo.result
    assert "--infilter=MS Word 2007 XML" in fake_lo.calls[0]
    assert '"UseTaggedPDF"' in fake_lo.calls[0][fake_lo.calls[0].index("--convert-to") + 1]
    assert list(fake_lo.root.iterdir()) == []


def test_no_output_is_an_error_and_cleans_up(step_ctx, fake_lo):
    fake_lo.result, fake_lo.log = None, "Error: source file could not be loaded\n"
    step_ctx.input_names = ["broken.docx"]
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "report.docx", Options())
    assert e.value.code == "INVALID_INPUT"
    assert e.value.message.startswith('"broken.docx" could not be opened')
    assert list(step_ctx.out_dir.iterdir()) == []
    assert list(fake_lo.root.iterdir()) == []


def test_an_archive_bomb_is_refused_before_libreoffice_runs(tmp_path, step_ctx, fake_lo):
    src = tmp_path / "1.docx"  # the runner names inputs after the sniffed format
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", bytes(3 * 2**20))  # ~1000:1
    with pytest.raises(StepError) as e:
        run(step_ctx, src, Options())
    assert e.value.code == "INVALID_INPUT" and "200:1" in e.value.message
    assert fake_lo.calls == []


def test_an_rtf_including_outside_files_is_refused_before_libreoffice_runs(
    tmp_path, step_ctx, fake_lo
):
    # LibreOffice follows INCLUDEPICTURE in RTF (task 64); rules: tests/unit/test_rtf_scan.py
    src = tmp_path / "1.rtf"
    src.write_bytes(rb'{\rtf1{\field{\*\fldinst INCLUDEPICTURE "../../jobs/x/in/1.png"}}}')
    with pytest.raises(StepError) as e:
        run(step_ctx, src, Options())
    assert e.value.code == "INVALID_INPUT" and "outside the document" in e.value.message
    assert fake_lo.calls == []


def test_an_empty_output_is_not_a_result(step_ctx, fake_lo):
    fake_lo.result = b""
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "report.docx", Options())
    assert e.value.code == "ENGINE_FAILED"


def test_an_unreadable_output_is_not_a_result(step_ctx, fake_lo):
    fake_lo.result = b"%PDF-1.7 not really"
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "report.docx", Options())
    assert e.value.code == "ENGINE_FAILED"
    assert list(step_ctx.out_dir.iterdir()) == []


def test_an_output_over_the_page_limit_is_refused_and_deleted(step_ctx, fake_lo, tmp_path):
    """Review L-2a: a small sheet with a huge print range."""
    fake_lo.result = _blank_pdf(tmp_path, 4)
    step_ctx.max_pages = 3
    step_ctx.input_names = ["huge-print-range.xlsx"]
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "budget.xlsx", Options())
    assert e.value.code == "TOO_MANY_PAGES"
    assert '"huge-print-range.xlsx"' in e.value.message and "4 pages" in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


# --- real LibreOffice (worker-office image)


def _pdf(ctx, src: Path, **options) -> Path:
    (out,) = run(ctx, src, Options(**options))
    assert out.read_bytes().startswith(b"%PDF-")
    return out


@needs_lo
@pytest.mark.parametrize(
    "name", ["report.docx", "budget.xlsx", "slides.pptx", "letter.odt", "legacy.xls"]
)
def test_office_samples_convert(step_ctx, name):
    import pikepdf

    with pikepdf.open(_pdf(step_ctx, OFFICE / name)) as pdf:
        assert len(pdf.pages) >= 1


@needs_lo
def test_slides_keep_one_page_per_slide(step_ctx):
    import pikepdf

    with pikepdf.open(_pdf(step_ctx, OFFICE / "slides.pptx")) as pdf:
        assert len(pdf.pages) == 3


@needs_lo
def test_pdfa_and_tagged_options_reach_the_output(step_ctx):
    import pikepdf

    with pikepdf.open(_pdf(step_ctx, OFFICE / "letter.odt", pdfa="2b", tagged=True)) as pdf:
        meta = pdf.open_metadata()
        assert meta.get("pdfaid:part") == "2"
        assert meta.get("pdfaid:conformance") == "B"
        assert "/StructTreeRoot" in pdf.Root


@needs_lo
def test_page_range_in_the_order_written(step_ctx, tmp_path):
    import pymupdf

    whole = _pdf(step_ctx, OFFICE / "report_20p.docx")
    (tmp_path / "r").mkdir()
    step_ctx.out_dir = tmp_path / "r"
    part = _pdf(step_ctx, OFFICE / "report_20p.docx", pages="3, 1-2, 2")
    with pymupdf.open(whole) as a, pymupdf.open(part) as b:
        starts = [p.get_text()[:80] for p in b]  # whole pages differ in layout details
        assert starts == [a[i].get_text()[:80] for i in (2, 0, 1, 1)]


@needs_lo
def test_a_range_past_the_end_selects_nothing(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "letter.odt", Options(pages="500-"))
    assert e.value.code == "INVALID_OPTIONS"
    assert list(step_ctx.out_dir.iterdir()) == []


@needs_lo
def test_a_damaged_file_is_invalid_input(step_ctx, tmp_path):
    bad = tmp_path / "0.docx"
    bad.write_bytes((OFFICE / "report.docx").read_bytes()[:3000])
    with pytest.raises(StepError) as e:
        run(step_ctx, bad, Options())
    assert e.value.code == "INVALID_INPUT"
    assert soffice_processes() == []


@needs_lo
def test_a_hanging_conversion_is_killed_at_the_timeout(step_ctx, monkeypatch):
    monkeypatch.setattr("filex.tools.office.office_to_pdf.TIMEOUT_S", 0.5)
    with pytest.raises(StepError) as e:
        run(step_ctx, OFFICE / "report_20p.docx", Options())
    assert e.value.code == "ENGINE_FAILED"
    assert "took too long" in e.value.message
    assert soffice_processes() == []  # soffice.bin too, not just the soffice wrapper
    assert list(step_ctx.out_dir.iterdir()) == []


@needs_lo
def test_a_macro_in_the_document_does_not_run(step_ctx, tmp_path):
    marker = tmp_path / "macro-ran"
    out = _pdf(step_ctx, macro_odt(tmp_path / "0.odt", marker))
    assert not marker.exists()
    text = pdf_text(out)
    assert "MACRO RAN" not in text and "Plain text before any macro." in text


@pytest.fixture
def other_job(tmp_path):
    """Files outside the step's work dir, standing in for another job's files."""
    d = tmp_path / "other-job"
    d.mkdir()
    secret = d / "secret.txt"
    secret.write_text("FILEX-SECRET-7f3a9c\n")
    return {
        "secret": secret,
        "image": tiny_png(d / "secret.png"),
        "ods": plain_ods(d / "secret.ods", "FILEX-SECRET-7f3a9c"),
        "hostname": Path("/etc/hostname").read_text().strip(),
    }


def _leaks(text: str, other_job) -> list[str]:
    return [s for s in ("FILEX-SECRET", other_job["hostname"]) if s and s in text]


@pytest.mark.parametrize("make", ["odt", "ods"])
def test_hostile_odf_samples_ask_for_automatic_link_updates(tmp_path, other_job_files, make):
    """The document setting overrides the global "never", so the LFI samples must carry it."""
    secret, image, ods = other_job_files
    if make == "odt":
        src = linked_odt(tmp_path / "0.odt", secret, image)
    else:
        src = linked_ods(tmp_path / "0.ods", secret, ods)
    with zipfile.ZipFile(src) as zf:
        settings = zf.read("settings.xml").decode()
    assert 'config:name="LinkUpdateMode" config:type="short">2<' in settings


@pytest.fixture
def other_job_files(tmp_path):
    d = tmp_path / "other"
    d.mkdir()
    (d / "secret.txt").write_text("x")
    return d / "secret.txt", tiny_png(d / "secret.png"), plain_ods(d / "secret.ods", "x")


@needs_lo
@pytest.mark.parametrize("kind", ["odt", "docx"])
def test_linked_files_and_images_are_not_pulled_in(step_ctx, tmp_path, other_job, kind):
    if kind == "odt":
        src = linked_odt(tmp_path / "0.odt", other_job["secret"], other_job["image"])
    else:  # refused before LibreOffice runs: links to local files (filex/tools/_ooxml.py)
        src = linked_docx(tmp_path / "0.docx", other_job["secret"], other_job["image"])
        with pytest.raises(StepError) as e:
            _pdf(step_ctx, src)
        assert e.value.code == "INVALID_INPUT"
        return
    out = _pdf(step_ctx, src)
    assert _leaks(pdf_text(out), other_job) == []
    assert pdf_image_count(out) == 0


@needs_lo
@pytest.mark.parametrize("kind", ["ods", "xlsx"])
def test_webservice_and_external_references_fetch_nothing(step_ctx, tmp_path, other_job, kind):
    if kind == "ods":
        src = linked_ods(tmp_path / "0.ods", other_job["secret"], other_job["ods"])
    else:
        src = webservice_xlsx(tmp_path / "0.xlsx", other_job["secret"])
    assert _leaks(pdf_text(_pdf(step_ctx, src)), other_job) == []


_SANDBOXED = """
import sys
from pathlib import Path
from filex.worker import sandbox
sandbox.apply_rlimits(memory_mb=1536 + 64, cpu_s=301, fsize_mb=1024, nofile=256, data=False)
sandbox.install_seccomp()
from filex.tools.office.office_to_pdf import Options, run

class Ctx:
    out_dir, aux, target, input_names, max_pages = Path(sys.argv[2]), {}, "pdf", [], 2000
    def progress(self, f, m=""): pass
    def warn(self, c, m): pass

print(run(Ctx(), Path(sys.argv[1]), Options())[0])
"""


@needs_lo
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp is Linux only")
def test_libreoffice_runs_under_the_step_sandbox(tmp_path):
    """The runner's limits for this tool and the seccomp filter (ADR-0007 items 1 and 3)."""
    out = tmp_path / "out"
    out.mkdir()
    done = subprocess.run(
        [sys.executable, "-c", _SANDBOXED, str(OFFICE / "slides.pptx"), str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert Path(done.stdout.strip()).read_bytes().startswith(b"%PDF-")
