"""pdf.rotate on real corpus files (plan task 20)."""

from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF

from filex.registry import StepError
from filex.tools.organize.pdf_rotate import Options, run


def rotate(ctx, path, **options) -> pikepdf.Pdf:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return pikepdf.open(out)


def rotations(pdf: pikepdf.Pdf) -> list[int]:
    return [int(p.obj.get("/Rotate", 0)) for p in pdf.pages]


def test_turns_every_page_by_default_adding_to_existing_rotation(step_ctx):
    pdf = rotate(step_ctx, PDF / "mixed_pages.pdf")
    assert rotations(pdf) == [90, 90, 90, 180]  # page 4 already had /Rotate 90
    assert step_ctx.warnings == []


@pytest.mark.parametrize(("angle", "expected"), [(180, [180, 0, 0, 270]), (270, [270, 0, 0, 0])])
def test_turns_only_the_selected_pages(step_ctx, angle, expected):
    pdf = rotate(step_ctx, PDF / "mixed_pages.pdf", angle=angle, pages="1, 4")
    assert rotations(pdf) == expected


def test_keeps_page_order_and_content(step_ctx, page_numbers):
    pdf = rotate(step_ctx, PDF / "text_10p.pdf", pages="2-3,9-")
    assert page_numbers(pdf) == list(range(1, 11))
    assert rotations(pdf) == [0, 90, 90, 0, 0, 0, 0, 0, 90, 90]


def test_inherited_rotation_is_added_to(step_ctx, tmp_path):
    src = tmp_path / "inherited.pdf"
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        pdf.Root.Pages.Rotate = 90
        if "/Rotate" in pdf.pages[0].obj:
            del pdf.pages[0].obj["/Rotate"]
        pdf.save(src)
    assert rotations(rotate(step_ctx, src, angle=180)) == [270]


def test_metadata_and_form_fields_are_kept(step_ctx, tmp_path):
    pdf = rotate(step_ctx, PDF / "metadata.pdf")
    assert "/Author" in pdf.docinfo
    (tmp_path / "f").mkdir()
    step_ctx.out_dir = tmp_path / "f"
    forms = rotate(step_ctx, PDF / "forms.pdf")
    with pikepdf.open(PDF / "forms.pdf") as one:
        assert len(forms.acroform.fields) == len(one.acroform.fields)


def test_a_page_past_the_end_names_the_range(step_ctx):
    step_ctx.input_names = ["scan.pdf"]
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "scanned_3p.pdf", Options(pages="2, 5-"))
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message == '"scan.pdf" has 3 pages, so "5-" selects no page.'


def test_angle_must_be_a_quarter_turn():
    with pytest.raises(ValueError):
        Options(angle=45)


def test_owner_restricted_input_is_rotated_and_warns(step_ctx):
    pdf = rotate(step_ctx, PDF / "encrypted_owner_only.pdf")
    assert not pdf.is_encrypted
    assert rotations(pdf) == [90]
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_repairable_xref_is_rotated_and_warns(step_ctx):
    pdf = rotate(step_ctx, PDF / "broken_xref.pdf")
    assert len(pdf.pages) == 3
    assert [c for c, _ in step_ctx.warnings] == ["INPUT_REPAIRED"]


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", Options())
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
        run(step_ctx, bad, Options())
    assert e.value.code == code
    assert e.value.message.startswith("The file ")
    assert list(step_ctx.out_dir.iterdir()) == []
