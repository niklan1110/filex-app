"""ocr.pdf (plan task 39) and the shared OCRmyPDF helpers. Language rules, the command line,
error mapping, the process wrapper and input preparation run everywhere; real OCR runs need
OCRmyPDF and Tesseract (the worker-ocr image)."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import Report, StepError
from filex.tools.ocr import _ocrmypdf as om
from filex.tools.ocr.ocr_pdf import Options, run

needs_ocr = pytest.mark.skipif(
    shutil.which("tesseract") is None or importlib.util.find_spec("ocrmypdf") is None,
    reason="OCRmyPDF and Tesseract not installed (worker-ocr image)",
)
INSTALLED = ["deu", "eng", "fra", "ita", "rus", "spa"]
LOREM = (  # scripts/make_corpus.py: the words on the corpus scans
    "FileX test corpus. The quick brown fox jumps over the lazy dog. Pack my box with five dozen "
    "liquor jugs. Sphinx of black quartz, judge my vow. How vexingly quick daft zebras jump."
)


@pytest.fixture(autouse=True)
def _restore_pillow_limit(monkeypatch):
    """prepare() sets Pillow's global pixel limit; a step is a fresh process, a test is not."""
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)


def text_of(path: Path) -> list[str]:
    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        return [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
    finally:
        pdf.close()


def words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}


# --- languages (no engine)


@pytest.mark.parametrize(
    ("given", "stored"),
    [("eng", "eng"), ("eng+rus", "eng+rus"), (" ENG, rus ", "eng+rus"), ("chi_sim", "chi_sim")],
)
def test_languages_are_normalized(given, stored):
    assert Options(languages=given).languages == stored


@pytest.mark.parametrize(
    "bad", ["", "en", "eng+eng", "eng+rus+deu+fra+spa", "../eng", "eng;rm", "e1g"]
)
def test_bad_language_lists_are_rejected(bad):
    with pytest.raises(ValidationError):
        Options(languages=bad)


def test_languages_must_be_installed():
    assert om.check_languages("eng+rus", INSTALLED) == "eng+rus"
    with pytest.raises(StepError) as e:
        om.check_languages("eng+ukr", INSTALLED)
    assert e.value.code == "INVALID_OPTIONS"
    assert "ukr is not installed" in e.value.message and "rus" in e.value.message


def test_installed_languages_come_from_tesseract_without_osd(monkeypatch):
    listing = 'List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (4):\n'
    listing += "eng\nosd\nrus\nequ\n"

    def fake_run(args, **kwargs):
        assert args == ["tesseract", "--list-langs"] and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 30 and not kwargs.get("start_new_session")
        assert kwargs["process_group"] == (0 if os.name == "posix" else None)
        return subprocess.CompletedProcess(args, 0, listing, "")

    monkeypatch.setattr(om.subprocess, "run", fake_run)
    assert om.installed_languages() == ["eng", "rus"]


def test_missing_tesseract_is_an_engine_failure(monkeypatch):
    def fake_run(args, **kwargs):
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(om.subprocess, "run", fake_run)
    with pytest.raises(StepError) as e:
        om.installed_languages()
    assert e.value.code == "ENGINE_FAILED"


def test_unknown_language_fails_before_any_work(step_ctx, monkeypatch):
    monkeypatch.setattr(om, "installed_languages", lambda: INSTALLED)
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "scanned_3p.pdf", Options(languages="ukr"))
    assert e.value.code == "INVALID_OPTIONS"
    assert list(step_ctx.out_dir.iterdir()) == []


# --- options (no engine)


def test_defaults_skip_pages_with_text_and_make_a_plain_pdf():
    o = Options()
    assert (o.languages, o.mode, o.output, o.optimize) == ("eng", "skip", "pdf", "lossless")
    assert not (o.deskew or o.rotate_pages or o.clean or o.sidecar)


def test_options_use_camel_case():
    assert Options.model_validate({"rotatePages": True, "output": "pdfa"}).rotate_pages


def test_deskew_cannot_be_combined_with_redo():
    with pytest.raises(ValidationError, match="Straightening"):
        Options(mode="redo", deskew=True)
    Options(mode="force", deskew=True)


# --- OCRmyPDF command line (no engine)


def args_for(**kw):
    defaults = {"languages": "eng", "mode": "skip", "max_pixels": 100_000_000}
    return om.ocrmypdf_args(Path("in.pdf"), Path("out.pdf"), **(defaults | kw))


def test_command_line_carries_the_sandbox_settings():
    args = args_for()
    assert args[:5] == [sys.executable, "-P", "-s", "-m", "ocrmypdf"]
    pairs = dict(zip(args[5::2], args[6::2]))
    assert pairs["--jobs"] == "1"
    assert pairs["--rasterizer"] == "pypdfium"  # pages never go through Ghostscript
    assert pairs["--max-image-mpixels"] == "100" and pairs["--skip-big"] == "100"
    assert pairs["--max-ocr-image-mpixels"] == str(om.MAX_OCR_MPIXELS)
    assert pairs["--tesseract-timeout"] == str(om.TESSERACT_TIMEOUT_S)
    assert args[-2:] == ["in.pdf", "out.pdf"]
    assert not any("jbig2" in a for a in args)


def test_command_line_follows_the_options():
    args = args_for(
        mode="redo", output_type="pdfa", optimize=3, rotate_pages=True, clean="ocr",
        sidecar=Path("t.txt"), languages="eng+rus", max_pixels=50_000_000,
    )  # fmt: skip
    pairs = dict(zip(args[5::2], args[6::2]))
    assert pairs["--mode"] == "redo" and pairs["--output-type"] == "pdfa"
    assert pairs["--optimize"] == "3" and pairs["--language"] == "eng+rus"
    assert pairs["--max-image-mpixels"] == "50"
    assert "--rotate-pages" in args and "--clean" in args and "--deskew" not in args
    assert args[args.index("--rotate-pages-threshold") + 1] == str(om.ROTATE_THRESHOLD)
    assert args[args.index("--sidecar") + 1] == "t.txt"
    assert "--clean-final" not in args


def test_final_clean_removes_black_borders_but_keeps_the_conservative_preset():
    args = args_for(clean="final")
    preset = args[args.index("--unpaper-args") + 1].split()
    assert "--clean-final" in args
    assert "--no-blackfilter" not in preset
    assert {"--no-mask-center", "--no-border-align", "--no-grayfilter"} <= set(preset)


def test_without_ocr_tesseract_reads_nothing():
    args = args_for(ocr=False, deskew=True)
    assert args[args.index("--tesseract-timeout") + 1] == "0"


# --- exit codes and the process wrapper (a Python child stands in for OCRmyPDF)


@pytest.mark.parametrize(
    ("code", "log", "expected"),
    [
        (8, "", "ENCRYPTED_PDF"),
        (2, "InputFileError", "INVALID_PDF"),
        (15, "PIL.Image.DecompressionBombError: ...", "INVALID_INPUT"),
        (15, "Traceback", "ENGINE_FAILED"),
        (7, "", "ENGINE_FAILED"),
    ],
)
def test_exit_codes_map_to_our_errors(code, log, expected):
    e = om.failure(code, log + " /tmp/secret.pdf", '"a.pdf"', "read")
    assert e.code == expected
    assert "secret" not in e.message and '"a.pdf"' in e.message


def test_engine_failure_is_reported_without_engine_output():
    child = [sys.executable, "-c", "import sys; sys.stderr.write('secret.pdf'); sys.exit(7)"]
    with pytest.raises(StepError) as e:
        om.run_ocrmypdf(child, Path.cwd(), 30, '"a.pdf"', "read")
    assert (e.value.code, e.value.message) == ("ENGINE_FAILED", '"a.pdf" could not be read.')


def test_pdfa_failure_still_counts_as_an_output():
    child = [sys.executable, "-c", "import sys; sys.exit(10)"]
    assert om.run_ocrmypdf(child, Path.cwd(), 30, "x", "read") == om.EXIT_PDFA_FAILED


def test_engine_is_killed_on_timeout():
    child = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(StepError) as e:
        om.run_ocrmypdf(child, Path.cwd(), 0.5, "The file", "read")
    assert e.value.message == "The file took too long to process."


def test_engine_gets_no_stdin_and_runs_in_the_work_dir(tmp_path):
    code = "import os, sys; assert sys.stdin.read() == ''; open('here', 'w').close()"
    om.run_ocrmypdf([sys.executable, "-c", code], tmp_path, 30, "x", "read")
    assert (tmp_path / "here").is_file()


def test_engine_stays_in_the_step_session(monkeypatch):
    """Own process group (timeout kills Tesseract too), never a new session (ADR-0007 item 2)."""
    seen = {}
    real = om.subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(om.subprocess, "Popen", spy)
    om.run_ocrmypdf([sys.executable, "-c", "pass"], Path.cwd(), 30, "x", "read")
    assert not seen.get("start_new_session") and "shell" not in seen
    assert seen["process_group"] == (0 if os.name == "posix" else None)


# --- input preparation (pikepdf, img2pdf, pypdfium2; no OCR engine)


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "broken_truncated.pdf", "INVALID_PDF"),
        (CORPUS / "odd" / "empty.pdf", "INVALID_PDF"),
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
    ],
)
def test_unreadable_pdf_fails_with_a_clear_code(step_ctx, tmp_path, bad, code):
    with pytest.raises(StepError) as e:
        om.prepare(step_ctx, bad, tmp_path, "x")
    assert e.value.code == code


def test_signed_pdf_is_refused(step_ctx, tmp_path):
    src = tmp_path / "signed.pdf"
    with pikepdf.open(PDF / "forms.pdf") as pdf:
        pdf.Root.AcroForm.SigFlags = 3
        pdf.save(src)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(StepError) as e:
        om.prepare(step_ctx, src, work, '"s.pdf"')
    assert e.value.code == "SIGNED_PDF"


def test_owner_restricted_pdf_is_unlocked_for_ocr(step_ctx, tmp_path):
    out = om.prepare(step_ctx, PDF / "encrypted_owner_only.pdf", tmp_path, "x")
    with pikepdf.open(out) as pdf:
        assert not pdf.is_encrypted
    assert out.read_bytes()[:5] == b"%PDF-"
    assert "RESTRICTIONS_REMOVED" in [c for c, _ in step_ctx.warnings]


def test_too_many_pages_is_refused(step_ctx, tmp_path):
    step_ctx.max_pages = 2
    with pytest.raises(StepError) as e:
        om.prepare(step_ctx, PDF / "scanned_3p.pdf", tmp_path, "x")
    assert e.value.code == "TOO_MANY_PAGES"


def test_every_tiff_frame_becomes_a_page(step_ctx, tmp_path):
    src = tmp_path / "in.tiff"
    shutil.copyfile(CORPUS / "image" / "scan_multipage_g4.tiff", src)
    out = om.prepare(step_ctx, src, tmp_path, "x")
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 3
        assert float(pdf.pages[0].mediabox[2]) == pytest.approx(8.27 * 72, rel=0.02)  # 150 dpi


def test_image_without_resolution_is_placed_at_300_dpi(step_ctx, tmp_path):
    from PIL import Image

    src = tmp_path / "in.png"
    Image.new("L", (600, 300), 255).save(src)
    with pikepdf.open(om.prepare(step_ctx, src, tmp_path, "x")) as pdf:
        assert float(pdf.pages[0].mediabox[2]) == pytest.approx(144)  # 600 px / 300 dpi * 72


def test_image_over_the_pixel_limit_is_refused(step_ctx, tmp_path):
    from PIL import Image

    step_ctx.max_pixels = 10_000
    src = tmp_path / "in.png"
    Image.new("L", (200, 100), 255).save(src)
    with pytest.raises(StepError) as e:
        om.prepare(step_ctx, src, tmp_path, "x")
    assert e.value.code == "INVALID_INPUT"


def test_unknown_file_type_is_refused(step_ctx, tmp_path):
    src = tmp_path / "in.docx"
    src.write_bytes(b"PK")
    with pytest.raises(StepError) as e:
        om.prepare(step_ctx, src, tmp_path, "x")
    assert e.value.code == "INVALID_INPUT"


def test_text_detection_matches_the_corpus():
    assert om.has_text(PDF / "text_10p.pdf") == [True] * 10
    assert om.has_text(PDF / "scanned_3p.pdf") == [False] * 3


# --- real OCR (OCRmyPDF + Tesseract)


def ocr(ctx, src, **options):
    outs = run(ctx, Path(src), Options(**options))
    assert all((o.path if isinstance(o, Report) else o).parent == ctx.out_dir for o in outs)
    return outs


@needs_ocr
def test_scanned_sample_becomes_searchable(step_ctx):
    (out,) = ocr(step_ctx, PDF / "scanned_3p.pdf")
    pages = text_of(out)
    assert len(pages) == 3
    expected = words(LOREM)
    for i in (0, 2):  # page 2 is nearly blank
        found = words(pages[i]) & expected
        assert len(found) >= 0.9 * len(expected), sorted(expected - found)
        assert "quartz" in pages[i] and "Sphinx" in pages[i]
    assert step_ctx.warnings == []


@needs_ocr
def test_pdf_with_text_is_skipped_by_default_with_a_warning(step_ctx):
    (out,) = ocr(step_ctx, PDF / "text_10p.pdf")
    assert [c for c, _ in step_ctx.warnings] == ["TEXT_PAGES_SKIPPED"]
    assert "(10 of 10)" in step_ctx.warnings[0][1]
    with pikepdf.open(out) as pdf:  # pages kept as they were: no image added
        assert not any(page.get_images() for page in pdf.pages)
    assert "page 3 of 10" in text_of(out)[2]


@needs_ocr
def test_sidecar_is_a_text_report(step_ctx):
    pdf, report = ocr(step_ctx, PDF / "scanned_3p.pdf", sidecar=True)
    assert isinstance(report, Report) and report.path.suffix == ".txt"
    assert "quartz" in report.path.read_text(encoding="utf-8")
    assert pdf.suffix == ".pdf"


@needs_ocr
def test_pdfa_output_declares_pdfa_2b(step_ctx):
    (out,) = ocr(step_ctx, PDF / "scanned_3p.pdf", output="pdfa")
    with pikepdf.open(out) as pdf:
        meta = pdf.open_metadata()
        assert (meta.get("pdfaid:part"), meta.get("pdfaid:conformance")) == ("2", "B")
    assert "PDFA_FAILED" not in [c for c, _ in step_ctx.warnings]


@needs_ocr
def test_image_input_becomes_a_searchable_pdf(step_ctx, tmp_path):
    src = tmp_path / "in.tiff"
    shutil.copyfile(CORPUS / "image" / "scan_multipage_g4.tiff", src)
    (out,) = ocr(step_ctx, src)
    pages = text_of(out)
    assert len(pages) == 3 and all(len(words(p) & words(LOREM)) >= 5 for p in pages)


@needs_ocr
def test_force_reads_a_text_pdf_again(step_ctx):
    (out,) = ocr(step_ctx, PDF / "text_1p.pdf", mode="force")
    assert step_ctx.warnings == []
    with pikepdf.open(out) as pdf:
        assert pdf.pages[0].get_images()  # the page became an image with a text layer
    assert "page" in text_of(out)[0].lower()


@needs_ocr
def test_redo_replaces_an_earlier_ocr_layer(step_ctx, tmp_path):
    (first,) = ocr(step_ctx, PDF / "scanned_3p.pdf")
    src = tmp_path / "ocred.pdf"
    shutil.move(first, src)
    (again,) = ocr(step_ctx, src, mode="redo")
    text = text_of(again)[0]
    assert text.count("quartz") == text_of(src)[0].count("quartz")  # replaced, not doubled


@needs_ocr
def test_cyrillic_scan_is_read_with_russian(step_ctx, tmp_path):
    """Generated here, not in the corpus: the corpus generator has no redistributable font with
    Cyrillic on every platform. Ghostscript brings the URW base35 fonts (AGPL with a font
    exception) into the worker-ocr image."""
    from PIL import Image, ImageDraw, ImageFont

    fonts = sorted(Path("/usr/share/fonts").rglob("NimbusSans-Regular.*"))
    if not fonts:
        pytest.skip("no URW Nimbus Sans font")
    phrase = "Съешь же ещё этих мягких французских булок да выпей чаю"
    img = Image.new("L", (2480, 600), 250)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(fonts[0]), 56)
    for row in range(3):
        draw.text((150, 100 + row * 150), phrase, fill=20, font=font)
    src = tmp_path / "ru.png"
    img.save(src, dpi=(300, 300))
    (out,) = ocr(step_ctx, src, languages="rus+eng")
    found = {w.lower() for w in re.findall(r"\w{4,}", text_of(out)[0])}
    expected = {w.lower() for w in re.findall(r"\w{4,}", phrase)}
    assert len(found & expected) >= 0.8 * len(expected), sorted(expected - found)
