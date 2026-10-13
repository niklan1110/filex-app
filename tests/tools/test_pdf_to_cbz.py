"""pdf.to-cbz (plan task 52): one image per page in a CBZ; PDF -> CBZ -> PDF keeps the page
count."""

import zipfile

import pytest
from conftest import PDF
from ebook_samples import scratch_copy

from filex.formats import sniff
from filex.registry import StepError, ToolOptions
from filex.tools.ebook import comic_to_pdf
from filex.tools.ebook.pdf_to_cbz import Options, run

pypdfium2 = pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")


def convert(step_ctx, src, **options):
    step_ctx.target = "cbz"
    (out,) = run(step_ctx, src, Options(**options))
    assert sniff(out, out.name) == "cbz"
    return out


def test_every_page_becomes_one_jpeg_at_the_chosen_dpi(step_ctx):
    out = convert(step_ctx, PDF / "text_10p.pdf", dpi=72)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert names == [f"{i:04d}.jpg" for i in range(1, 11)]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
        with zf.open(names[0]) as f, Image.open(f) as im:
            assert im.format == "JPEG"
            pdf = pypdfium2.PdfDocument(PDF / "text_10p.pdf")
            w, h = pdf.get_page_size(0)
            pdf.close()
            assert abs(im.width - w) <= 1 and abs(im.height - h) <= 1  # 72 dpi = 1 px per pt


def test_png_pages(step_ctx):
    out = convert(step_ctx, PDF / "text_1p.pdf", dpi=50, image_format="png")
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["0001.png"]


def test_pdf_to_cbz_to_pdf_keeps_the_page_count(step_ctx, tmp_path):
    cbz = convert(step_ctx, PDF / "text_10p.pdf", dpi=50)
    step_ctx.out_dir = tmp_path / "back"
    step_ctx.out_dir.mkdir()
    step_ctx.target = "pdf"
    (pdf_path,) = comic_to_pdf.run(
        step_ctx, scratch_copy(cbz, tmp_path / "in", "cbz"), ToolOptions()
    )
    pdf = pypdfium2.PdfDocument(pdf_path)
    assert len(pdf) == 10
    pdf.close()


def test_pages_over_the_pixel_limit_are_refused_before_rendering(step_ctx):
    step_ctx.max_pixels = 10_000
    step_ctx.target = "cbz"
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_1p.pdf", Options(dpi=300))
    assert e.value.code == "INVALID_OPTIONS" and "megapixels" in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
        (PDF.parent / "odd/empty.pdf", "INVALID_PDF"),
    ],
)
def test_bad_pdfs(step_ctx, path, code):
    step_ctx.target = "cbz"
    with pytest.raises(StepError) as e:
        run(step_ctx, path, Options(dpi=36))
    assert e.value.code == code


def test_dpi_is_bounded():
    with pytest.raises(ValueError):
        Options(dpi=601)
