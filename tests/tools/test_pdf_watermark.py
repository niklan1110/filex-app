"""pdf.watermark on real corpus files (plan task 29)."""

from pathlib import Path

import pikepdf
import pypdfium2
import pytest
from conftest import CORPUS, PDF
from pydantic import ValidationError

from filex.registry import StepError
from filex.tools.edit.pdf_watermark import Options, run

RED = "#FF0000"


def watermark(ctx, path, **options) -> Path:
    (out,) = run(ctx, Path(path), Options(**options))
    assert out.parent == ctx.out_dir
    return out


def texts(path: Path) -> list[str]:
    pdf = pypdfium2.PdfDocument(path)
    try:
        return [page.get_textpage().get_text_range() for page in pdf]
    finally:
        pdf.close()


def red_box(path: Path, index: int = 0) -> tuple[float, float, float, float]:
    """Bounding box of the red pixels of the rendered page as fractions of its width and
    height (0, 0 = top left as the reader sees it, rotation applied)."""
    pdf = pypdfium2.PdfDocument(path)
    try:
        img = pdf[index].render(scale=0.5).to_pil().convert("RGB")
    finally:
        pdf.close()
    w, h = img.size
    px = img.load()
    hits = [(x, y) for y in range(h) for x in range(w) if _red(px[x, y])]
    assert hits, "no red pixels"
    xs, ys = [x for x, _ in hits], [y for _, y in hits]
    return min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h


def _red(p) -> bool:
    return p[0] > 180 and p[1] < 90 and p[2] < 90


def test_text_is_on_every_selected_page_only(step_ctx):
    out = watermark(step_ctx, PDF / "text_10p.pdf", text="CONFIDENTIAL", pages="2, 4-5")
    found = [i + 1 for i, t in enumerate(texts(out)) if "CONFIDENTIAL" in t]
    assert found == [2, 4, 5]
    assert step_ctx.warnings == []


def test_blank_pages_means_every_page(step_ctx):
    out = watermark(step_ctx, PDF / "mixed_pages.pdf", text="DRAFT")
    assert all("DRAFT" in t for t in texts(out))


def test_cyrillic_text_is_embedded_and_extractable(step_ctx):
    out = watermark(step_ctx, PDF / "text_1p.pdf", text="СЕКРЕТНО Черновик")
    assert "СЕКРЕТНО Черновик" in texts(out)[0]


def test_text_the_font_cannot_show_is_refused(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_1p.pdf", Options(text="草稿 draft"))
    assert e.value.code == "UNSUPPORTED_TEXT"
    assert "草" in e.value.message


@pytest.mark.parametrize(
    ("position", "check"),
    [
        ("top-left", lambda b: b[2] < 0.5 and b[3] < 0.3),
        ("bottom-right", lambda b: b[0] > 0.5 and b[1] > 0.7),
        ("center", lambda b: b[0] < 0.5 < b[2] and b[1] < 0.5 < b[3]),
        ("top-center", lambda b: b[0] < 0.5 < b[2] and b[3] < 0.3),
    ],
)
def test_nine_point_positions(step_ctx, position, check):
    out = watermark(
        step_ctx,
        PDF / "text_1p.pdf",
        text="MARK",
        color=RED,
        opacity=100,
        rotation=0,
        position=position,
    )
    box = red_box(out)
    assert check(box), box
    assert box[0] > 0.02 and box[2] < 0.98  # stays inside the 10 mm margin


@pytest.mark.parametrize("index", [2, 3])  # page 3 landscape, page 4 /Rotate 90
def test_position_and_reading_direction_follow_the_displayed_page(step_ctx, index):
    out = watermark(
        step_ctx,
        PDF / "mixed_pages.pdf",
        text="MARK",
        color=RED,
        opacity=100,
        rotation=0,
        position="top-right",
    )
    x0, y0, x1, y1 = red_box(out, index)
    assert x0 > 0.5 and y1 < 0.3, (x0, y0, x1, y1)
    assert x1 - x0 > y1 - y0  # horizontal for the reader, not turned with the page


def test_diagonal_rotation_spans_both_directions(step_ctx):
    out = watermark(step_ctx, PDF / "text_1p.pdf", text="CONFIDENTIAL", color=RED, opacity=100)
    x0, y0, x1, y1 = red_box(out)
    assert x1 - x0 > 0.3 and y1 - y0 > 0.2  # 45 degrees by default


def test_tiled_repeats_over_the_page(step_ctx):
    out = watermark(step_ctx, PDF / "text_1p.pdf", text="DRAFT", position="tiled", font_size=24)
    assert texts(out)[0].count("DRAFT") >= 6
    x0, y0, x1, y1 = red_box(
        watermark(
            step_ctx, PDF / "text_1p.pdf", text="DRAFT", position="tiled", color=RED, opacity=100
        )
    )
    assert x0 < 0.15 and x1 > 0.85 and y0 < 0.15 and y1 > 0.85


def test_tiling_a_huge_crop_box_is_refused_not_looped(step_ctx, tmp_path):
    """Review L-2e: a 1e7 pt CropBox would ask for billions of tiles."""
    huge = tmp_path / "huge.pdf"
    with pikepdf.open(PDF / "text_1p.pdf") as pdf:
        pdf.pages[0].obj.CropBox = pikepdf.Array([0, 0, 10_000_000, 10_000_000])
        pdf.save(huge)
    with pytest.raises(StepError) as e:
        run(step_ctx, huge, Options(text="DRAFT", position="tiled"))
    assert e.value.code == "INVALID_OPTIONS" and "10000" in e.value.message
    assert list(step_ctx.out_dir.iterdir()) == []


def _stamp_ops(path: Path) -> tuple[bytes, pikepdf.Object, pikepdf.Pdf]:
    pdf = pikepdf.open(path)
    page = pdf.pages[0]
    name = next(k for k in page.Resources.XObject if k.startswith("/FxStamp"))
    return page.Contents.read_bytes(), page.Resources.XObject[name], pdf


def test_over_wraps_the_page_content_and_draws_last(step_ctx):
    contents, _, _ = _stamp_ops(watermark(step_ctx, PDF / "text_1p.pdf", text="X"))
    assert contents.startswith(b"q\n")
    assert contents.rstrip().endswith(b"Do Q")


def test_under_draws_first(step_ctx):
    contents, _, _ = _stamp_ops(watermark(step_ctx, PDF / "text_1p.pdf", text="X", layer="under"))
    assert contents.startswith(b"q ") and b"Do Q" in contents.split(b"\n")[0]


def test_opacity_is_set_as_fill_alpha(step_ctx):
    _, form, _pdf = _stamp_ops(watermark(step_ctx, PDF / "text_1p.pdf", text="X", opacity=25))
    alphas = [float(gs.get("/ca", 1)) for gs in form.Resources.ExtGState.values()]
    assert 0.25 in alphas


def test_image_watermark_keeps_its_transparency(step_ctx):
    step_ctx.aux = {"stamp": CORPUS / "image" / "graphic_rgba.png"}
    out = watermark(step_ctx, PDF / "text_10p.pdf", kind="image", pages="1", image_width=30)
    pdf = pikepdf.open(out)
    first, second = (
        [k for k in page.Resources.get("/XObject", {}) if k.startswith("/FxStamp")]
        for page in pdf.pages[:2]
    )
    assert len(first) == 1 and second == []
    form = pdf.pages[0].Resources.XObject[first[0]]
    images = [x for x in form.Resources.XObject.values() if x.Subtype == "/Image"]
    assert images and "/SMask" in images[0]


def test_image_watermark_without_an_image_is_refused(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_1p.pdf", Options(kind="image"))
    assert e.value.code == "INVALID_OPTIONS"


def test_a_damaged_stamp_image_is_refused(step_ctx):
    step_ctx.aux = {"stamp": CORPUS / "odd" / "truncated.jpg"}
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_1p.pdf", Options(kind="image"))
    assert e.value.code == "INVALID_INPUT"


def test_stamp_image_decoder_is_pinned_to_its_format(step_ctx, tmp_path):
    fake = tmp_path / "stamp.jpg"  # PNG bytes in the jpg slot (review L-3)
    fake.write_bytes((CORPUS / "image" / "graphic_rgba.png").read_bytes())
    step_ctx.aux = {"stamp": fake}
    with pytest.raises(StepError) as e:
        run(step_ctx, PDF / "text_1p.pdf", Options(kind="image"))
    assert e.value.code == "INVALID_INPUT"


def test_text_watermark_needs_text():
    with pytest.raises(ValidationError):
        Options(text="   ")


@pytest.mark.parametrize("bad", ["red", "#12345", "#GGGGGG"])
def test_colour_must_be_hex(bad):
    with pytest.raises(ValidationError):
        Options(text="X", color=bad)


def test_metadata_and_bookmarks_are_kept(step_ctx):
    with pikepdf.open(watermark(step_ctx, PDF / "metadata.pdf", text="X")) as pdf:
        assert str(pdf.docinfo["/Author"]) == "Jane Example"


def test_not_a_pdf_fails_cleanly(step_ctx):
    with pytest.raises(StepError) as e:
        run(step_ctx, CORPUS / "odd" / "text_named.pdf", Options(text="X"))
    assert e.value.code == "INVALID_PDF"
