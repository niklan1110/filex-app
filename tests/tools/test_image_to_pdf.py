"""image.to-pdf on real corpus images (plan task 28)."""

import shutil

import pikepdf
import pytest
from conftest import CORPUS, StepCtx
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.convert import image_to_pdf
from filex.tools.convert.image_to_pdf import Options, run

IMAGE = CORPUS / "image"
ALL = sorted(IMAGE.iterdir())  # 7 files, 9 pages: the G4 TIFF has 3


def convert(ctx, paths, **options) -> list[pikepdf.Pdf]:
    outs = run(ctx, list(paths), Options(**options))
    assert all(o.parent == ctx.out_dir for o in outs)
    return [pikepdf.open(o) for o in outs]


def size(page) -> tuple[int, int]:
    box = [float(x) for x in page.mediabox]
    return round(box[2] - box[0]), round(box[3] - box[1])


def image_of(page) -> pikepdf.Stream:
    (xobj,) = page.Resources.XObject.values()
    return xobj


def test_all_corpus_images_combine_into_one_pdf(step_ctx):
    (pdf,) = convert(step_ctx, ALL)
    assert len(pdf.pages) == 9
    assert step_ctx.warnings == []


def test_each_mode_makes_one_pdf_per_image(step_ctx):
    pdfs = convert(step_ctx, ALL, mode="each")
    assert [len(p.pages) for p in pdfs] == [1, 1, 1, 1, 1, 3, 1]


def test_jpeg_is_embedded_without_re_encoding_and_cmyk_stays_cmyk(step_ctx):
    (pdf,) = convert(step_ctx, [IMAGE / "photo_cmyk.jpg"])
    xobj = image_of(pdf.pages[0])
    assert xobj.Filter == "/DCTDecode"
    assert xobj.ColorSpace == "/DeviceCMYK"
    assert xobj.read_raw_bytes() == (IMAGE / "photo_cmyk.jpg").read_bytes()


def test_exif_orientation_turns_the_page(step_ctx):
    (pdf,) = convert(step_ctx, [IMAGE / "photo_exif.jpg"])
    assert pdf.pages[0].get("/Rotate") == 90


def test_png_alpha_is_kept_as_a_soft_mask(step_ctx):
    (pdf,) = convert(step_ctx, [IMAGE / "graphic_rgba.png"])
    assert "/SMask" in image_of(pdf.pages[0])


def test_heic_converts_at_its_pixel_size(step_ctx):
    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    with Image.open(IMAGE / "photo.heic") as im:
        w, h = im.size
    (pdf,) = convert(step_ctx, [IMAGE / "photo.heic"])
    xobj = image_of(pdf.pages[0])
    assert (int(xobj.Width), int(xobj.Height)) == (w, h)


def test_webp_and_multipage_tiff_convert(step_ctx):
    (pdf,) = convert(step_ctx, [IMAGE / "photo.webp", IMAGE / "scan_multipage_g4.tiff"])
    assert len(pdf.pages) == 4


def test_a4_page_with_margin_fits_the_image_inside(step_ctx):
    (pdf,) = convert(step_ctx, [IMAGE / "photo_cmyk.jpg"], page_size="a4", margin_mm=10)
    assert size(pdf.pages[0]) in ((595, 842), (842, 595))  # auto: turned to match the photo


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"page_size": "a4", "orientation": "portrait"}, (595, 842)),
        ({"page_size": "letter", "orientation": "landscape", "fit": "fill"}, (792, 612)),
        ({"page_size": "a5", "orientation": "portrait", "fit": "shrink"}, (420, 595)),
    ],
)
def test_fixed_paper_size_and_orientation(step_ctx, options, expected):
    (pdf,) = convert(step_ctx, [IMAGE / "photo_cmyk.jpg"], **options)
    assert size(pdf.pages[0]) == expected


def test_margin_is_limited():
    with pytest.raises(ValidationError):
        Options(margin_mm=51)


def test_decompression_bomb_is_refused_before_conversion(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, [CORPUS / "odd/png_huge_dimensions.png"], Options())
    assert e.value.code == "INVALID_INPUT"
    assert list(step_ctx.out_dir.iterdir()) == []


def test_image_over_the_pixel_limit_is_refused_by_name(tmp_path):
    ctx = StepCtx(tmp_path, ["big.jpg"])
    ctx.max_pixels = 10_000  # photo_exif.jpg is 640x480
    with pytest.raises(StepError) as e:
        run(ctx, [IMAGE / "photo_exif.jpg"], Options())
    assert e.value.code == "INVALID_INPUT"
    assert '"big.jpg"' in e.value.message and "megapixels" in e.value.message


def test_too_many_frames_are_refused(step_ctx, monkeypatch):
    monkeypatch.setattr(image_to_pdf, "MAX_FRAMES", 2)
    with pytest.raises(StepError) as e:
        run(step_ctx, [IMAGE / "scan_multipage_g4.tiff"], Options())
    assert e.value.code == "INVALID_INPUT" and "frames" in e.value.message


@pytest.mark.parametrize("bad", ["odd/truncated.jpg", "odd/text_named.pdf"])
def test_damaged_image_fails_with_invalid_input(step_ctx, bad):
    with pytest.raises(StepError) as e:
        run(step_ctx, [IMAGE / "photo_exif.jpg", CORPUS / bad], Options())
    assert e.value.code == "INVALID_INPUT"
    assert "Image 2" in e.value.message


def test_combine_refuses_more_pages_in_total_than_the_limit(tmp_path):
    ctx = StepCtx(tmp_path)
    ctx.max_pages = 8  # ALL makes 9 pages, no single image more than 3
    with pytest.raises(StepError) as e:
        run(ctx, ALL, Options())
    assert e.value.code == "TOO_MANY_PAGES" and "together" in e.value.message
    assert list(ctx.out_dir.iterdir()) == []
    assert len(run(ctx, ALL, Options(mode="each"))) == len(ALL)  # per image it is fine


def test_each_mode_refuses_one_image_over_the_page_limit(tmp_path):
    ctx = StepCtx(tmp_path, ["scan.tiff"])
    ctx.max_pages = 2
    with pytest.raises(StepError) as e:
        run(ctx, [IMAGE / "scan_multipage_g4.tiff"], Options(mode="each"))
    assert e.value.code == "TOO_MANY_PAGES" and '"scan.tiff"' in e.value.message


def test_decoder_is_pinned_to_the_sniffed_format(step_ctx, tmp_path):
    """A PNG under a .jpg name (the runner names files after the sniffed format) is refused,
    not decoded by whichever Pillow plugin recognises the bytes (review L-3)."""
    fake = tmp_path / "1.jpg"
    shutil.copy(IMAGE / "graphic_rgba.png", fake)
    with pytest.raises(StepError) as e:
        run(step_ctx, [fake], Options())
    assert e.value.code == "INVALID_INPUT"
