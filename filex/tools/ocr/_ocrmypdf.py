"""OCRmyPDF runner and input preparation shared by ``ocr.pdf`` and ``scan.clean``.

OCRmyPDF runs as a child process (``python -m ocrmypdf``), not through its Python API, so our
timeout can kill it together with the Tesseract, unpaper and Ghostscript processes it starts:
it gets its own process group inside the step's session (a new session would count as an escape
and the runner's sweep would kill it, ADR-0007 item 2), stdin from ``/dev/null``, no shell, and
its console output goes to the step log only, never into a message.

Settings (ADR-0007 Appendix A, review H2/H4):

* ``--jobs 1`` (``OMP_THREAD_LIMIT=1`` comes from the runner): the heavy slot decides CPU use.
* ``--max-image-mpixels`` and ``--skip-big`` from ``FILEX_MAX_PIXELS`` (``ctx.max_pixels``),
  ``--max-ocr-image-mpixels`` caps the image Tesseract gets (memory), ``--tesseract-timeout``
  per page.
* ``--rasterizer pypdfium``: pages are rendered by pdfium, never by Ghostscript. Ghostscript is
  only reachable when PDF/A is asked for and OCRmyPDF's own PDF/A pass is not enough, so tools
  offering PDF/A declare the ``ghostscript`` engine. Whatever OCRmyPDF reads is first rewritten
  by pikepdf (repaired, decrypted, starting with ``%PDF-``), and checked for ``%PDF-``.
* No lossy JBIG2 (OCRmyPDF 17 no longer offers it); languages are checked against
  ``tesseract --list-langs``, never against configuration.

OCRmyPDF options: https://ocrmypdf.readthedocs.io/en/latest/advanced.html and
``python -m ocrmypdf --help`` (17.13); exit codes: ``ocrmypdf.exceptions.ExitCode``.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError

from filex.formats import FORMATS
from filex.registry import StepContext, StepError
from filex.tools._pdf import open_pdf, save

IMAGES = ("jpg", "png", "tiff", "webp", "gif", "bmp")  # no HEIC: pillow-heif is not in the image
MAX_LANGUAGES = 4  # every extra language slows Tesseract down and adds misreads
NOT_LANGUAGES = frozenset({"osd", "equ"})  # orientation and equation data, not languages
TESSERACT_TIMEOUT_S = 120  # per page; a page that takes longer comes out without text
MAX_OCR_MPIXELS = 30  # images above this are downsampled for Tesseract only (memory)
DEFAULT_IMAGE_DPI = 300  # images that claim no credible resolution
# Tesseract's orientation confidence needed to turn a page. OCRmyPDF's default (14) left most
# pages alone: on 48 generated scans (150 and 300 dpi, 4 orientations) every orientation was
# detected right, at confidence 4.7 to 15.6, and none wrong. The analyst's benchmark tunes it.
ROTATE_THRESHOLD = 5

# Warnings and errors this module raises; a tool using it declares them.
WARNINGS = ("INPUT_REPAIRED", "RESTRICTIONS_REMOVED")
ERRORS = ("INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES", "SIGNED_PDF")

# OCRmyPDF exit codes (ocrmypdf.exceptions.ExitCode)
EXIT_INPUT_FILE, EXIT_ENCRYPTED, EXIT_PDFA_FAILED = 2, 8, 10

_CODE = re.compile(r"[a-z]{3}(_[a-z]+)*")


def _normalize_languages(text: str) -> str:
    codes = [c for c in re.split(r"[\s,+]+", text.strip().lower()) if c]
    if not codes:
        raise PydanticCustomError("languages", "Enter at least one language, like eng.")
    for code in codes:
        if not _CODE.fullmatch(code):
            raise PydanticCustomError(
                "languages",
                '"{code}" is not a language code. Use Tesseract codes like eng, rus or deu.',
                {"code": code},
            )
    if len(set(codes)) != len(codes):
        raise PydanticCustomError("languages", "A language is listed twice.")
    if len(codes) > MAX_LANGUAGES:
        raise PydanticCustomError(
            "languages", f"Choose at most {MAX_LANGUAGES} languages; each one slows OCR down."
        )
    return "+".join(codes)


# Options field: "eng", "eng+rus" or "eng, rus" in, "eng+rus" stored.
Languages = Annotated[
    str,
    AfterValidator(_normalize_languages),
    Field(max_length=100, json_schema_extra={"examples": ["eng", "eng+rus"]}),
]


def installed_languages() -> list[str]:
    """The languages Tesseract has data for, from ``tesseract --list-langs``."""
    posix = os.name == "posix"
    try:
        out = subprocess.run(  # one process, no children: run() kills it on timeout
            ["tesseract", "--list-langs"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            process_group=0 if posix else None,
            creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise StepError("ENGINE_FAILED", "The OCR engine is not available.") from e
    # First line is 'List of available languages in "...":'
    return sorted({ln.strip() for ln in out.splitlines()[1:] if ln.strip()} - NOT_LANGUAGES)


def check_languages(languages: str, installed: Sequence[str]) -> str:
    """``languages`` (normalized, ``eng+rus``) if every code is installed; else INVALID_OPTIONS
    naming the missing ones and what is installed."""
    missing = [c for c in languages.split("+") if c not in installed]
    if missing:
        names = ", ".join(missing)
        raise StepError(
            "INVALID_OPTIONS",
            f"OCR language {names} is not installed. Installed: {', '.join(installed)}.",
        )
    return languages


def format_of(src: Path) -> str:
    """The input's format. The runner names scratch inputs after the sniffed format, so the
    suffix is the format, not the user's file name."""
    suffix = src.suffix.lower()
    for fmt in ("pdf", *IMAGES):
        if suffix in FORMATS[fmt].extensions:
            return fmt
    raise StepError("INVALID_INPUT", "This file type cannot be read by the OCR tools.")


def prepare(ctx: StepContext, src: Path, work: Path, what: str) -> Path:
    """``work/input.pdf``: a PDF input rewritten by pikepdf (repaired, restrictions removed,
    signature checked), or an image input turned into a PDF. The file starts with ``%PDF-``."""
    target = work / "input.pdf"
    if format_of(src) == "pdf":
        with open_pdf(ctx, src, what) as pdf:
            form = pdf.Root.get("/AcroForm")
            if form is not None and int(form.get("/SigFlags", 0)) & 1:
                raise StepError(
                    "SIGNED_PDF",
                    f"{what} is digitally signed; changing it would break the signature. "
                    "Use a copy without the signature.",
                )
            save(pdf, target, what)
    else:
        _image_to_pdf(ctx, src, target, what)
    with target.open("rb") as f:
        if f.read(5) != b"%PDF-":  # never hand anything else to OCRmyPDF or Ghostscript
            raise StepError("INVALID_PDF", f"{what} is not a readable PDF.")
    return target


def _image_to_pdf(ctx: StepContext, src: Path, target: Path, what: str) -> None:
    """Every frame of the image as a page, at the image's resolution (300 dpi if it claims
    none). Pixel and frame limits are checked first, as in ``image.to-pdf``."""
    import img2pdf
    from PIL import Image

    from filex.tools.convert.image_to_pdf import _check, open_image

    Image.MAX_IMAGE_PIXELS = ctx.max_pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        _check(ctx, src, what)
        with open_image(src) as im:
            dpi = float(im.info.get("dpi", (0, 0))[0] or 0)
            frames = getattr(im, "n_frames", 1)
    if frames > ctx.max_pages:
        raise StepError(
            "TOO_MANY_PAGES", f"{what} has {frames} pages; the limit is {ctx.max_pages}."
        )
    dpi = round(dpi) if 72 <= dpi <= 2400 else DEFAULT_IMAGE_DPI
    try:
        with target.open("wb") as f:
            img2pdf.convert(
                str(src),
                layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi)),
                rotation=img2pdf.Rotation.ifvalid,
                outputstream=f,
            )
    except (img2pdf.ImageOpenError, ValueError, OSError) as e:
        target.unlink(missing_ok=True)
        raise StepError("INVALID_INPUT", f"{what} could not be read as an image.") from e


def has_text(path: Path) -> list[bool]:
    """Per page: does it already have text (what OCRmyPDF's skip mode skips)?"""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        found = []
        for i in range(len(pdf)):
            page = pdf[i]
            text = page.get_textpage()
            found.append(text.count_chars() > 0)
            text.close()
            page.close()
        return found
    finally:
        pdf.close()


def ocrmypdf_args(
    src: Path,
    dst: Path,
    *,
    languages: str,
    mode: str,
    max_pixels: int,
    output_type: str = "pdf",
    optimize: int = 1,
    deskew: bool = False,
    rotate_pages: bool = False,
    clean: str | None = None,
    ocr: bool = True,
    sidecar: Path | None = None,
) -> list[str]:
    """The OCRmyPDF command line. ``clean`` is ``"ocr"`` (unpaper cleans the image Tesseract
    reads only) or ``"final"`` (the cleaned image replaces the page, black scanner borders
    removed). ``ocr=False`` keeps deskew, rotation and cleaning but adds no text: a Tesseract
    timeout of 0 skips recognition, while deskew and rotation use their own timeout."""
    mpixels = f"{max_pixels / 1_000_000:g}"
    args = [
        sys.executable,
        "-P",  # no script or working directory on sys.path, no user site (as the step child)
        "-s",
        "-m",
        "ocrmypdf",
        "--jobs",
        "1",
        "--language",
        languages,
        "--mode",
        mode,
        "--output-type",
        output_type,
        "--optimize",
        str(optimize),
        "--rasterizer",
        "pypdfium",
        "--max-image-mpixels",
        mpixels,
        "--skip-big",
        mpixels,
        "--max-ocr-image-mpixels",
        str(MAX_OCR_MPIXELS),
        "--tesseract-timeout",
        str(TESSERACT_TIMEOUT_S if ocr else 0),
    ]
    if deskew:
        args.append("--deskew")
    if rotate_pages:
        args += ["--rotate-pages", "--rotate-pages-threshold", str(ROTATE_THRESHOLD)]
    if clean == "ocr":
        args.append("--clean")
    elif clean == "final":
        # OCRmyPDF's conservative unpaper preset without --no-blackfilter: solid black areas
        # touching the page edges (scanner lid, book gutter) are wiped; content stays.
        preset = (
            "--layout none --mask-scan-size 100 --no-border-align --no-mask-center"
            " --no-grayfilter --no-deskew"
        )
        args += ["--clean-final", "--unpaper-args", preset]
    if sidecar is not None:
        args += ["--sidecar", str(sidecar)]
    return [*args, str(src), str(dst)]


def run_ocrmypdf(args: list[str], cwd: Path, timeout_s: float, what: str, verb: str) -> int:
    """Run OCRmyPDF in its own process group and kill the group when it ends or times out.
    Returns the exit code when there is an output (0, or 10: PDF/A conversion failed but a
    regular PDF was written); other codes become our own StepError. ``verb`` completes
    "could not be ..." in messages."""
    posix = os.name == "posix"
    try:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            process_group=0 if posix else None,
            creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError as e:
        raise StepError("ENGINE_FAILED", "The OCR engine could not start.") from e
    try:
        _, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise StepError("ENGINE_FAILED", f"{what} took too long to process.") from e
    finally:
        _kill_group(proc)
    log = err.decode("utf-8", "replace")
    if proc.returncode in (0, EXIT_PDFA_FAILED):
        return proc.returncode
    sys.stderr.write(log[-3000:])
    raise failure(proc.returncode, log, what, verb)


def failure(code: int, log: str, what: str, verb: str) -> StepError:
    """Our message for a failed run, judged from the exit code and the console output (which
    itself never reaches the user)."""
    if "decompression bomb" in log.lower() or "DecompressionBomb" in log:
        return StepError("INVALID_INPUT", f"{what} contains an image that is too large.")
    if code == EXIT_ENCRYPTED:
        return StepError(
            "ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first."
        )
    if code == EXIT_INPUT_FILE:
        return StepError("INVALID_PDF", f"{what} could not be {verb}. It may be damaged.")
    return StepError("ENGINE_FAILED", f"{what} could not be {verb}.")


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the whole group (Tesseract or unpaper may outlive OCRmyPDF) and reap the child.
    Usually the group is already empty."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def pages(n: int) -> str:
    return f"{n} page" if n == 1 else f"{n} pages"
