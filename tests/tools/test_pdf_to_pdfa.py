"""pdf.to-pdfa (plan task 27). Options, the Ghostscript command line, the PDF/A marker check and
the lossy-change warnings (with a fake Ghostscript) run everywhere; real conversions need ``gs``
(the worker-core image).

veraPDF is not in any image yet (spec A1: CI only), so real conversions are checked for the
structural markers: XMP pdfaid part and conformance, an sRGB GTS_PDFA1 OutputIntent, no
encryption, every font embedded, no transparency in PDF/A-1b. Where ``pikepdf.pdfa`` (pikepdf's
allowlist PDF/A validator, extra ``pikepdf[pdfa]``) is installed, outputs must pass it too.
"""

import shutil
from pathlib import Path, PurePosixPath

import pikepdf
import pytest
from conftest import PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools import _gs
from filex.tools.optimize import pdf_to_pdfa
from filex.tools.optimize.pdf_to_pdfa import (
    Options,
    gs_args,
    has_transparency,
    pdfa_def,
    pdfa_problems,
    run,
    unembedded_fonts,
)

needs_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")


def convert(ctx, path, **options) -> Path:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return out


def codes(ctx) -> list[str]:
    return [c for c, _ in ctx.warnings]


def mark_pdfa(pdf: pikepdf.Pdf, part: str) -> None:
    """Give ``pdf`` the PDF/A markers Ghostscript writes."""
    icc = pdf.make_stream(b"\0" * 128, N=3)
    pdf.Root.OutputIntents = pikepdf.Array(
        [
            pikepdf.Dictionary(
                Type=pikepdf.Name.OutputIntent,
                S=pikepdf.Name.GTS_PDFA1,
                DestOutputProfile=icc,
                OutputConditionIdentifier="sRGB",
            )
        ]
    )
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["pdfaid:part"] = part
        meta["pdfaid:conformance"] = "B"


# --- options and command line (no engine)


def test_2b_is_the_default_and_only_b_levels_exist():
    assert Options().level == "2b"
    assert Options.model_validate({"level": "3b"}).level == "3b"
    for bad in ({"level": "2u"}, {"level": "4"}, {"level": "1a"}, {"pdfa": "2b"}):
        with pytest.raises(ValidationError):
            Options.model_validate(bad)


@pytest.mark.parametrize(("level", "part"), [("1b", "1"), ("2b", "2"), ("3b", "3")])
def test_ghostscript_args_select_the_part(tmp_path, level, part):
    args = gs_args(level, tmp_path / "in.pdf", tmp_path / "gs.pdf", Path("/icc/srgb.icc"))
    assert f"-dPDFA={part}" in args
    assert "-dPDFACompatibilityPolicy=1" in args  # drop what the level forbids, stay PDF/A
    assert "-sColorConversionStrategy=RGB" in args


def test_ghostscript_reads_only_the_input_and_the_profile(tmp_path):
    src, dst, icc = tmp_path / "in.pdf", tmp_path / "gs.pdf", Path("/icc/srgb.icc")
    args = gs_args("2b", src, dst, icc)
    assert args[:5] == ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET"]
    assert [a for a in args if a.startswith("--permit-file-read=")] == [
        f"--permit-file-read={src}",
        f"--permit-file-read={icc}",
    ]
    assert not any(a.startswith(("-dNOSAFER", "-dDELAYSAFER")) for a in args)
    assert f"-sOutputFile={dst}" in args and args[-2:] == ["-f", str(src)]


def test_output_intent_profile_path_is_a_safe_postscript_string():
    code = pdfa_def(PurePosixPath("/odd (dir)\\x/srgb.icc"))
    assert r"(/odd \(dir\)\\x/srgb.icc) (r) file" in code
    assert "/S /GTS_PDFA1" in code and "/N 3" in code


# --- marker check and inspection (no engine)


def test_plain_pdf_lacks_every_marker():
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        problems = pdfa_problems(pdf, "2b")
    assert len(problems) == 3
    assert any("pdfaid" in p for p in problems)
    assert any("OutputIntent" in p for p in problems)
    assert "fonts not embedded: Helvetica" in problems


def test_marked_pdf_without_fonts_passes_the_check():
    with pikepdf.open(PDF / "scanned_3p.pdf") as pdf:
        mark_pdfa(pdf, "2")
        assert pdfa_problems(pdf, "2b") == []
        assert pdfa_problems(pdf, "3b") == ["XMP pdfaid is '2B', not '3B'"]


def test_embedded_fonts_are_not_reported():
    with pikepdf.open(PDF / "pdfa_2b.pdf") as pdf:
        # the corpus file embeds Bitstream Vera; only PyMuPDF's unused Helvetica is bare
        assert unembedded_fonts(pdf) == {"Helvetica"}


def test_transparency_is_found(tmp_path):
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        assert not has_transparency(pdf)
        pdf.pages[0].Resources.ExtGState = pikepdf.Dictionary(GS9=pikepdf.Dictionary(ca=0.5))
        assert has_transparency(pdf)


# --- run with a fake Ghostscript


def _fake_gs(make):
    """A stand-in for Ghostscript: ``make(pdf)`` edits the cleaned input, which is saved as
    Ghostscript's output."""

    def fake(args, timeout_s, **messages):
        src, dst = Path(args[-1]), next(a for a in args if a.startswith("-sOutputFile="))[13:]
        with pikepdf.open(src) as pdf:
            make(pdf)
            pdf.save(dst)

    return fake


def test_lossy_changes_are_warned(step_ctx, monkeypatch):
    def drop_form(pdf):
        del pdf.Root.AcroForm
        for obj in pdf.objects:  # pretend Ghostscript embedded the fonts
            if isinstance(obj, pikepdf.Dictionary) and obj.get("/Type") == "/Font":
                obj.FontDescriptor = pikepdf.Dictionary(FontFile2=pdf.make_stream(b"x"))
        mark_pdfa(pdf, "2")

    monkeypatch.setattr(_gs, "run_ghostscript", _fake_gs(drop_form))
    monkeypatch.setattr(pdf_to_pdfa, "icc_profile", lambda: Path("/icc/srgb.icc"))
    step_ctx.input_names = ["form.pdf"]
    convert(step_ctx, PDF / "forms.pdf")
    assert codes(step_ctx) == ["FONTS_SUBSTITUTED", "FORM_FIELDS_REMOVED"]
    assert "(Helvetica)" in step_ctx.warnings[0][1]
    assert step_ctx.warnings[1][1].startswith('"form.pdf" had fillable form fields')


def test_output_that_is_not_pdfa_fails_the_job(step_ctx, monkeypatch):
    monkeypatch.setattr(_gs, "run_ghostscript", _fake_gs(lambda pdf: None))
    monkeypatch.setattr(pdf_to_pdfa, "icc_profile", lambda: Path("/icc/srgb.icc"))
    with pytest.raises(StepError) as e:
        convert(step_ctx, PDF / "text_1p.pdf", level="1b")
    assert (e.value.code, e.value.message) == (
        "ENGINE_FAILED",
        "The file could not be converted to valid PDF/A-1b.",
    )
    assert list(step_ctx.out_dir.iterdir()) == []


def test_signed_input_is_refused(step_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(_gs.subprocess, "Popen", None)  # never reached
    signed = tmp_path / "signed.pdf"
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(), SigFlags=3)
        pdf.save(signed)
    with pytest.raises(StepError) as e:
        convert(step_ctx, signed)
    assert e.value.code == "SIGNED_PDF"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "broken_truncated.pdf", "INVALID_PDF"),
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
    ],
)
def test_unreadable_input_fails_before_ghostscript(step_ctx, monkeypatch, bad, code):
    monkeypatch.setattr(_gs.subprocess, "Popen", None)
    with pytest.raises(StepError) as e:
        convert(step_ctx, bad)
    assert e.value.code == code


# --- real conversions (Ghostscript)


def _validator_report(path: Path, level: str):
    """pikepdf's PDF/A validator verdict, or None when ``pikepdf[pdfa]`` is not installed."""
    try:
        import pikepdf.pdfa
    except ImportError:
        return None
    return pikepdf.pdfa.validate_written(path, level)


@needs_gs
@pytest.mark.parametrize("level", ["1b", "2b", "3b"])
def test_text_sample_becomes_pdfa(step_ctx, level):
    out = convert(step_ctx, PDF / "text_10p.pdf", level=level)
    with pikepdf.open(out) as pdf:
        assert pdfa_problems(pdf, level) == []
        assert len(pdf.pages) == 10
        assert not pdf.is_encrypted
    assert codes(step_ctx) == ["FONTS_SUBSTITUTED"]  # Helvetica is not embedded in the input
    report = _validator_report(out, level)
    if report is not None:
        assert report.passed, [str(f) for f in report.findings][:10]


@needs_gs
def test_text_stays_selectable(step_ctx):
    import pymupdf

    out = convert(step_ctx, PDF / "text_10p.pdf")
    with pymupdf.open(out) as doc:
        assert "page 3 of 10" in doc[2].get_text()


@needs_gs
def test_scanned_sample_becomes_pdfa_without_warnings(step_ctx):
    out = convert(step_ctx, PDF / "scanned_3p.pdf", level="2b")
    with pikepdf.open(out) as pdf:
        assert pdfa_problems(pdf, "2b") == []
    assert step_ctx.warnings == []


@needs_gs
def test_transparency_is_flattened_for_1b(step_ctx, tmp_path):
    src = tmp_path / "see-through.pdf"
    with pikepdf.open(PDF / "scanned_3p.pdf") as pdf:
        page = pdf.pages[0]
        page.Resources.ExtGState = pikepdf.Dictionary(GS9=pikepdf.Dictionary(ca=0.5, CA=0.5))
        page.contents_add(pdf.make_stream(b"q /GS9 gs 1 0 0 rg 50 50 200 200 re f Q"))
        pdf.save(src)
    out = convert(step_ctx, src, level="1b")
    with pikepdf.open(out) as pdf:
        assert not has_transparency(pdf)
    assert "TRANSPARENCY_FLATTENED" in codes(step_ctx)


@needs_gs
def test_owner_restricted_input_is_converted(step_ctx):
    out = convert(step_ctx, PDF / "encrypted_owner_only.pdf")
    with pikepdf.open(out) as pdf:
        assert pdfa_problems(pdf, "2b") == []
    assert "RESTRICTIONS_REMOVED" in codes(step_ctx)


@needs_gs
def test_form_fields_lose_fillability_with_a_warning(step_ctx):
    out = convert(step_ctx, PDF / "forms.pdf")
    with pikepdf.open(out) as pdf:
        assert pdfa_problems(pdf, "2b") == []
        assert not pdf_to_pdfa.has_form(pdf)  # the fields are gone
    assert codes(step_ctx) == ["FONTS_SUBSTITUTED", "FORM_FIELDS_REMOVED"]


def _lfi_pdf(path: Path, secret: Path) -> None:
    """A page drawn from external-file streams (PDF ``/F`` on a stream): the content stream
    names ``/etc/hostname``, an image names another job's file (review H2 fix 4)."""
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        page = pdf.pages[0]
        external = pdf.make_stream(b"", F=pikepdf.String("/etc/hostname"))
        image = pdf.make_stream(
            b"",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=8,
            Height=1,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
            F=pikepdf.String(str(secret)),
        )
        page.Resources.XObject = pikepdf.Dictionary(Im9=image)
        page.contents_add(external)
        page.contents_add(pdf.make_stream(b"q 100 0 0 10 50 50 cm /Im9 Do Q"))
        pdf.save(path)


def _everything_in(path: Path) -> bytes:
    data = path.read_bytes()
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                try:
                    data += obj.read_bytes()
                except pikepdf.PdfError:
                    data += obj.read_raw_bytes()
    return data


@needs_gs
def test_ghostscript_reads_no_host_or_other_job_files(step_ctx, tmp_path):
    """Ghostscript LFI case (task 64): neither ``pdf.to-pdfa`` nor ``pdf.repair``'s
    Ghostscript rebuild may copy a referenced host file or another job's file into the output."""
    from filex.tools.optimize.pdf_repair import ghostscript_rebuild

    other_job = tmp_path / "other-job" / "secret.txt"
    other_job.parent.mkdir()
    other_job.write_bytes(b"FILEX-LFI-OTHER-JOB-7f3a9c")
    host = Path("/etc/hostname").read_bytes().strip()
    src = tmp_path / "lfi.pdf"
    _lfi_pdf(src, other_job)

    assert host  # the container's id
    rebuilt = tmp_path / "rebuilt.pdf"
    ghostscript_rebuild(step_ctx, src, rebuilt, "x")
    outputs = [rebuilt]
    for level in ("1b", "2b"):
        step_ctx.out_dir = tmp_path / level
        step_ctx.out_dir.mkdir()
        try:
            outputs.append(convert(step_ctx, src, level=level))
        except StepError as e:
            assert e.value.code in ("ENGINE_FAILED", "INVALID_PDF")  # refusing is also safe
    for out in outputs:
        data = _everything_in(out)
        assert b"FILEX-LFI-OTHER-JOB" not in data
        assert host not in data
