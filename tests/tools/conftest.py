"""Shared fixtures for the page tools' tests: a fake step context and a page reader."""

import re
from pathlib import Path

import pytest

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
PDF = CORPUS / "pdf"


class StepCtx:
    """What the worker passes to ``run()`` (filex.worker.step.Context), recording warnings."""

    def __init__(self, out_dir: Path, names: list[str] | None = None) -> None:
        self.out_dir, self.aux, self.target = out_dir, {}, None
        self.input_names = names or []
        self.max_pages = 2000
        self.max_pixels = 100_000_000
        self.warnings: list[tuple[str, str]] = []

    def progress(self, fraction: float, message: str = "") -> None:
        assert 0.0 <= fraction <= 1.0

    def warn(self, code: str, message: str) -> None:
        self.warnings.append((code, message))


@pytest.fixture(autouse=True)
def _restore_pillow_pixel_limit():
    """Image tools set Pillow's global ``MAX_IMAGE_PIXELS`` from ``ctx.max_pixels`` (each step is
    its own process in the worker); here every test gets it back."""
    try:
        from PIL import Image
    except ImportError:
        yield
        return
    saved = Image.MAX_IMAGE_PIXELS
    yield
    Image.MAX_IMAGE_PIXELS = saved


@pytest.fixture
def step_ctx(tmp_path) -> StepCtx:
    out = tmp_path / "out"
    out.mkdir()
    return StepCtx(out)


def _numbers(pdf) -> list[int]:  # a pikepdf.Pdf (not in every worker image)
    """The "page N of 10" number printed on each page of text_10p.pdf, in output order."""
    found = []
    for page in pdf.pages:
        m = re.search(rb"page (\d+) of 10", page.Contents.read_bytes())
        found.append(int(m[1]) if m else 0)
    return found


@pytest.fixture
def page_numbers():
    return _numbers
