"""pdf.organize on real corpus files (plan task 21)."""

from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.organize.pdf_organize import Options, run


def plan(*steps) -> Options:
    """``plan(3, (1, 90))``: page 3 as is, then page 1 turned 90 degrees."""
    return Options.model_validate(
        {
            "pages": [
                {"page": s} if isinstance(s, int) else {"page": s[0], "rotate": s[1]} for s in steps
            ]
        }
    )


def organize(ctx, path, opts) -> pikepdf.Pdf:
    (out,) = run(ctx, Path(path), opts)
    assert out.parent == ctx.out_dir
    return pikepdf.open(out)


def rotations(pdf: pikepdf.Pdf) -> list[int]:
    return [int(p.obj.get("/Rotate", 0)) for p in pdf.pages]


def test_reverse_delete_and_rotate_on_ten_pages(step_ctx, page_numbers):
    # Plan task 21 acceptance: reverse, drop pages 4 and 7, turn page 9 and page 2.
    steps = [
        (n, 90 if n == 9 else 270 if n == 2 else 0) for n in range(10, 0, -1) if n not in (4, 7)
    ]
    pdf = organize(step_ctx, PDF / "text_10p.pdf", plan(*steps))
    assert page_numbers(pdf) == [10, 9, 8, 6, 5, 3, 2, 1]
    assert rotations(pdf) == [0, 90, 0, 0, 0, 0, 270, 0]
    assert step_ctx.warnings == []


def test_duplicates_are_separate_pages(step_ctx, page_numbers):
    pdf = organize(step_ctx, PDF / "text_10p.pdf", plan(2, (2, 180), 2))
    assert page_numbers(pdf) == [2, 2, 2]
    assert rotations(pdf) == [0, 180, 0]
    assert len({p.objgen for p in pdf.pages}) == 3


def test_turns_add_to_the_existing_rotation(step_ctx):
    with pikepdf.open(PDF / "mixed_pages.pdf") as src:
        before = rotations(src)
    pdf = organize(
        step_ctx, PDF / "mixed_pages.pdf", plan(*((i + 1, 90) for i in range(len(before))))
    )
    assert rotations(pdf) == [(r + 90) % 360 for r in before]


def test_bookmarks_follow_the_first_copy_and_dropped_pages_go(step_ctx, tmp_path):
    src = tmp_path / "outlined.pdf"
    with pikepdf.open(PDF / "text_10p.pdf") as pdf:
        with pdf.open_outline() as o:
            o.root.extend([pikepdf.OutlineItem("Three", 2), pikepdf.OutlineItem("Five", 4)])
        pdf.save(src)
    pdf = organize(step_ctx, src, plan(1, 3, 3))
    index = {p.objgen: i for i, p in enumerate(pdf.pages)}
    with pdf.open_outline() as o:
        assert [(i.title, index[i.destination[0].objgen]) for i in o.root] == [("Three", 1)]


def test_form_fields_keep_working_when_duplicated(step_ctx):
    pdf = organize(step_ctx, PDF / "forms.pdf", plan(1, 1))
    with pikepdf.open(PDF / "forms.pdf") as one:
        n = len(list(one.acroform.fields))
    assert len(list(pdf.acroform.fields)) == 2 * n


def test_input_metadata_is_not_carried_over(step_ctx):
    pdf = organize(step_ctx, PDF / "metadata.pdf", plan(1))
    assert not {str(k) for k in pdf.docinfo} & {"/Author", "/Company", "/Keywords", "/Title"}
    assert "/Metadata" not in pdf.Root


def test_a_page_past_the_end_is_named(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", plan(1, 12, 11))
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message == "The file has 10 pages, so page 12 does not exist."
    assert list(step_ctx.out_dir.iterdir()) == []


def test_a_plan_longer_than_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 12
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", plan(*([1] * 13)))
    assert e.value.code == "INVALID_OPTIONS"
    assert "13 pages; the limit is 12" in e.value.message


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"pages": []},
        {"pages": [{"page": 0}]},
        {"pages": [{"page": 1, "rotate": 45}]},
        {"pages": [{"page": 1, "turn": 90}]},
        {"pages": [{"page": 1}] * 2001},
        {"pages": "1-3"},
    ],
)
def test_bad_plans_are_rejected_before_the_job(raw):
    with pytest.raises(ValidationError):
        Options.model_validate(raw)


def test_owner_restricted_input_warns(step_ctx):
    pdf = organize(step_ctx, PDF / "encrypted_owner_only.pdf", plan(1))
    assert not pdf.is_encrypted
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_10p.pdf", plan(1))
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
        run(step_ctx, bad, plan(1))
    assert e.value.code == code
    assert e.value.message.startswith('"in.pdf" ')
    assert list(step_ctx.out_dir.iterdir()) == []


def test_a_job_step_runs_it_on_a_real_file(tmp_path, page_numbers):
    """Regression (task 64): the step child passed a list, run() wanted a Path, every job
    failed with ENGINE_FAILED. Runs through the step child's own dispatch."""
    from filex.worker import step

    out = tmp_path / "w0"
    out.mkdir()
    req = {
        "module": "filex.tools.organize.pdf_organize",
        "tool": "pdf.organize",
        "options": {"pages": [{"page": 3, "rotate": 90}, {"page": 1}]},
        "secrets": {},
        "inputs": [str(PDF / "text_10p.pdf")],
        "input_names": ["ten.pdf"],
        "out_dirs": [str(out)],
        "target": None,
        "aux": {},
        "max_pages": 2000,
        "max_pixels": 100_000_000,
    }
    ((k, name, role, _),) = step.run(req, lambda **e: None)
    assert (k, role) == (0, "RESULT")
    pdf = pikepdf.open(out / name)
    assert page_numbers(pdf) == [3, 1]
    assert rotations(pdf) == [90, 0]
