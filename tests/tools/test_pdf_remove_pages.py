"""pdf.remove-pages on real corpus files (plan task 20)."""

from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.organize.pdf_remove_pages import Options, run


def remove(ctx, path, pages) -> pikepdf.Pdf:
    (out,) = run(ctx, Path(path), Options(pages=pages))
    assert out.parent == ctx.out_dir
    return pikepdf.open(out)


def test_removes_the_selected_pages_and_keeps_the_order(step_ctx, page_numbers):
    pdf = remove(step_ctx, PDF / "text_10p.pdf", "2, 5-7, 9-")
    assert page_numbers(pdf) == [1, 3, 4, 8]
    assert step_ctx.warnings == []


def test_overlapping_ranges_remove_each_page_once(step_ctx, page_numbers):
    assert page_numbers(remove(step_ctx, PDF / "text_10p.pdf", "-3, 2-4, 3")) == [5, 6, 7, 8, 9, 10]


def test_a_range_past_the_end_stops_at_the_last_page(step_ctx, page_numbers):
    assert page_numbers(remove(step_ctx, PDF / "text_10p.pdf", "4-99")) == [1, 2, 3]


def test_keeps_page_size_and_rotation(step_ctx):
    pdf = remove(step_ctx, PDF / "mixed_pages.pdf", "1-2")
    with pikepdf.open(PDF / "mixed_pages.pdf") as src:
        expected = [(list(p.mediabox), p.obj.get("/Rotate", 0)) for p in src.pages[2:]]
    assert [(list(p.mediabox), p.obj.get("/Rotate", 0)) for p in pdf.pages] == expected


def test_bookmarks_to_removed_pages_are_dropped(step_ctx, tmp_path):
    src = tmp_path / "outlined.pdf"
    with pikepdf.open(PDF / "text_10p.pdf") as pdf:
        with pdf.open_outline() as o:
            o.root.extend([pikepdf.OutlineItem("Two", 1), pikepdf.OutlineItem("Six", 5)])
        pdf.save(src)
    pdf = remove(step_ctx, src, "2")
    index = {p.objgen: i for i, p in enumerate(pdf.pages)}
    with pdf.open_outline() as o:
        assert [(i.title, index[i.destination[0].objgen]) for i in o.root] == [("Six", 4)]


def test_input_metadata_is_not_carried_over(step_ctx):
    pdf = remove(step_ctx, PDF / "metadata.pdf", "2")
    assert len(pdf.pages) == 1
    assert not {str(k) for k in pdf.docinfo} & {"/Author", "/Company", "/Keywords", "/Title"}
    assert "/Metadata" not in pdf.Root


def test_removing_every_page_is_refused(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "scanned_3p.pdf", Options(pages="1-"))
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message == "The file would have no pages left. Keep at least one page."
    assert list(step_ctx.out_dir.iterdir()) == []


def test_a_page_past_the_end_names_the_range(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "scanned_3p.pdf", Options(pages="4-"))
    assert e.value.message == 'The file has 3 pages, so "4-" selects no page.'


def test_pages_are_required():
    with pytest.raises(ValidationError):
        Options()


def test_repairable_xref_warns(step_ctx):
    pdf = remove(step_ctx, PDF / "broken_xref.pdf", "1")
    assert len(pdf.pages) == 2
    assert [c for c, _ in step_ctx.warnings] == ["INPUT_REPAIRED"]


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", Options(pages="1"))
    assert e.value.code == "TOO_MANY_PAGES"


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
        run(step_ctx, bad, Options(pages="1"))
    assert e.value.code == code
    assert list(step_ctx.out_dir.iterdir()) == []
