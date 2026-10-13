"""pdf.repair (plan task 27). The qpdf path, warning wording, the PostScript guard and the
fallback's file handling run everywhere; real Ghostscript rebuilds need ``gs`` (the
worker-core image)."""

import shutil
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF

from filex.registry import StepError, ToolOptions
from filex.tools import _gs
from filex.tools.optimize import pdf_repair
from filex.tools.optimize.pdf_repair import describe_fixes, gs_args, run

needs_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")


def repair(ctx, path) -> Path:
    (out,) = run(ctx, Path(path), ToolOptions())
    assert out.parent == ctx.out_dir
    return out


def codes(ctx) -> list[str]:
    return [c for c, _ in ctx.warnings]


def no_ghostscript(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Ghostscript must not run")

    monkeypatch.setattr(_gs.subprocess, "Popen", refuse)


# --- warning wording (no engine)


def test_qpdf_warnings_become_our_own_sentences():
    src = Path("/scratch/in.pdf")
    warnings = [
        f"{src}: file is damaged",
        f"{src} (offset 999999): xref not found",
        f"{src}: Attempting to reconstruct cross-reference table",
        f"{src} (object 7 0, offset 900): stream keyword followed by carriage return only",
        f"{src}: something new",
    ]
    assert describe_fixes(warnings, src) == [
        "the cross-reference table was rebuilt",
        "damaged data streams were fixed",
        "other damage was fixed",
    ]


def test_the_file_path_never_decides_the_wording():
    src = Path("/scratch/xref-stream-object.pdf")
    assert describe_fixes([f"{src}: unexpected token"], src) == ["other damage was fixed"]


# --- qpdf path (no Ghostscript)


def test_broken_xref_is_repaired_without_ghostscript(step_ctx, monkeypatch):
    no_ghostscript(monkeypatch)
    out = repair(step_ctx, PDF / "broken_xref.pdf")
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 3
        assert pdf.get_warnings() == []
    assert step_ctx.warnings == [
        (
            "INPUT_REPAIRED",
            "The file was damaged and has been repaired: the cross-reference table was rebuilt.",
        )
    ]


def test_message_names_the_upload_and_never_the_scratch_path(step_ctx, monkeypatch):
    no_ghostscript(monkeypatch)
    step_ctx.input_names = ["report.pdf"]
    repair(step_ctx, PDF / "broken_xref.pdf")
    (message,) = [m for _, m in step_ctx.warnings]
    assert message.startswith('"report.pdf" was damaged')
    assert "corpus" not in message and "offset" not in message


def test_clean_input_comes_back_as_a_clean_copy(step_ctx, monkeypatch):
    no_ghostscript(monkeypatch)
    out = repair(step_ctx, PDF / "text_10p.pdf")
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 10
    assert codes(step_ctx) == ["NOTHING_TO_REPAIR"]


def test_output_is_never_encrypted(step_ctx, monkeypatch):
    no_ghostscript(monkeypatch)
    out = repair(step_ctx, PDF / "encrypted_owner_only.pdf")
    with pikepdf.open(out) as pdf:
        assert not pdf.is_encrypted
    assert "RESTRICTIONS_REMOVED" in codes(step_ctx)


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
        (CORPUS / "odd" / "zip_named.pdf", "INVALID_PDF"),
        (CORPUS / "odd" / "empty.pdf", "INVALID_PDF"),
    ],
)
def test_inputs_that_are_not_repairable_pdfs_fail_before_ghostscript(
    step_ctx, monkeypatch, bad, code
):
    no_ghostscript(monkeypatch)
    with pytest.raises(StepError) as e:
        run(step_ctx, bad, ToolOptions())
    assert e.value.code == code
    assert list(step_ctx.out_dir.iterdir()) == []


def test_postscript_renamed_pdf_never_reaches_ghostscript(step_ctx, tmp_path, monkeypatch):
    no_ghostscript(monkeypatch)
    ps = tmp_path / "invoice.pdf"
    ps.write_bytes(b"%!PS-Adobe-3.0\n(/etc/passwd) (r) file 100 string readstring\n")
    with pytest.raises(StepError) as e:
        run(step_ctx, ps, ToolOptions())
    assert e.value.code == "INVALID_PDF"


def test_page_limit_applies(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", ToolOptions())
    assert e.value.code == "TOO_MANY_PAGES"


# --- Ghostscript fallback (a fake Ghostscript writes a known PDF)


def _fake_gs(result: Path):
    def fake(args, timeout_s, **messages):
        dst = next(a for a in args if a.startswith("-sOutputFile=")).split("=", 1)[1]
        shutil.copyfile(result, dst)

    return fake


def test_unreadable_input_falls_back_to_ghostscript_and_says_so(step_ctx, monkeypatch):
    monkeypatch.setattr(_gs, "run_ghostscript", _fake_gs(PDF / "text_1p.pdf"))
    out = repair(step_ctx, PDF / "broken_truncated.pdf")
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1 and not pdf.is_encrypted
    ((code, message),) = step_ctx.warnings
    assert code == "FULL_REBUILD"
    assert "1 page that could be read" in message and "check the result" in message


def test_ghostscript_failure_is_invalid_pdf(step_ctx, monkeypatch):
    def failing(args, timeout_s, *, failed, failed_code, **rest):
        raise StepError(failed_code, failed)

    monkeypatch.setattr(_gs, "run_ghostscript", failing)
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "broken_truncated.pdf", ToolOptions())
    assert (e.value.code, e.value.message) == ("INVALID_PDF", "The file is too damaged to repair.")


def test_ghostscript_output_without_pages_is_invalid_pdf(step_ctx, tmp_path, monkeypatch):
    empty = tmp_path / "empty.pdf"
    with pikepdf.new() as pdf:
        pdf.save(empty)
    monkeypatch.setattr(_gs, "run_ghostscript", _fake_gs(empty))
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "broken_truncated.pdf", ToolOptions())
    assert e.value.code == "INVALID_PDF"
    assert list(step_ctx.out_dir.iterdir()) == []


def test_ghostscript_runs_sandboxed_on_the_input_only(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "gs.pdf"
    args = gs_args(src, dst)
    assert args[:5] == ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET"]
    assert [a for a in args if a.startswith("--permit-file-read=")] == [f"--permit-file-read={src}"]
    assert not any(a.startswith(("-dNOSAFER", "-dDELAYSAFER")) for a in args)
    assert f"-sOutputFile={dst}" in args and args[-2:] == ["-f", str(src)]


# --- real Ghostscript


@needs_gs
def test_truncated_file_recovers_what_is_readable(step_ctx):
    """First 60% of a 3-page file: qpdf gives up, Ghostscript gets the pages it can read."""
    out = repair(step_ctx, PDF / "broken_truncated.pdf")
    with pikepdf.open(out) as pdf:
        assert 1 <= len(pdf.pages) <= 3
    assert codes(step_ctx) == ["FULL_REBUILD"]


@needs_gs
def test_ghostscript_rebuild_keeps_the_text(step_ctx, tmp_path):
    import pymupdf

    out = tmp_path / "rebuilt.pdf"
    assert pdf_repair.ghostscript_rebuild(step_ctx, PDF / "text_10p.pdf", out, "x") == 10
    with pymupdf.open(out) as doc:
        assert "page 3 of 10" in doc[2].get_text()
