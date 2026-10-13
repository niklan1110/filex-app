"""pdf.metadata-edit on real corpus files (plan task 30): Info and XMP stay in sync."""

from pathlib import Path

import pikepdf
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.edit.pdf_metadata_edit import Options, run

DC, XMP_NS, PDF_NS = (
    "{http://purl.org/dc/elements/1.1/}",
    "{http://ns.adobe.com/xap/1.0/}",
    "{http://ns.adobe.com/pdf/1.3/}",
)


def edit(ctx, path, **options) -> tuple[dict, dict]:
    """(Info as plain strings, XMP as a dict) of the output."""
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    with pikepdf.open(out) as pdf:
        info = {k: str(v) for k, v in pdf.docinfo.items()}
        with pdf.open_metadata() as m:
            return info, dict(m)


def test_sets_fields_in_info_and_xmp(step_ctx):
    info, xmp = edit(
        step_ctx,
        PDF / "metadata.pdf",
        title="Годовой отчёт",
        author="Ann Writer",
        subject="Finance",
        keywords="report, 2024",
        creator="Word",
        producer="FileX",
    )
    assert info["/Title"] == "Годовой отчёт" and xmp[DC + "title"] == "Годовой отчёт"
    assert info["/Author"] == "Ann Writer" and xmp[DC + "creator"] == ["Ann Writer"]
    assert info["/Subject"] == "Finance" and xmp[DC + "description"] == "Finance"
    assert info["/Keywords"] == "report, 2024" and xmp[PDF_NS + "Keywords"] == "report, 2024"
    assert info["/Creator"] == "Word" and xmp[XMP_NS + "CreatorTool"] == "Word"
    assert info["/Producer"] == "FileX" and xmp[PDF_NS + "Producer"] == "FileX"


def test_update_keeps_fields_left_out_and_custom_keys(step_ctx):
    info, xmp = edit(step_ctx, PDF / "metadata.pdf", title="New title")
    assert info["/Author"] == "Jane Example" and xmp[DC + "creator"] == ["Jane Example"]
    assert info["/Company"] == "Example Corp"
    assert "pikepdf" not in info.get("/Producer", "")  # pikepdf does not stamp itself


def test_a_file_without_xmp_keeps_its_info_fields(step_ctx):
    info, xmp = edit(step_ctx, PDF / "text_1p.pdf", keywords="added")
    assert info["/Title"] == "Text PDF" and xmp[DC + "title"] == "Text PDF"
    assert info["/Subject"] == "unspecified"
    assert info["/Keywords"] == "added"


def test_empty_string_clears_a_field_in_both(step_ctx):
    info, xmp = edit(step_ctx, PDF / "metadata.pdf", author="")
    assert "/Author" not in info and DC + "creator" not in xmp
    assert info["/Title"].startswith("Quarterly report")


def test_replace_removes_everything_not_given(step_ctx):
    info, xmp = edit(step_ctx, PDF / "metadata.pdf", mode="replace", title="Only this")
    assert info == {"/Title": "Only this"}
    assert DC + "creator" not in xmp and XMP_NS + "CreatorTool" not in xmp
    assert xmp[DC + "title"] == "Only this"


def test_dates(step_ctx):
    info, xmp = edit(
        step_ctx, PDF / "metadata.pdf", created="2024-05-01T14:30+02:00", modified="2025-01-31"
    )
    assert info["/CreationDate"].startswith("D:20240501143000+02")
    assert xmp[XMP_NS + "CreateDate"] == "2024-05-01T14:30:00+02:00"
    assert info["/ModDate"].startswith("D:20250131000000")


@pytest.mark.parametrize("bad", ["yesterday", "2024-13-01", "01/05/2024"])
def test_bad_dates_are_rejected(bad):
    with pytest.raises(ValidationError):
        Options(created=bad)


def test_password_protected_input_is_refused(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "encrypted_aes256.pdf", Options(title="x"))
    assert e.value.code == "ENCRYPTED_PDF"


def test_not_a_pdf_fails_cleanly(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, CORPUS / "odd" / "text_named.pdf", Options(title="x"))
    assert e.value.code == "INVALID_PDF"
