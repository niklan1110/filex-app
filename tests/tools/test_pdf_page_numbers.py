"""pdf.page-numbers on real corpus files (plan task 29)."""

import re
from pathlib import Path

import pypdfium2
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.edit.pdf_page_numbers import Options, run


def number(ctx, path, **options) -> Path:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return out


def stamped(path: Path, pattern: str) -> list[str | None]:
    """Per page, the first match of ``pattern`` in the extracted text, or None."""
    pdf = pypdfium2.PdfDocument(path)
    try:
        found = []
        for page in pdf:
            m = re.search(pattern, page.get_textpage().get_text_range())
            found.append(m[0] if m else None)
        return found
    finally:
        pdf.close()


def box(path: Path, needle: str, index: int = 0) -> tuple[float, float, float, float]:
    """Box of ``needle`` on the page as fractions of the page size (0, 0 = bottom left)."""
    pdf = pypdfium2.PdfDocument(path)
    try:
        page = pdf[index]
        width, height = page.get_size()
        text = page.get_textpage()
        start, count = text.search(needle).get_next()
        boxes = [text.get_charbox(i) for i in range(start, start + count)]
        return (
            min(b[0] for b in boxes) / width,
            min(b[1] for b in boxes) / height,
            max(b[2] for b in boxes) / width,
            max(b[3] for b in boxes) / height,
        )
    finally:
        pdf.close()


def test_every_page_gets_its_number_at_the_bottom_centre(step_ctx):
    out = number(step_ctx, PDF / "text_10p.pdf", template="No. {n}")
    assert stamped(out, r"No\. \d+") == [f"No. {n}" for n in range(1, 11)]
    x0, _, x1, y1 = box(out, "No. 1")
    assert x0 < 0.5 < x1 and y1 < 0.06  # 10 mm from the bottom of an A4 page
    assert step_ctx.warnings == []


def test_skip_first_and_total_count_the_numbered_pages(step_ctx):
    out = number(step_ctx, PDF / "text_10p.pdf", template="No. {n}/{total}", skip_first=True)
    assert stamped(out, r"No\. \d+/\d+") == [None] + [f"No. {n}/9" for n in range(1, 10)]


def test_start_and_page_selection(step_ctx):
    out = number(
        step_ctx, PDF / "text_10p.pdf", template="No. {n} of {total}", start=7, pages="3-5"
    )
    expected = [None, None, "No. 7 of 9", "No. 8 of 9", "No. 9 of 9"] + [None] * 5
    assert stamped(out, r"No\. \d+ of \d+") == expected


def test_position_and_margin(step_ctx):
    out = number(
        step_ctx, PDF / "text_1p.pdf", template="Folio {n}", position="top-right", margin_mm=20
    )
    x0, y0, x1, _ = box(out, "Folio 1")
    margin = 20 * 72 / 25.4 / 595.2756  # 20 mm as a share of the A4 width
    assert x0 > 0.7 and y0 > 0.9
    assert x1 == pytest.approx(1 - margin, abs=0.01)


def test_numbers_follow_each_page_size(step_ctx):
    out = number(step_ctx, PDF / "mixed_pages.pdf", template="No. {n}", position="bottom-right")
    assert stamped(out, r"No\. \d") == ["No. 1", "No. 2", "No. 3", "No. 4"]
    x0, _, _, y1 = box(out, "No. 3", index=2)  # landscape page
    assert x0 > 0.85 and y1 < 0.1


def test_cyrillic_template(step_ctx):
    out = number(step_ctx, PDF / "text_1p.pdf", template="Страница {n} из {total}")
    assert stamped(out, r"Страница \d из \d") == ["Страница 1 из 1"]


@pytest.mark.parametrize("bad", ["Page", "{page}", "{n} {", "{n} {total} {0}", "{n.real}"])
def test_template_takes_only_n_and_total(bad):
    with pytest.raises(ValidationError):
        Options(template=bad)


def test_font_size_is_bounded():
    with pytest.raises(ValidationError):
        Options(font_size=200)


def test_not_a_pdf_fails_cleanly(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, CORPUS / "odd" / "text_named.pdf", Options())
    assert e.value.code == "INVALID_PDF"
