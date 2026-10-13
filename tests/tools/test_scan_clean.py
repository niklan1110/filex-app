"""scan.clean (plan task 40). Blank-page detection and removal run everywhere (pypdfium2,
pikepdf); deskew, rotation, cleaning and OCR need OCRmyPDF, Tesseract and unpaper (the
worker-ocr image)."""

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pikepdf
import pytest
from conftest import PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.ocr import _ocrmypdf as om
from filex.tools.ocr import scan_clean
from filex.tools.ocr.scan_clean import Options, blank_pages, is_blank, run

needs_ocr = pytest.mark.skipif(
    shutil.which("tesseract") is None
    or shutil.which("unpaper") is None
    or importlib.util.find_spec("ocrmypdf") is None,
    reason="OCRmyPDF, Tesseract and unpaper not installed (worker-ocr image)",
)
BLANK_ONLY = {"deskew": False, "rotate_pages": False, "clean": False}
_spec = importlib.util.spec_from_file_location(
    "make_corpus", Path(__file__).resolve().parents[2] / "scripts" / "make_corpus.py"
)
make_corpus = sys.modules.setdefault("make_corpus", importlib.util.module_from_spec(_spec))
_spec.loader.exec_module(make_corpus)


def render(path: Path, dpi: int = 150) -> list:
    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        return [pdf[i].render(scale=dpi / 72, grayscale=True).to_pil() for i in range(len(pdf))]
    finally:
        pdf.close()


def skew(image) -> float:
    """Degrees to turn ``image`` counter-clockwise to make its text lines level, to 0.1 degree:
    the angle whose row profile of dark pixels is sharpest (projection profile)."""
    from PIL import Image, ImageStat

    g = image.convert("L")
    ink = g.resize((g.width // 2, g.height // 2)).point(lambda v: 255 if v < 128 else 0)
    best = max(
        range(-50, 51),
        key=lambda i: ImageStat.Stat(
            ink.rotate(i / 10, resample=Image.BILINEAR).resize((1, ink.height), Image.BOX)
        ).var[0],
    )
    return best / 10


def ink(image, margin: float = 0.05) -> int:
    g = image.convert("L")
    mx, my = int(g.width * margin), int(g.height * margin)
    return sum(g.crop((mx, my, g.width - mx, g.height - my)).histogram()[:128])


def text_of(path: Path) -> list[str]:
    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        return [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
    finally:
        pdf.close()


def clean(ctx, src, **options) -> Path:
    (out,) = run(ctx, Path(src), Options(**options))
    assert out.parent == ctx.out_dir
    return out


# --- options (no engine)


def test_defaults_do_the_basic_clean_up_without_ocr():
    o = Options()
    assert o.deskew and o.rotate_pages and o.clean and o.remove_blank and not o.ocr


def test_at_least_one_step_is_needed():
    with pytest.raises(ValidationError, match="at least one"):
        Options(remove_blank=False, **BLANK_ONLY)


def test_options_use_camel_case():
    assert not Options.model_validate({"removeBlank": False}).remove_blank


# --- blank pages (pypdfium2 + Pillow, no OCR engine)


def test_specks_and_grain_are_blank_but_a_line_of_text_is_not():
    from PIL import Image, ImageDraw

    page = Image.new("L", (827, 1169), 245)
    d = ImageDraw.Draw(page)
    for x, y in [(100, 200), (400, 700), (600, 1000)]:
        d.ellipse((x, y, x + 2, y + 2), fill=60)
    assert is_blank(page)
    d.text((100, 500), "Signed: Jane Example, 12 March", fill=20, font_size=20)
    assert not is_blank(page)


def test_dark_scanner_edges_do_not_make_a_page_look_full():
    from PIL import Image, ImageDraw

    page = Image.new("L", (827, 1169), 245)
    ImageDraw.Draw(page).rectangle((0, 0, 30, 1169), fill=10)  # inside the 5% margin
    assert is_blank(page)


def test_the_nearly_blank_corpus_page_is_found():
    assert blank_pages(PDF / "scanned_3p.pdf") == [1]
    assert blank_pages(PDF / "text_10p.pdf") == []


def test_blank_pages_are_removed_with_a_warning(step_ctx):
    out = clean(step_ctx, PDF / "scanned_3p.pdf", **BLANK_ONLY)
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2
    assert step_ctx.warnings == [
        ("BLANK_PAGES_REMOVED", "The file: removed 1 page that looked blank (page 2).")
    ]


def test_only_blank_pages_is_an_error(step_ctx, tmp_path):
    src = tmp_path / "blank.pdf"
    with pikepdf.open(PDF / "scanned_3p.pdf") as pdf:
        del pdf.pages[2]
        del pdf.pages[0]
        pdf.save(src)
    with pytest.raises(StepError) as e:
        clean(step_ctx, src, **BLANK_ONLY)
    assert e.value.code == "ALL_PAGES_BLANK"
    assert list(step_ctx.out_dir.iterdir()) == []


def test_blank_removal_alone_needs_no_ocr_engine(step_ctx, monkeypatch):
    def no_engine(*args, **kwargs):
        raise AssertionError("OCRmyPDF or Tesseract was called")

    monkeypatch.setattr(om, "installed_languages", no_engine)
    monkeypatch.setattr(om, "run_ocrmypdf", no_engine)
    clean(step_ctx, PDF / "text_10p.pdf", **BLANK_ONLY)
    assert step_ctx.warnings == []


def test_bad_input_fails_before_the_engine(step_ctx, monkeypatch):
    monkeypatch.setattr(om, "installed_languages", lambda: ["eng"])
    with pytest.raises(StepError) as e:
        clean(step_ctx, PDF / "encrypted_aes256.pdf")
    assert e.value.code == "ENCRYPTED_PDF"


def test_options_reach_the_engine(step_ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(om, "installed_languages", lambda: ["eng", "rus"])

    def fake_engine(args, cwd, timeout_s, what, verb):
        seen.extend(args)
        shutil.copyfile(args[-2], args[-1])
        return 0

    monkeypatch.setattr(scan_clean.om, "run_ocrmypdf", fake_engine)
    clean(step_ctx, PDF / "scanned_3p.pdf", ocr=True, languages="rus+eng", rotate_pages=False)
    assert {"--deskew", "--clean-final"} <= set(seen) and "--rotate-pages" not in seen
    assert seen[seen.index("--mode") + 1] == "skip"
    assert seen[seen.index("--language") + 1] == "rus+eng"
    assert seen[seen.index("--tesseract-timeout") + 1] == str(om.TESSERACT_TIMEOUT_S)


# --- real clean-up (OCRmyPDF + Tesseract + unpaper)


@needs_ocr
def test_skewed_scan_is_straightened_and_blank_page_removed(step_ctx):
    before = render(PDF / "scanned_3p.pdf")
    assert abs(skew(before[0]) + 2.0) <= 0.2  # the sample really is 2 degrees off
    out = clean(step_ctx, PDF / "scanned_3p.pdf")
    after = render(out)
    assert len(after) == 2
    assert abs(skew(after[0])) <= 0.5 and abs(skew(after[1])) <= 0.5
    assert [c for c, _ in step_ctx.warnings] == ["BLANK_PAGES_REMOVED"]
    # No content lost: about as much ink as before (deskew and speck removal change a little).
    for b, a in [(before[0], after[0]), (before[2], after[1])]:
        assert ink(a) >= 0.9 * ink(b)


@needs_ocr
def test_no_text_is_cropped(step_ctx):
    """With OCR on, the first and last line of the skewed page (and every line's last word)
    are still readable after the clean-up."""
    out = clean(step_ctx, PDF / "scanned_3p.pdf", ocr=True)
    page = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text_of(out)[0])}
    lines = make_corpus._sample_lines(40, 1)
    for line in (lines[0], lines[-1]):
        expected = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", line)}
        assert len(page & expected) >= 0.8 * len(expected), sorted(expected - page)
    last_words = {re.findall(r"[A-Za-z]{4,}", ln)[-1].lower() for ln in lines}
    assert len(page & last_words) >= 0.8 * len(last_words)


@needs_ocr
def test_without_ocr_no_text_is_added(step_ctx):
    out = clean(step_ctx, PDF / "scanned_3p.pdf")
    assert [t.strip() for t in text_of(out)] == ["", ""]


@needs_ocr
def test_black_scanner_border_is_removed_but_black_content_stays(step_ctx, tmp_path):
    from PIL import Image, ImageDraw

    w, h = 1240, 1754
    img = Image.new("L", (w, h), 250)
    d = ImageDraw.Draw(img)
    for i in range(30):
        d.text((150, 200 + i * 40), f"The quick brown fox jumps over the lazy dog {i}",
               fill=20, font_size=24)  # fmt: skip
    d.rectangle((150, 1450, 500, 1600), fill=0)  # a black photo inside the content
    d.rectangle((0, 0, 70, h), fill=0)  # scanner lid along the left edge
    d.rectangle((0, h - 60, w, h), fill=0)  # and the bottom
    src = tmp_path / "in.png"
    img.save(src, dpi=(150, 150))
    out = clean(step_ctx, src, deskew=False, rotate_pages=False, ocr=True)
    (page,) = render(out)
    assert page.crop((5, 100, 60, 1600)).getextrema()[0] > 128  # left border wiped
    assert page.crop((100, h - 50, w - 100, h - 5)).getextrema()[0] > 128  # bottom wiped
    assert page.crop((160, 1460, 490, 1590)).getextrema()[1] < 64  # the photo stays black
    assert text_of(out)[0].count("fox") >= 28  # every text line is still there


@needs_ocr
def test_upside_down_page_is_turned_upright(step_ctx, tmp_path):
    (straight,) = render(PDF / "scanned_3p.pdf")[2:3]
    src = tmp_path / "upside_down.png"
    straight.rotate(180).save(src, dpi=(150, 150))
    out = clean(step_ctx, src, ocr=True)
    (page,) = render(out)
    band = page.height // 10  # the text block starts near the top and ends well above the foot
    top = ink(page.crop((0, 0, page.width, band)), margin=0)
    foot = ink(page.crop((0, page.height - band, page.width, page.height)), margin=0)
    assert top > 1000 and foot < top / 10
    assert "quartz" in text_of(out)[0]


@needs_ocr
def test_pages_with_text_are_left_alone(step_ctx):
    out = clean(step_ctx, PDF / "text_10p.pdf")
    assert [c for c, _ in step_ctx.warnings] == ["TEXT_PAGES_SKIPPED"]
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 10 and not any(p.get_images() for p in pdf.pages)
