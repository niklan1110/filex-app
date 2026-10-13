"""pdf.to-image on real corpus files (plan task 28)."""

import zipfile

import pytest
from conftest import PDF, StepCtx
from PIL import Image
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.convert import pdf_to_image
from filex.tools.convert.pdf_to_image import Options, run


def render(ctx, path, **options) -> list:
    outs = run(ctx, path, Options(**options))
    assert all(o.path.parent == ctx.out_dir for o in outs)
    return outs


def test_300_dpi_render_of_10_pages_zips_correctly(tmp_path):
    ctx = StepCtx(tmp_path, ["report.pdf"])
    outs = render(ctx, PDF / "text_10p.pdf", dpi=300)
    assert [o.name for o in outs] == [f"report-p{n:03}.png" for n in range(1, 11)]
    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for o in outs:
            z.write(o.path, o.name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None and len(z.namelist()) == 10
        with z.open("report-p007.png") as f, Image.open(f) as im:
            assert im.format == "PNG"
            assert abs(im.width - 2480) <= 1 and abs(im.height - 3508) <= 1  # A4 at 300 dpi


@pytest.mark.parametrize(("fmt", "pil"), [("jpg", "JPEG"), ("webp", "WEBP"), ("tiff", "TIFF")])
def test_formats(step_ctx, fmt, pil):
    (out,) = render(step_ctx, PDF / "text_1p.pdf", format=fmt, dpi=72)
    assert out.name == f"p001.{fmt}"  # no input name: no prefix
    with Image.open(out.path) as im:
        assert im.format == pil and im.size == (596, 842)


def test_target_from_a_route_wins_over_the_format_option(step_ctx):
    step_ctx.target = "jpg"
    (out,) = render(step_ctx, PDF / "text_1p.pdf", format="png", dpi=72)
    with Image.open(out.path) as im:
        assert im.format == "JPEG"


def test_page_selection_keeps_pdf_numbers_and_renders_each_page_once(step_ctx):
    outs = render(step_ctx, PDF / "text_10p.pdf", dpi=36, pages="9-, 2, 2")
    assert [o.name for o in outs] == ["p009.png", "p010.png", "p002.png"]


def test_rotated_page_renders_turned(step_ctx):
    outs = render(step_ctx, PDF / "mixed_pages.pdf", dpi=72, pages="4")
    with Image.open(outs[0].path) as im:
        assert im.width > im.height  # A4 portrait with /Rotate 90


def test_page_over_the_pixel_limit_is_refused_before_rendering(step_ctx):
    step_ctx.max_pixels = 1_000_000
    with pytest.raises(StepError) as e:
        render(step_ctx, PDF / "text_10p.pdf", dpi=300)
    assert e.value.code == "INVALID_OPTIONS" and "lower resolution" in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


def test_dpi_is_capped_at_600():
    with pytest.raises(ValidationError):
        Options(dpi=601)


def test_more_images_than_the_output_cap_is_refused(step_ctx, monkeypatch):
    monkeypatch.setattr(pdf_to_image, "MAX_OUTPUTS", 5)
    with pytest.raises(StepError) as e:
        render(step_ctx, PDF / "text_10p.pdf", dpi=36)
    assert e.value.code == "INVALID_OPTIONS"


def test_page_past_the_end_is_refused(step_ctx):
    with pytest.raises(StepError) as e:
        render(step_ctx, PDF / "text_1p.pdf", pages="3")
    assert e.value.code == "INVALID_OPTIONS"


def test_input_over_the_page_limit_is_refused(step_ctx):
    step_ctx.max_pages = 9
    with pytest.raises(StepError) as e:
        render(step_ctx, PDF / "text_10p.pdf")
    assert e.value.code == "TOO_MANY_PAGES"


def test_owner_restricted_pdf_renders(step_ctx):
    assert len(render(step_ctx, PDF / "encrypted_owner_only.pdf", dpi=36)) == 1


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (PDF / "encrypted_aes256.pdf", "ENCRYPTED_PDF"),
        (PDF / "broken_truncated.pdf", "INVALID_PDF"),
        (PDF.parent / "odd/text_named.pdf", "INVALID_PDF"),
    ],
)
def test_bad_files(step_ctx, bad, code):
    with pytest.raises(StepError) as e:
        render(step_ctx, bad)
    assert e.value.code == code
