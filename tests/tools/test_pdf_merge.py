"""pdf.merge on real corpus files (plan task 7)."""

from pathlib import Path

import pikepdf
import pytest

from filex.registry import StepError
from filex.tools.organize.pdf_merge import Options, run

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
PDF = CORPUS / "pdf"


class Ctx:
    def __init__(self, out_dir: Path, names: list[str] | None = None) -> None:
        self.out_dir, self.aux, self.target = out_dir, {}, None
        self.input_names = names or []
        self.max_pages = 2000
        self.warnings: list[tuple[str, str]] = []

    def progress(self, fraction: float, message: str = "") -> None:
        assert 0.0 <= fraction <= 1.0

    def warn(self, code: str, message: str) -> None:
        self.warnings.append((code, message))


@pytest.fixture
def ctx(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return Ctx(out)


def merge(ctx, *paths, **options) -> pikepdf.Pdf:
    (out,) = run(ctx, [Path(p) for p in paths], Options(**options))
    assert out.parent == ctx.out_dir
    return pikepdf.open(out)


def outline(pdf: pikepdf.Pdf) -> list[tuple[str, int, list]]:
    """(title, page index, children) for every outline item."""
    index = {p.objgen: i for i, p in enumerate(pdf.pages)}

    def walk(items):
        return [(i.title, index[i.destination[0].objgen], walk(i.children)) for i in items]

    with pdf.open_outline() as o:
        return walk(o.root)


def test_merges_text_and_scan_with_a_bookmark_per_file(ctx):
    pdf = merge(ctx, PDF / "text_10p.pdf", PDF / "scanned_3p.pdf", bookmarks=True)
    assert len(pdf.pages) == 13
    assert outline(pdf) == [("File 1", 0, []), ("File 2", 10, [])]
    assert ctx.warnings == []


def test_bookmarks_use_the_file_names(ctx):
    ctx.input_names = ["Report 2026.PDF", ""]
    pdf = merge(ctx, PDF / "text_1p.pdf", PDF / "scanned_3p.pdf", bookmarks=True)
    assert outline(pdf) == [("Report 2026", 0, []), ("File 2", 1, [])]


def test_error_names_the_file(ctx):
    ctx.input_names = ["a.pdf", "locked.pdf"]
    with pytest.raises(StepError) as e:
        run(ctx, [PDF / "text_1p.pdf", PDF / "encrypted_aes256.pdf"], Options())
    assert e.value.message == '"locked.pdf" is password-protected. Remove the password first.'


def test_declares_the_codes_it_raises():
    from filex.tools.organize.pdf_merge import run as tool

    assert set(tool.errors) == {"INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"}


def test_no_bookmarks_by_default(ctx):
    pdf = merge(ctx, PDF / "text_1p.pdf", PDF / "scanned_3p.pdf")
    assert len(pdf.pages) == 4
    assert outline(pdf) == []


def test_keeps_upload_order_page_sizes_and_rotation(ctx):
    pdf = merge(ctx, PDF / "scanned_3p.pdf", PDF / "mixed_pages.pdf", PDF / "text_1p.pdf")
    with pikepdf.open(PDF / "mixed_pages.pdf") as mixed:
        expected = [(list(p.mediabox), p.obj.get("/Rotate", 0)) for p in mixed.pages]
    got = [(list(p.mediabox), p.obj.get("/Rotate", 0)) for p in pdf.pages[3:7]]
    assert got == expected
    assert len(pdf.pages) == 8
    assert len(pdf.pages[0].get_images()) == 1  # the scan came first
    assert "/Font" in pdf.pages[7].resources  # the text page came last


def test_inherited_page_attributes_survive(ctx, tmp_path):
    src = tmp_path / "inherited.pdf"
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        page = pdf.pages[0].obj
        pdf.Root.Pages.MediaBox = page.MediaBox
        pdf.Root.Pages.Rotate = 90
        del page["/MediaBox"]
        del page["/Rotate"]
        pdf.save(src)

    out = merge(ctx, PDF / "text_1p.pdf", src)
    assert out.pages[1].mediabox == out.pages[0].mediabox
    assert out.pages[1].obj.Rotate == 90


def test_source_bookmarks_are_kept_and_nested_under_the_file(tmp_path):
    src = tmp_path / "outlined.pdf"
    with pikepdf.open(PDF / "text_10p.pdf") as pdf:
        with pdf.open_outline() as o:
            chapter = pikepdf.OutlineItem("Chapter", 2)
            chapter.children.append(pikepdf.OutlineItem("Section", 4))
            o.root.append(chapter)
        pdf.save(src)

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    flat = merge(Ctx(tmp_path / "a"), PDF / "text_1p.pdf", src)
    assert outline(flat) == [("Chapter", 3, [("Section", 5, [])])]

    nested = merge(Ctx(tmp_path / "b"), PDF / "text_1p.pdf", src, bookmarks=True)
    assert outline(nested) == [
        ("File 1", 0, []),
        ("File 2", 1, [("Chapter", 3, [("Section", 5, [])])]),
    ]


def test_form_fields_keep_working_and_clashing_names_are_renamed(ctx):
    pdf = merge(ctx, PDF / "forms.pdf", PDF / "forms.pdf")
    names = [f.fully_qualified_name for f in pdf.acroform.fields]
    with pikepdf.open(PDF / "forms.pdf") as one:
        per_file = len(one.acroform.fields)
    assert len(names) == 2 * per_file
    assert "name" in names and "name+1" in names


def test_input_metadata_is_not_carried_over(ctx):
    pdf = merge(ctx, PDF / "metadata.pdf", PDF / "text_1p.pdf")
    info = {str(k) for k in pdf.docinfo}
    assert not info & {"/Author", "/Company", "/Keywords", "/Title"}
    assert "/Metadata" not in pdf.Root


@pytest.mark.parametrize("name", ["encrypted_aes256.pdf", "encrypted_rc4_40.pdf"])
def test_password_protected_input_fails_with_a_clear_code(ctx, name):
    with pytest.raises(StepError) as e:
        run(ctx, [PDF / "text_1p.pdf", PDF / name], Options())
    assert e.value.code == "ENCRYPTED_PDF"
    assert "File 2" in e.value.message
    assert list(ctx.out_dir.iterdir()) == []


def test_owner_restricted_input_merges_and_warns(ctx):
    pdf = merge(ctx, PDF / "encrypted_owner_only.pdf", PDF / "text_1p.pdf")
    assert len(pdf.pages) == 2
    assert not pdf.is_encrypted
    assert [c for c, _ in ctx.warnings] == ["RESTRICTIONS_REMOVED"]


def test_repairable_xref_merges_and_warns(ctx):
    pdf = merge(ctx, PDF / "text_1p.pdf", PDF / "broken_xref.pdf")
    assert len(pdf.pages) == 4
    assert [c for c, _ in ctx.warnings] == ["INPUT_REPAIRED"]
    assert "File 2" in ctx.warnings[0][1]


@pytest.mark.parametrize("bad", [PDF / "broken_truncated.pdf", CORPUS / "odd" / "empty.pdf"])
def test_broken_input_fails_with_invalid_pdf(ctx, bad):
    with pytest.raises(StepError) as e:
        run(ctx, [PDF / "text_1p.pdf", bad], Options())
    assert e.value.code == "INVALID_PDF"
    assert "File 2" in e.value.message
    assert list(ctx.out_dir.iterdir()) == []


def test_input_over_the_page_limit_is_refused(ctx):
    ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        run(ctx, [PDF / "text_1p.pdf", PDF / "text_10p.pdf"], Options())
    assert e.value.code == "TOO_MANY_PAGES"
    assert e.value.message == "File 2 has 10 pages; the limit is 9."


def test_total_pages_over_the_limit_are_refused(ctx):
    """Review L-2b: each file is under the limit, together they are over it."""
    ctx.max_pages = 12
    with pytest.raises(StepError) as e:
        run(ctx, [PDF / "text_10p.pdf", PDF / "scanned_3p.pdf"], Options())
    assert e.value.code == "TOO_MANY_PAGES"
    assert "over 12 pages" in e.value.message and "File 2" in e.value.message
    assert list(ctx.out_dir.iterdir()) == []
    ctx.max_pages = 13
    assert len(merge(ctx, PDF / "text_10p.pdf", PDF / "scanned_3p.pdf").pages) == 13
