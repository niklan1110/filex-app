"""djvu.to-pdf (plan task 52). Pure parts run everywhere; real conversions need djvulibre and run
in the worker-ebook image (``needs_djvu``). The planned OCR step is covered in
tests/api/test_api_plan.py."""

import shutil
import subprocess

import pytest
from ebook_samples import EBOOK, needs_djvu, pdf_text, scratch_copy, write

from filex.formats import sniff
from filex.registry import StepError
from filex.tools.ebook import djvu_to_pdf
from filex.tools.ebook.djvu_to_pdf import Options, parse_sexpr, plan, run, words

DJVU = EBOOK / "un_resolution_1837.djvu"

HIDDEN_TEXT = r"""(page 0 0 2550 3300
 (line 100 200 500 250
  (word 100 200 200 250 "Hello")
  (word 220 200 500 250 "W\303\266rld \"quoted\""))
 (line 0 0 10 10 (word 5 5 5 9 "empty-box"))
 (word 300 300 400 350 "Привет"))
"""


def test_sniffs_as_djvu():
    assert sniff(DJVU, DJVU.name) == "djvu"


def test_hidden_text_leaves_become_words_with_boxes():
    found = sorted(words(parse_sexpr(HIDDEN_TEXT)))
    assert found == [
        (100.0, 200.0, 200.0, 250.0, "Hello"),
        (220.0, 200.0, 500.0, 250.0, 'Wörld "quoted"'),
        (300.0, 300.0, 400.0, 350.0, "Привет"),
    ]  # the zero-width box is dropped


@pytest.mark.parametrize("bad", ['(page 0 0 1 1 "x"', "(page))", '(word 1 2 3 4 "x)'])
def test_damaged_hidden_text_raises(bad):
    with pytest.raises(ValueError):
        parse_sexpr(bad)


def test_plan_ocr_never_adds_nothing():
    p = plan(Options(ocr="never"), frozenset({"ocr.pdf"}))
    assert p.steps == () and p.warnings == ()


def test_plan_ocr_online_adds_ocr_in_skip_mode():
    (follow,) = plan(Options(), frozenset({"ocr.pdf"})).steps
    assert follow.tool == "ocr.pdf" and dict(follow.options) == {"mode": "skip"}


def test_plan_ocr_offline_warns():
    p = plan(Options(), frozenset({"djvu.to-pdf"}))
    assert p.steps == () and [c for c, _ in p.warnings] == ["OCR_SKIPPED"]


def test_a_non_djvu_input_is_refused(step_ctx, tmp_path):
    with pytest.raises(StepError) as e:
        run(step_ctx, write(tmp_path, "txt", "hello"), Options())
    assert e.value.code == "INVALID_INPUT"


# --- real conversions (worker-ebook image)


def convert(step_ctx, tmp_path, src=DJVU):
    step_ctx.target = "pdf"
    (out,) = run(step_ctx, scratch_copy(src, tmp_path / "in", "djvu"), Options())
    assert sniff(out) == "pdf"
    return out


def djvu_pages() -> int:
    done = subprocess.run(
        ["djvused", "-e", "n", str(DJVU)], capture_output=True, text=True, check=True
    )
    return int(done.stdout)


def djvu_text() -> str:
    done = subprocess.run(
        ["djvused", "-u", "-e", "print-pure-txt", str(DJVU)],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@needs_djvu
def test_corpus_djvu_keeps_its_page_count_and_text_layer(step_ctx, tmp_path):
    import pypdfium2

    out = convert(step_ctx, tmp_path)
    pdf = pypdfium2.PdfDocument(out)
    assert len(pdf) == djvu_pages()
    pdf.close()
    hidden = [w for w in djvu_text().split() if len(w) > 4 and w.isalpha()][:60]
    assert len(hidden) == 60  # the corpus DjVu has a text layer
    text = pdf_text(out)
    assert "Security" in text and "Resolution" in text
    found = [w for w in hidden if w in text]
    assert len(found) >= len(hidden) * 0.9, (hidden, text[:500])


@needs_djvu
def test_pages_over_the_pixel_limit_are_scaled_down(step_ctx, tmp_path):
    import pypdfium2

    step_ctx.max_pixels = 200_000
    out = convert(step_ctx, tmp_path)
    pdf = pypdfium2.PdfDocument(out)
    try:
        for page in pdf:
            for obj in page.get_objects():
                if obj.type == pypdfium2.raw.FPDF_PAGEOBJ_IMAGE:
                    w, h = obj.get_px_size()
                    assert w * h <= 200_000
    finally:
        pdf.close()


@needs_djvu
def test_a_djvu_over_the_page_limit_is_refused_before_rendering(step_ctx, tmp_path, monkeypatch):
    rendered = []
    monkeypatch.setattr(djvu_to_pdf, "render", lambda *a: rendered.append(a))
    step_ctx.max_pages = djvu_pages() - 1
    with pytest.raises(StepError) as e:
        convert(step_ctx, tmp_path)
    assert e.value.code == "INVALID_INPUT" and "limit" in e.value.message
    assert not rendered


@needs_djvu
def test_a_damaged_djvu_is_invalid_input(step_ctx, tmp_path):
    bad = tmp_path / "bad.djvu"
    bad.write_bytes(DJVU.read_bytes()[:200])
    with pytest.raises(StepError) as e:
        convert(step_ctx, tmp_path, bad)
    assert e.value.code == "INVALID_INPUT"


@needs_djvu
def test_works_from_a_cyrillic_folder(step_ctx, tmp_path):
    folder = tmp_path / "Книги"
    folder.mkdir()
    src = shutil.copyfile(DJVU, folder / "0.djvu")
    step_ctx.target = "pdf"
    (out,) = run(step_ctx, src, Options())
    assert sniff(out) == "pdf"
