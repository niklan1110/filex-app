"""``ocr.pdf``: make scanned PDFs and images searchable with OCRmyPDF and Tesseract 5 (spec 6.5,
plan task 39).

The text layer is invisible and sits over the page image, so the page looks the same and its
text can be searched, selected and copied. Pages that already have text are left alone by
default (``skip``, with a ``TEXT_PAGES_SKIPPED`` warning); ``redo`` replaces an earlier OCR
layer and ``force`` turns every page into an image and reads it again (vector text is lost).
Deskew straightens pages before reading them (not with ``redo``); auto-rotate turns pages the
right way up; ``clean`` lets unpaper clean the image Tesseract reads, the page itself is kept.
Output is a regular PDF or PDF/A-2b; ``PDFA_FAILED`` when a file cannot be made PDF/A (it then
comes back as a regular PDF). The optional sidecar is the recognised text as a ``.txt`` report.

Images become one PDF each (every TIFF frame a page) before OCR. Engine settings, limits and
the process handling are in :mod:`filex.tools.ocr._ocrmypdf`.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from filex.registry import (
    Example,
    Files,
    Limits,
    Output,
    Report,
    StepContext,
    StepError,
    ToolOptions,
    tool,
)
from filex.tools._pdf import label
from filex.tools.ocr import _ocrmypdf as om

TIMEOUT_S = 1800
ENGINE_TIMEOUT_S = TIMEOUT_S - 60  # leaves time to prepare the input and move the outputs
OPTIMIZE = {"none": 0, "lossless": 1, "lossy": 2, "strong": 3}


class Options(ToolOptions):
    languages: om.Languages = Field("eng", title="Languages (for example eng or eng+rus)")
    mode: Literal["skip", "redo", "force"] = Field(
        "skip",
        title="Pages that already have text",
        json_schema_extra={
            "x-enum-titles": ["Skip them", "Redo old OCR", "Force OCR"],
            "x-enum-descriptions": [
                "Leave them as they are",
                "Replace an earlier OCR layer",
                "Read every page again (turns pages into images)",
            ],
        },
    )
    deskew: bool = Field(False, title="Straighten crooked pages")
    rotate_pages: bool = Field(False, title="Turn pages the right way up")
    clean: bool = Field(False, title="Clean the scan before reading (page stays as it is)")
    output: Literal["pdf", "pdfa"] = Field(
        "pdf",
        title="Output",
        json_schema_extra={
            "x-enum-titles": ["PDF", "PDF/A-2b"],
            "x-enum-descriptions": ["Standard PDF", "For long-term archiving"],
        },
    )
    optimize: Literal["none", "lossless", "lossy", "strong"] = Field(
        "lossless",
        title="Optimize images",
        json_schema_extra={
            "x-enum-titles": ["Off", "Lossless", "Smaller", "Smallest"],
            "x-enum-descriptions": [
                "Do not optimize",
                "Same image quality",
                "Slightly lower image quality",
                "Lower image quality",
            ],
        },
    )
    sidecar: bool = Field(False, title="Also give me the text as a .txt file")

    @model_validator(mode="after")
    def _redo_keeps_pages(self) -> Options:
        if self.mode == "redo" and self.deskew:
            raise ValueError(
                "Straightening pages changes them, so it cannot be combined with replacing an "
                "earlier OCR layer. Choose 'Read every page again' or turn straightening off."
            )
        return self


@tool(
    id="ocr.pdf",
    title="OCR (searchable PDF)",
    summary="Recognise the text in scans and photos so the PDF can be searched and copied.",
    category="ocr",
    group="ocr",
    inputs=["pdf", *om.IMAGES],
    outputs=["pdf", "txt"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=[
        "ocrmypdf",
        "tesseract",
        "unpaper",
        "ghostscript",
        "pikepdf",
        "pypdfium2",
        "pillow",
        "img2pdf",
    ],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=1536),
    examples=[Example(inputs=["pdf/scanned_3p.pdf"], options={"languages": "eng"})],
    warnings=[*om.WARNINGS, "TEXT_PAGES_SKIPPED", "PDFA_FAILED"],
    errors=list(om.ERRORS),
    keywords=["ocr", "searchable", "recognize", "tesseract", "scan", "text", "pdfa", "copy"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path | Output]:
    what = label(ctx)
    languages = om.check_languages(opts.languages, om.installed_languages())
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        work = Path(tmp)
        ctx.progress(0.02, "Preparing")
        pdf = om.prepare(ctx, src, work, what)
        if opts.mode == "skip":
            _warn_text_pages(ctx, pdf, what)
        sidecar = work / "text.txt" if opts.sidecar else None
        args = om.ocrmypdf_args(
            pdf,
            work / "ocr.pdf",
            languages=languages,
            mode=opts.mode,
            max_pixels=ctx.max_pixels,
            output_type=opts.output,
            optimize=OPTIMIZE[opts.optimize],
            deskew=opts.deskew,
            rotate_pages=opts.rotate_pages,
            clean="ocr" if opts.clean else None,
            sidecar=sidecar,
        )
        ctx.progress(0.05, "Reading the text")
        code = om.run_ocrmypdf(args, work, ENGINE_TIMEOUT_S, what, "read")
        if code == om.EXIT_PDFA_FAILED:
            ctx.warn(
                "PDFA_FAILED",
                f"{what} could not be made PDF/A; it was saved as a regular searchable PDF.",
            )
        out: list[Path | Output] = [
            Path(shutil.move(work / "ocr.pdf", ctx.out_dir / "searchable.pdf"))
        ]
        if sidecar is not None:
            if not sidecar.is_file():
                raise StepError("ENGINE_FAILED", f"{what}: the text file was not created.")
            out.append(Report(Path(shutil.move(sidecar, ctx.out_dir / "text.txt"))))
        return out


def _warn_text_pages(ctx: StepContext, pdf: Path, what: str) -> None:
    flags = om.has_text(pdf)
    if any(flags):
        ctx.warn(
            "TEXT_PAGES_SKIPPED",
            f"{what}: pages that already had text were left as they are ({sum(flags)} of "
            f"{len(flags)}). Choose 'Replace an earlier OCR layer' or 'Read every page again' "
            "to read them anyway.",
        )
