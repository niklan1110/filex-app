"""pdf.to-text on real corpus files (plan task 28)."""

from pathlib import Path

import pytest
from conftest import PDF

from filex.formats import sniff
from filex.registry import StepError
from filex.tools.convert.pdf_to_text import Options, run

GOLDEN = Path(__file__).parent / "golden"


def extract(ctx, path, **options) -> str:
    (out,) = run(ctx, path, Options(**options))
    assert out.parent == ctx.out_dir
    assert sniff(out, out.name) == "txt"
    return out.read_bytes().decode("utf-8")


def test_text_sample_matches_the_golden_file(step_ctx):
    assert extract(step_ctx, PDF / "text_1p.pdf") == (GOLDEN / "text_1p.txt").read_text("utf-8")
    assert step_ctx.warnings == []


def test_pages_are_separated_by_form_feeds_in_order(step_ctx):
    pages = extract(step_ctx, PDF / "text_10p.pdf").split("\f")
    assert len(pages) == 10
    assert all(f"page {n} of 10" in p for n, p in enumerate(pages, 1))
    assert all(p.endswith("\n") and "\r" not in p for p in pages)


def test_layout_keeps_lines_and_indents_the_centred_footer(step_ctx):
    text = extract(step_ctx, PDF / "text_1p.pdf", layout=True)
    lines = text.splitlines()
    assert lines[0] == "Text PDF - page 1 of 1"
    footer = lines[-1]
    assert footer.strip() == "- 1 -" and footer.startswith(" " * 30)
    assert "" in lines  # wide vertical gaps become blank lines


def test_scan_without_text_warns_and_suggests_ocr(step_ctx):
    text = extract(step_ctx, PDF / "scanned_3p.pdf")
    assert text == "\n\f\n\f\n"
    ((code, message),) = step_ctx.warnings
    assert code == "NO_TEXT" and "OCR" in message


def test_owner_restricted_pdf_gives_its_text(step_ctx):
    assert "Owner password only" in extract(step_ctx, PDF / "encrypted_owner_only.pdf")


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", Options())
    assert e.value.code == "TOO_MANY_PAGES"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "encrypted_rc4_40.pdf", "ENCRYPTED_PDF"),
        (PDF / "broken_truncated.pdf", "INVALID_PDF"),
        (PDF.parent / "odd/empty.pdf", "INVALID_PDF"),
    ],
)
def test_bad_files(step_ctx, bad, code):
    with pytest.raises(StepError) as e:
        run(step_ctx, bad, Options())
    assert e.value.code == code
