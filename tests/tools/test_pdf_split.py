"""pdf.split on real corpus files (plan task 20)."""

from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF, StepCtx
from pydantic import ValidationError

from filex import registry
from filex.registry import StepError
from filex.tools.organize import pdf_split
from filex.tools.organize.pdf_split import Options, run


def split(ctx, path, page_numbers=None, **options) -> list:
    outs = [o.path for o in run(ctx, [Path(path)], Options(**options))]
    assert all(o.parent == ctx.out_dir for o in outs)
    assert len({o.name for o in outs}) == len(outs)
    pdfs = [pikepdf.open(o) for o in outs]
    return [page_numbers(p) for p in pdfs] if page_numbers else pdfs


def test_each_page_by_default(step_ctx, page_numbers):
    parts = split(step_ctx, PDF / "text_10p.pdf", page_numbers)
    assert parts == [[n] for n in range(1, 11)]
    assert step_ctx.warnings == []


def test_every_n_pages_with_a_shorter_last_part(step_ctx, page_numbers):
    parts = split(step_ctx, PDF / "text_10p.pdf", page_numbers, every=4)
    assert parts == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]


def test_every_n_larger_than_the_file_gives_one_part(step_ctx, page_numbers):
    assert split(step_ctx, PDF / "text_10p.pdf", page_numbers, every=50) == [list(range(1, 11))]


def test_by_ranges_in_the_order_written_overlaps_allowed(step_ctx, page_numbers):
    parts = split(step_ctx, PDF / "text_10p.pdf", page_numbers, mode="ranges", ranges="8-, 1-3, 3")
    assert parts == [[8, 9, 10], [1, 2, 3], [3]]


def test_parts_are_numbered_in_order(step_ctx):
    outs = run(step_ctx, [PDF / "scanned_3p.pdf"], Options())
    assert [o.name for o in outs] == ["part-001.pdf", "part-002.pdf", "part-003.pdf"]


def test_ranges_mode_needs_ranges():
    with pytest.raises(ValidationError) as e:
        Options(mode="ranges")
    assert e.value.errors()[0]["loc"] == ("ranges",)
    with pytest.raises(ValidationError) as e:
        Options(mode="ranges", ranges="4-2")
    assert "goes backwards" in e.value.errors()[0]["msg"]


def test_a_range_past_the_end_names_it(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, [PDF / "scanned_3p.pdf"], Options(mode="ranges", ranges="1, 4-"))
    assert e.value.code == "INVALID_OPTIONS"
    assert '"4-"' in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


@pytest.fixture
def outlined(tmp_path) -> Path:
    """text_10p with top-level bookmarks at pages 3 and 7 (one with a child at 8)."""
    src = tmp_path / "outlined.pdf"
    with pikepdf.open(PDF / "text_10p.pdf") as pdf:
        with pdf.open_outline() as o:
            second = pikepdf.OutlineItem("Second", 6)
            second.children.append(pikepdf.OutlineItem("Detail", 7))
            o.root.extend([pikepdf.OutlineItem("First", 2), second])
        pdf.save(src)
    return src


def test_by_top_level_bookmarks_with_the_front_matter_first(step_ctx, outlined, page_numbers):
    pdfs = split(step_ctx, outlined, mode="bookmarks")
    assert [page_numbers(p) for p in pdfs] == [[1, 2], [3, 4, 5, 6], [7, 8, 9, 10]]
    with pdfs[2].open_outline() as o:
        (item,) = o.root
        assert item.title == "Second"
        assert [c.title for c in item.children] == ["Detail"]


def test_no_bookmarks_is_a_clear_error(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, [PDF / "text_10p.pdf"], Options(mode="bookmarks"))
    assert e.value.code == "NO_BOOKMARKS"
    assert e.value.message == "The file has no bookmarks to split at."


def test_too_many_parts_is_refused(step_ctx, monkeypatch):
    monkeypatch.setattr("filex.tools.organize.pdf_split.MAX_OUTPUTS", 5)
    with pytest.raises(StepError) as e:
        run(step_ctx, [PDF / "text_10p.pdf"], Options())
    assert e.value.code == "INVALID_OPTIONS"
    assert e.value.message.startswith("This would make 10 files; the limit is 5.")


def test_parts_keep_forms_and_drop_metadata(step_ctx, tmp_path):
    (form,) = split(step_ctx, PDF / "forms.pdf")
    with pikepdf.open(PDF / "forms.pdf") as one:
        assert len(form.acroform.fields) == len(one.acroform.fields)
    (tmp_path / "m").mkdir()
    step_ctx.out_dir = tmp_path / "m"
    for part in split(step_ctx, PDF / "metadata.pdf"):
        assert "/Author" not in part.docinfo
        assert "/Metadata" not in part.Root


def test_owner_restricted_input_warns(step_ctx):
    (part,) = split(step_ctx, PDF / "encrypted_owner_only.pdf")
    assert not part.is_encrypted
    assert [c for c, _ in step_ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(step_ctx, [PDF / "text_10p.pdf"], Options())
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
        run(step_ctx, [bad], Options())
    assert e.value.code == code
    assert list(step_ctx.out_dir.iterdir()) == []


def test_part_cap_is_the_runner_output_cap():
    assert pdf_split.MAX_OUTPUTS is registry.MAX_OUTPUTS == 1000


@pytest.mark.parametrize(
    ("names", "first"),
    [
        (["Annual report.pdf"], "Annual report-part-001.pdf"),
        (["scan"], "scan-part-001.pdf"),
        ([""], "part-001.pdf"),
        ([], "part-001.pdf"),
        (["x" * 300 + ".pdf"], "x" * 100 + "-part-001.pdf"),
    ],
)
def test_parts_are_named_after_the_input(tmp_path, names, first):
    ctx = StepCtx(tmp_path, names)
    outs = run(ctx, [PDF / "text_10p.pdf"], Options(every=4))
    assert [o.name for o in outs] == [first.replace("001", f"00{n}") for n in (1, 2, 3)]
    assert [o.path.name for o in outs] == ["part-001.pdf", "part-002.pdf", "part-003.pdf"]
