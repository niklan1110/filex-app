"""pdf.extract-pages on real corpus files (plan task 20)."""

from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.organize.pdf_extract_pages import Options, run


def extract(ctx, path, pages) -> pikepdf.Pdf:
    (out,) = run(ctx, Path(path), Options(pages=pages))
    assert out.parent == ctx.out_dir
    return pikepdf.open(out)


def outline(pdf: pikepdf.Pdf) -> list[tuple[str, int | None, list]]:
    index = {p.objgen: i for i, p in enumerate(pdf.pages)}

    def walk(items):
        return [
            (i.title, index[i.destination[0].objgen] if i.destination else None, walk(i.children))
            for i in items
        ]

    with pdf.open_outline() as o:
        return walk(o.root)


def test_keeps_the_selected_pages_in_document_order(step_ctx, page_numbers):
    pdf = extract(step_ctx, PDF / "text_10p.pdf", "8-, 2, 1-3, 3")
    assert page_numbers(pdf) == [1, 2, 3, 8, 9, 10]
    assert step_ctx.warnings == []


def test_open_ranges_and_a_single_page(step_ctx, page_numbers, tmp_path):
    assert page_numbers(extract(step_ctx, PDF / "text_10p.pdf", "-2")) == [1, 2]
    (tmp_path / "b").mkdir()
    step_ctx.out_dir = tmp_path / "b"
    assert page_numbers(extract(step_ctx, PDF / "text_10p.pdf", "10")) == [10]


def test_a_range_past_the_end_stops_at_the_last_page(step_ctx, page_numbers):
    assert page_numbers(extract(step_ctx, PDF / "text_10p.pdf", "9-20")) == [9, 10]


def test_keeps_page_size_and_rotation(step_ctx):
    pdf = extract(step_ctx, PDF / "mixed_pages.pdf", "3-4")
    with pikepdf.open(PDF / "mixed_pages.pdf") as src:
        expected = [(list(p.mediabox), p.obj.get("/Rotate", 0)) for p in src.pages[2:]]
    assert [(list(p.mediabox), p.obj.get("/Rotate", 0)) for p in pdf.pages] == expected


def test_bookmarks_to_kept_pages_are_kept(step_ctx, tmp_path):
    src = tmp_path / "outlined.pdf"
    with pikepdf.open(PDF / "text_10p.pdf") as pdf:
        with pdf.open_outline() as o:
            one = pikepdf.OutlineItem("One", 0)
            one.children.append(pikepdf.OutlineItem("One.Five", 4))
            two = pikepdf.OutlineItem("Two", 6)
            two.children.append(pikepdf.OutlineItem("Two.Eight", 7))
            o.root.extend([one, two, pikepdf.OutlineItem("Nine", 8)])
        pdf.save(src)
    pdf = extract(step_ctx, src, "5, 8-9")
    # "One" points at a dropped page but has a kept child, so it stays as a title.
    assert outline(pdf) == [
        ("One", None, [("One.Five", 0, [])]),
        ("Two", None, [("Two.Eight", 1, [])]),
        ("Nine", 2, []),
    ]


def test_form_fields_keep_working(step_ctx):
    pdf = extract(step_ctx, PDF / "forms.pdf", "1")
    with pikepdf.open(PDF / "forms.pdf") as one:
        expected = sorted(f.fully_qualified_name for f in one.acroform.fields)
    assert sorted(f.fully_qualified_name for f in pdf.acroform.fields) == expected


def test_input_metadata_is_not_carried_over(step_ctx):
    pdf = extract(step_ctx, PDF / "metadata.pdf", "1")
    assert not {str(k) for k in pdf.docinfo} & {"/Author", "/Company", "/Keywords", "/Title"}
    assert "/Metadata" not in pdf.Root


def test_a_page_past_the_end_names_the_range(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", Options(pages="1, 11"))
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message == 'The file has 10 pages, so "11" selects no page.'


def test_pages_are_required():
    with pytest.raises(ValidationError):
        Options()
    with pytest.raises(ValidationError):
        Options(pages="")


def test_owner_restricted_input_warns(step_ctx):
    pdf = extract(step_ctx, PDF / "encrypted_owner_only.pdf", "1")
    assert not pdf.is_encrypted
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_repairable_xref_warns(step_ctx):
    pdf = extract(step_ctx, PDF / "broken_xref.pdf", "2-")
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
        (PDF / "encrypted_rc4_40.pdf", "ENCRYPTED_PDF"),
    ],
)
def test_unreadable_input_fails_with_a_clear_code(step_ctx, bad, code):
    step_ctx.input_names = ["in.pdf"]
    with pytest.raises(StepError) as e:
        run(step_ctx, bad, Options(pages="1"))
    assert e.value.code == code
    assert e.value.message.startswith('"in.pdf" ')
    assert list(step_ctx.out_dir.iterdir()) == []
