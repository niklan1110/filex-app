"""pdf.compress (plan task 23). Preset mapping, Ghostscript arguments, the process wrapper and
the never-grow guard run everywhere; real compressions need ``gs`` (the worker-core image)."""

import os
import shutil
import sys
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF

from filex.registry import StepError
from filex.tools import _gs
from filex.tools.optimize import pdf_compress
from filex.tools.optimize.pdf_compress import (
    PRESETS,
    Options,
    Settings,
    gs_args,
    run,
    run_ghostscript,
    save_compact,
    search_target,
    settings_at,
    settings_for,
    smallest,
)

needs_gs = pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript not installed")


def compress(ctx, path, **options) -> Path:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return out


def fresh(ctx, tmp_path, name):
    ctx.out_dir = tmp_path / name
    ctx.out_dir.mkdir()
    return ctx


# --- presets and options (no engine)


def test_presets_get_stronger_from_low_to_extreme():
    levels = ["LOW", "MEDIUM", "HIGH", "EXTREME"]
    dpis = [PRESETS[x][0] for x in levels]
    qfactors = [PRESETS[x][1] for x in levels]
    assert dpis == sorted(dpis, reverse=True) and qfactors == sorted(qfactors)


def test_medium_is_the_default():
    assert settings_for(Options()) == Settings(dpi=100, qfactor=1.2, grayscale=False)


def test_dpi_cap_only_lowers_the_level_resolution():
    assert settings_for(Options(level="LOW", max_dpi=150)).dpi == 150
    assert settings_for(Options(level="EXTREME", max_dpi=150)).dpi == 72


def test_options_use_camel_case_and_reject_silly_values():
    assert Options.model_validate({"maxDpi": 96, "grayscale": True}).max_dpi == 96
    assert Options.model_validate({"targetMb": 2}).target_mb == 2
    for bad in (
        {"maxDpi": 10},
        {"maxDpi": 5000},
        {"level": "ULTRA"},
        {"targetMb": 0.01},
        {"targetMb": 5000},
    ):
        with pytest.raises(ValueError):
            Options.model_validate(bad)


# --- target-size search (no engine: a fake pass maps settings to a size)


def test_quality_scale_runs_through_the_presets():
    assert settings_at(1.0, None, False) == Settings(72, 2.0)
    for level in ["LOW", "MEDIUM", "HIGH"]:
        dpi, qfactor = PRESETS[level]
        t = next(t / 1000 for t in range(1001) if settings_at(t / 1000, None, False).dpi <= dpi)
        assert abs(settings_at(t, None, False).qfactor - qfactor) < 0.1, level


def test_quality_scale_keeps_the_dpi_cap_and_grayscale():
    assert settings_at(0.0, 150, True) == Settings(150, 0.25, grayscale=True)


class FakePasses:
    """Sizes by quality position ``t`` (0 best, 1 smallest); records every pass."""

    def __init__(self, size_at, seconds=1.0, fail_after=None):
        self.size_at, self.seconds, self.fail_after = size_at, seconds, fail_after
        self.tried: list[Settings] = []
        self.discarded: list[Settings] = []
        self.now = 0.0

    def clock(self):
        return self.now

    def attempt(self, s: Settings, timeout_s: float) -> int:
        assert timeout_s > 0
        if self.fail_after is not None and len(self.tried) >= self.fail_after:
            raise StepError("ENGINE_FAILED", "x")
        self.tried.append(s)
        self.now += self.seconds
        return self.size_at(s)

    def search(self, target, **kw):
        kw.setdefault("budget_s", 240)
        return search_target(target, self.attempt, self.discarded.append, clock=self.clock, **kw)


def _monotonic(s: Settings) -> int:
    return int(1000 * s.dpi / s.qfactor)  # higher quality, larger file


def test_target_search_picks_the_largest_result_under_the_target():
    fake = FakePasses(_monotonic)
    found = fake.search(60_000)
    assert found.reached and found.size <= 60_000
    assert found.size == max(_monotonic(s) for s in fake.tried if _monotonic(s) <= 60_000)
    assert found.size > 0.9 * 60_000 or len(fake.tried) == 6
    assert len(fake.tried) <= 6


def test_target_search_tries_the_smallest_setting_first_and_stops_if_it_is_too_big():
    fake = FakePasses(_monotonic)
    found = fake.search(10_000)  # EXTREME gives 36 000
    assert not found.reached
    assert found.size == 36_000 and found.settings == Settings(72, 2.0)
    assert fake.tried == [Settings(72, 2.0)]


def test_target_search_stops_when_close_enough():
    fake = FakePasses(_monotonic)
    found = fake.search(37_000)  # EXTREME's 36 000 is within 10 %
    assert found.reached and fake.tried == [Settings(72, 2.0)]


def test_target_search_survives_sizes_that_do_not_fall_with_quality():
    def bumpy(s: Settings) -> int:  # Ghostscript can give larger files at QFactor ~1.0
        return _monotonic(s) * (3 if 0.9 < s.qfactor < 1.1 else 1)

    fake = FakePasses(bumpy)
    found = fake.search(150_000)
    assert found.reached and found.size <= 150_000
    assert found.size == max(bumpy(s) for s in fake.tried if bumpy(s) <= 150_000)
    assert len(fake.tried) <= 6


def test_target_search_discards_every_result_it_does_not_return():
    fake = FakePasses(_monotonic)
    found = fake.search(60_000)
    assert found.settings not in fake.discarded
    assert sorted(fake.discarded, key=str) == sorted(
        [s for s in fake.tried if s != found.settings], key=str
    )


def test_target_search_never_runs_more_than_six_passes():
    fake = FakePasses(lambda s: 1 if s.dpi == 72 else 10**9)  # only EXTREME fits
    found = fake.search(1000)
    assert found.reached and found.settings == Settings(72, 2.0)
    assert len(fake.tried) == 6


def test_target_search_stays_within_its_time_budget():
    fake = FakePasses(lambda s: 1 if s.dpi == 72 else 10**9, seconds=50)
    found = fake.search(1000, budget_s=180)
    assert found.reached
    assert fake.now <= 180
    assert len(fake.tried) == 2  # 50 s each: a third would risk running past 180 s


def test_target_search_keeps_its_result_when_a_later_pass_fails():
    fake = FakePasses(_monotonic, fail_after=1)
    found = fake.search(60_000)
    assert found.reached and found.settings == Settings(72, 2.0)


def test_target_search_reports_a_failing_first_pass():
    fake = FakePasses(_monotonic, fail_after=0)
    with pytest.raises(StepError):
        fake.search(60_000)


# --- Ghostscript command line (no engine)


def test_ghostscript_runs_sandboxed_with_explicit_files(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "gs.pdf"
    args = gs_args(Settings(150, 0.76), src, dst)
    assert args[:5] == ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET"]
    assert not any(a.startswith(("-dNOSAFER", "-dDELAYSAFER")) for a in args)
    assert f"--permit-file-read={src}" in args
    assert f"-sOutputFile={dst}" in args
    assert args[-2:] == ["-f", str(src)]  # the input is the last thing read
    assert "-dAutoRotatePages=/None" in args  # pages keep their orientation


def test_ghostscript_args_carry_the_settings(tmp_path):
    args = gs_args(Settings(110, 1.0, grayscale=True), tmp_path / "a", tmp_path / "b")
    assert "-dColorImageResolution=110" in args and "-dGrayImageResolution=110" in args
    assert "-dMonoImageResolution=220" in args
    assert "-sColorConversionStrategy=Gray" in args
    assert "/QFactor 1.00" in args[args.index("-c") + 1]
    assert "-sColorConversionStrategy=Gray" not in gs_args(Settings(110, 1.0), Path(), Path())


# --- process wrapper (a Python child stands in for Ghostscript)


def test_engine_failure_is_reported_without_engine_output():
    with pytest.raises(StepError) as e:
        run_ghostscript(
            [sys.executable, "-c", "import sys; sys.stderr.write('secret.pdf'); sys.exit(3)"],
            30,
            '"a.pdf"',
        )
    assert e.value.code == "ENGINE_FAILED"
    assert e.value.message == '"a.pdf" could not be compressed.'


def test_engine_is_killed_on_timeout():
    with pytest.raises(StepError) as e:
        run_ghostscript([sys.executable, "-c", "import time; time.sleep(30)"], 0.5, "The file")
    assert e.value.message == "The file took too long to compress."


def test_engine_gets_no_stdin():
    run_ghostscript([sys.executable, "-c", "import sys; assert sys.stdin.read() == ''"], 30, "x")


def test_engine_stays_in_the_step_session(monkeypatch):
    """Regression: a new session made the runner's sweep kill Ghostscript mid-step (ADR-0007
    item 2). Its own process group keeps the timeout kill; the Linux sweep test is in
    tests/integration/test_sandbox.py."""
    seen = {}
    real = _gs.subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(_gs.subprocess, "Popen", spy)
    run_ghostscript([sys.executable, "-c", "pass"], 30, "x")
    assert not seen.get("start_new_session")
    assert seen["process_group"] == (0 if os.name == "posix" else None)


# --- never grow (no engine: the Ghostscript pass is replaced)


def test_smallest_never_returns_a_candidate_that_is_not_smaller(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"x" * 20)
    assert smallest([b, a], 11) == a
    assert smallest([b, a], 10) is None
    assert smallest([], 10) is None


def _bloating_pass(s, src, work, what, timeout_s):
    out = work / "lossy.pdf"
    out.write_bytes(src.read_bytes() + b"%" * 100_000)
    return out


def test_input_comes_back_unchanged_when_nothing_is_smaller(step_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_compress, "ghostscript_pass", _bloating_pass)
    src = tmp_path / "compact.pdf"
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        save_compact(pdf, src, "x")
    out = compress(step_ctx, src)
    assert out.read_bytes() == src.read_bytes()
    assert [c for c, _ in step_ctx.warnings] == ["NOT_SMALLER"]
    assert "already compact" in step_ctx.warnings[0][1]


def test_lossless_rewrite_wins_when_ghostscript_bloats(step_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_compress, "ghostscript_pass", _bloating_pass)
    src = tmp_path / "loose.pdf"
    with pikepdf.open(PDF / "text_10p.pdf") as pdf:
        pdf.save(src, compress_streams=False)
    out = compress(step_ctx, src)
    assert out.stat().st_size < src.stat().st_size
    assert step_ctx.warnings == []


# --- target size in the tool (no engine: the Ghostscript pass is replaced)


def _sized_pass(calls):
    """A Ghostscript stand-in whose output has ``dpi`` KB, so EXTREME (72 dpi) gives 72 KB."""

    def fake(s, src, work, what, timeout_s):
        calls.append(s)
        out = work / "lossy.pdf"
        out.write_bytes(b"%PDF-" + b"x" * (s.dpi * 1000 - 5))
        return out

    return fake


def test_target_met_by_the_lossless_rewrite_needs_no_ghostscript(step_ctx, monkeypatch):
    def no_ghostscript(*args):
        raise AssertionError("the lossless rewrite is already under the target")

    monkeypatch.setattr(pdf_compress, "ghostscript_pass", no_ghostscript)
    out = compress(step_ctx, PDF / "text_10p.pdf", target_mb=1)
    assert out.stat().st_size <= 1_000_000
    assert "TARGET_NOT_REACHED" not in [c for c, _ in step_ctx.warnings]


def test_target_is_reached_with_the_best_quality_found(step_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_compress, "ghostscript_pass", _sized_pass(calls))
    out = compress(step_ctx, PDF / "scanned_3p.pdf", target_mb=0.15)
    assert 0.9 * 150_000 <= out.stat().st_size <= 150_000 or len(calls) == 6
    assert out.stat().st_size <= 150_000
    assert 1 < len(calls) <= 6
    assert step_ctx.warnings == []


def test_unreachable_target_gives_the_smallest_result_and_says_so(step_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_compress, "ghostscript_pass", _sized_pass(calls))
    out = compress(step_ctx, PDF / "scanned_3p.pdf", target_mb=0.05)
    assert out.stat().st_size == 72_000 and len(calls) == 1
    assert step_ctx.warnings == [
        ("TARGET_NOT_REACHED", "Best we could do: 72 KB (target 50 KB) for the file.")
    ]


def test_grayscale_target_always_runs_ghostscript(step_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_compress, "ghostscript_pass", _sized_pass(calls))
    compress(step_ctx, PDF / "text_10p.pdf", target_mb=1, grayscale=True)
    assert calls and all(s.grayscale for s in calls)


def test_size_text_is_stable():
    assert pdf_compress.size_text(2_000_000) == "2 MB"
    assert pdf_compress.size_text(2_400_000) == "2.4 MB"
    assert pdf_compress.size_text(2_041_000) == "2.04 MB"
    assert pdf_compress.size_text(72_000) == "72 KB"
    assert pdf_compress.size_text(300) == "1 KB"


# --- bad inputs fail before any engine runs


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "broken_truncated.pdf", "INVALID_PDF"),
        (CORPUS / "odd" / "empty.pdf", "INVALID_PDF"),
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
    ],
)
def test_unreadable_input_fails_with_a_clear_code(step_ctx, bad, code):
    with pytest.raises(StepError) as e:
        run(step_ctx, bad, Options())
    assert e.value.code == code
    assert list(step_ctx.out_dir.iterdir()) == []


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", Options())
    assert e.value.code == "TOO_MANY_PAGES"


# --- real compressions (Ghostscript)


@needs_gs
def test_medium_halves_the_scanned_sample(step_ctx):
    src = PDF / "scanned_3p.pdf"
    out = compress(step_ctx, src, level="MEDIUM")
    assert out.stat().st_size <= src.stat().st_size / 2
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 3
    assert step_ctx.warnings == []


@needs_gs
def test_stronger_levels_give_smaller_files(step_ctx, tmp_path):
    sizes = []
    for level in ["LOW", "MEDIUM", "HIGH", "EXTREME"]:
        sizes.append(
            compress(fresh(step_ctx, tmp_path, level), PDF / "scanned_3p.pdf", level=level)
            .stat()
            .st_size
        )
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] < (PDF / "scanned_3p.pdf").stat().st_size


@needs_gs
@pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH", "EXTREME"])
def test_text_only_sample_never_grows(step_ctx, level):
    src = PDF / "text_10p.pdf"
    assert compress(step_ctx, src, level=level).stat().st_size <= src.stat().st_size


@needs_gs
def test_text_stays_selectable(tmp_path):
    import pymupdf

    src = tmp_path / "in.pdf"
    shutil.copyfile(PDF / "text_10p.pdf", src)
    out = pdf_compress.ghostscript_pass(Settings(72, 1.5), src, tmp_path, "x", 60)
    with pymupdf.open(out) as doc:
        assert "page 3 of 10" in doc[2].get_text()


@needs_gs
def test_dpi_cap_downsamples_images(step_ctx):
    out = compress(step_ctx, PDF / "scanned_3p.pdf", level="LOW", max_dpi=75)
    with pikepdf.open(out) as pdf:
        (image,) = pdf.pages[0].get_images().values()
        assert image.Width < 1240 * 0.6  # 150 dpi source, capped at 75


@needs_gs
def test_grayscale_converts_color_images(step_ctx, tmp_path):
    import pymupdf
    from PIL import Image

    png = tmp_path / "red.png"
    Image.new("RGB", (600, 600), (200, 30, 30)).save(png)
    src = tmp_path / "color.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_image(pymupdf.Rect(50, 50, 550, 550), filename=str(png))
        doc.save(src)
    out = compress(step_ctx, src, grayscale=True)
    with pikepdf.open(out) as pdf:
        (image,) = pdf.pages[0].get_images().values()
        assert image.ColorSpace == "/DeviceGray"


@needs_gs
def test_target_size_on_the_scanned_sample(step_ctx, tmp_path):
    src = PDF / "scanned_3p.pdf"  # 0.45 MB; MEDIUM gives about 0.21 MB
    out = compress(step_ctx, src, target_mb=0.15)
    warned = dict(step_ctx.warnings)
    if "TARGET_NOT_REACHED" in warned:
        assert warned["TARGET_NOT_REACHED"].startswith("Best we could do: ")
    else:
        assert out.stat().st_size <= 150_000
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 3
    out2 = compress(fresh(step_ctx, tmp_path, "t2"), src, target_mb=2)  # already under it
    assert out2.stat().st_size < src.stat().st_size


@needs_gs
def test_owner_restricted_input_is_compressed_and_warns(step_ctx):
    out = compress(step_ctx, PDF / "encrypted_owner_only.pdf")
    with pikepdf.open(out) as pdf:
        assert not pdf.is_encrypted
    assert "RESTRICTIONS_REMOVED" in [c for c, _ in step_ctx.warnings]


def test_form_fields_are_never_traded_for_bytes(step_ctx, monkeypatch):
    def no_ghostscript(*args):
        raise AssertionError("Ghostscript would drop the form fields")

    monkeypatch.setattr(pdf_compress, "ghostscript_pass", no_ghostscript)
    out = compress(step_ctx, PDF / "forms.pdf", level="EXTREME")
    with pikepdf.open(out) as pdf, pikepdf.open(PDF / "forms.pdf") as src:
        assert len(pdf.acroform.fields) == len(src.acroform.fields) > 0
    assert "LOSSLESS_ONLY" in [c for c, _ in step_ctx.warnings]


@needs_gs
def test_ghostscript_pass_keeps_the_metadata(tmp_path):
    src = tmp_path / "in.pdf"
    shutil.copyfile(PDF / "metadata.pdf", src)
    out = pdf_compress.ghostscript_pass(Settings(72, 2.0), src, tmp_path, "x", 60)
    with pikepdf.open(out) as pdf, pikepdf.open(src) as orig:
        assert dict(pdf.docinfo) == dict(orig.docinfo)


@needs_gs
def test_repairable_xref_is_compressed_and_warns(step_ctx):
    out = compress(step_ctx, PDF / "broken_xref.pdf")
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 3
    assert step_ctx.warnings[0][0] == "INPUT_REPAIRED"


@needs_gs
def test_ghostscript_reads_no_host_or_other_job_files(step_ctx, tmp_path):
    """Ghostscript LFI case (review M-2): the compress pass gets the pikepdf rewrite, which keeps
    ``/F`` external streams, so Ghostscript must not resolve them."""
    from test_pdf_to_pdfa import _everything_in, _lfi_pdf

    other_job = tmp_path / "other-job" / "secret.txt"
    other_job.parent.mkdir()
    other_job.write_bytes(b"FILEX-LFI-OTHER-JOB-7f3a9c")
    host = Path("/etc/hostname").read_bytes().strip()
    assert host  # the container's id
    src = tmp_path / "lfi.pdf"
    _lfi_pdf(src, other_job)
    work = tmp_path / "work"
    work.mkdir()
    outputs = [pdf_compress.ghostscript_pass(Settings(72, 2.0), src, work, "x", 60)]
    try:
        outputs.append(compress(step_ctx, src, level="HIGH"))
    except StepError as e:
        assert e.code in ("ENGINE_FAILED", "INVALID_PDF")  # refusing is also safe
    for out in outputs:
        data = _everything_in(out)
        assert b"FILEX-LFI-OTHER-JOB" not in data
        assert host not in data
