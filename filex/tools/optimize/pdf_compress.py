"""``pdf.compress``: shrink a PDF with Ghostscript presets plus a pikepdf clean-up (spec 6.2,
plan task 23).

Two candidates are made and the smaller one is kept:

* **lossless**: pikepdf rewrite of the input with unused resources dropped and object streams
  generated. This is also the file Ghostscript reads, so Ghostscript always gets a repaired,
  decrypted file that starts with ``%PDF-`` (ADR-0007 Appendix A).
* **lossy**: Ghostscript ``pdfwrite`` with the preset's image resolution and JPEG quality
  (optionally grayscale), then the same pikepdf rewrite. Text stays text, so it stays selectable.

Grayscale is a request about the result, so with it only the lossy candidate counts.
Ghostscript drops AcroForm fields, so a PDF with form fields gets only the lossless candidate
and ``LOSSLESS_ONLY``. Ghostscript also rewrites the Info dictionary and XMP; the clean-up puts
the input's back (removing metadata is ``pdf.metadata-strip``'s job, not this tool's). If no
candidate is smaller than the input, the input comes back byte for byte with ``NOT_SMALLER``
(an owner-restricted input comes back as the unrestricted rewrite instead, because the
restrictions are always removed). Sizes need no report: the job already records the size of
every input and output, which is what the UI's "-62%, 8.4 MB -> 3.2 MB" is computed from.

**Target size** (plan task 25): with ``targetMb`` the level is ignored. If the lossless
candidate (or the input) is already under the target, that is the answer: no quality is lost.
Otherwise :func:`search_target` walks one quality scale (:func:`settings_at`: 300 dpi and
QFactor 0.25 at the top, EXTREME at the bottom, LOW/MEDIUM/HIGH on the way) with at most
``MAX_PASSES`` Ghostscript passes inside ``TARGET_BUDGET_S``: EXTREME first (if even that is too
big, it is the smallest result and the warning ``TARGET_NOT_REACHED`` says "Best we could do:
2.4 MB (target 2 MB) for ..."), then bisection towards better quality, keeping the largest
result under the target. The never-grow rule still applies to whatever it returns.

Ghostscript (review H4, ADR-0007 Appendix A): ``-dSAFER -dBATCH -dNOPAUSE -dQUIET``,
``--permit-file-read=<input>``, input and output inside a private temp dir (``TMPDIR`` is the
step's scratch dir in the worker), no shell, stdin from ``/dev/null``, own process group killed
on timeout. Its output never reaches a message.

Ghostscript parameters: https://ghostscript.readthedocs.io/en/latest/VectorDevices.html
(``ColorImageResolution``, ``ColorImageDownsampleThreshold``, ``PassThroughJPEGImages``,
``ColorConversionStrategy``, ``AutoRotatePages``) and the distiller ``ColorImageDict`` /
``ColorACSImageDict`` ``QFactor`` set with ``setdistillerparams``.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from filex.registry import Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools import _gs
from filex.tools._pdf import label, open_pdf

Level = Literal["LOW", "MEDIUM", "HIGH", "EXTREME"]


@dataclass(frozen=True)
class Settings:
    """One Ghostscript pass: color and gray images above ``dpi`` (times the threshold) are
    downsampled to it and every JPEG is re-encoded at ``qfactor`` (lower is better quality:
    0.15 is near lossless, 0.76 is Distiller's medium, 1.5 is rough)."""

    dpi: int
    qfactor: float
    grayscale: bool = False


# (dpi, QFactor). Starting values measured on the corpus scans; the analyst's benchmark (plan
# task 24) tunes them. QFactor 1.0 is avoided: Ghostscript 10.0 gives larger files at 1.0 than
# at 0.76.
PRESETS: dict[str, tuple[int, float]] = {
    "LOW": (200, 0.40),
    "MEDIUM": (100, 1.2),
    "HIGH": (85, 1.5),
    "EXTREME": (72, 2.0),
}
MIN_DPI, MAX_DPI = 36, 600
DOWNSAMPLE_THRESHOLD = 1.1  # downsample images above 1.1 x dpi (Ghostscript's default is 1.5)
GS_TIMEOUT_S = 270  # inside the tool's 300 s limit, leaving time for the pikepdf passes
MIN_TARGET_MB, MAX_TARGET_MB = 0.05, 1024
MAX_PASSES = 6  # Ghostscript passes per file in target mode (plan task 25)
TARGET_BUDGET_S = 240  # all target-mode passes together, inside the 300 s tool limit
CLOSE_ENOUGH = 0.9  # a result at 90 % of the target or more ends the search
# The quality scale of target mode: from TOP_DPI / TOP_QFACTOR (t = 0) geometrically down to
# EXTREME (t = 1). LOW, MEDIUM and HIGH lie on it (t ~ 0.28, 0.77, 0.88).
TOP_DPI, TOP_QFACTOR = 300, 0.25


class Options(ToolOptions):
    level: Level = Field(
        "MEDIUM",
        title="Compression level",
        json_schema_extra={
            "x-enum-titles": ["Low", "Medium", "High", "Extreme"],
            "x-enum-descriptions": [
                "Best quality, images at 200 dpi",
                "Balanced, 100 dpi",
                "Smaller, 85 dpi",
                "Smallest, 72 dpi",
            ],
        },
    )
    grayscale: bool = Field(False, title="Convert to grayscale")
    max_dpi: int | None = Field(
        None, ge=MIN_DPI, le=MAX_DPI, title="Image resolution cap (dpi, blank for the level's)"
    )
    target_mb: float | None = Field(
        None,
        ge=MIN_TARGET_MB,
        le=MAX_TARGET_MB,
        title="Target size (MB, blank to use the level)",
        json_schema_extra={"x-unit": "MB"},
    )


def settings_for(opts: Options) -> Settings:
    dpi, qfactor = PRESETS[opts.level]
    if opts.max_dpi is not None:
        dpi = min(dpi, opts.max_dpi)
    return Settings(dpi=dpi, qfactor=qfactor, grayscale=opts.grayscale)


def settings_at(t: float, max_dpi: int | None, grayscale: bool) -> Settings:
    """Target mode's quality scale: ``t`` = 0 is the best quality tried, 1 is EXTREME."""
    low_dpi, low_q = PRESETS["EXTREME"]
    dpi = round(TOP_DPI * (low_dpi / TOP_DPI) ** t)
    qfactor = round(TOP_QFACTOR * (low_q / TOP_QFACTOR) ** t, 2)
    if max_dpi is not None:
        dpi = min(dpi, max_dpi)
    return Settings(dpi=dpi, qfactor=qfactor, grayscale=grayscale)


@dataclass(frozen=True)
class Found:
    settings: Settings
    size: int
    reached: bool  # size <= target


def search_target(
    target: int,
    attempt: Callable[[Settings, float], int],
    discard: Callable[[Settings], None],
    *,
    max_dpi: int | None = None,
    grayscale: bool = False,
    max_passes: int = MAX_PASSES,
    budget_s: float = TARGET_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> Found:
    """Find the best quality whose output is at most ``target`` bytes.

    ``attempt(settings, timeout_s)`` runs one pass and returns its size; ``discard(settings)``
    is called for every attempted result that is not returned. EXTREME goes first: if it is too
    big, it is returned with ``reached=False``. Otherwise bisection between it and the top of
    the scale keeps the largest result under the target (sizes need not fall with quality). It
    stops at ``max_passes``, at a result within ``CLOSE_ENOUGH`` of the target, or when the
    time left is under twice the slowest pass so far. A failing first pass raises; a later one
    ends the search with what was found."""
    deadline = clock() + budget_s
    slowest = 0.0

    def run(t: float) -> Found:
        nonlocal slowest
        s = settings_at(t, max_dpi, grayscale)
        started = clock()
        size = attempt(s, max(deadline - started, 1.0))
        slowest = max(slowest, clock() - started)
        return Found(s, size, size <= target)

    best = run(1.0)
    if not best.reached:
        return best
    passes, lo, hi = 1, 0.0, 1.0
    while passes < max_passes and best.size < CLOSE_ENOUGH * target:
        if deadline - clock() < 2 * slowest:
            break
        t = (lo + hi) / 2
        if settings_at(t, max_dpi, grayscale) == best.settings:
            hi = t  # the dpi cap can map two points to one pass: no need to run it again
            continue
        try:
            r = run(t)
        except StepError:
            break
        passes += 1
        if r.reached:
            hi = t
            if r.size > best.size:
                best, r = r, best
        else:
            lo = t
        discard(r.settings)
    return best


def size_text(n: int) -> str:
    """A size for the target-mode message: "72 KB", "2 MB", "2.4 MB", "2.04 MB"."""
    if n < 100_000:
        return f"{max(math.ceil(n / 1000), 1)} KB"
    return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + " MB"


def gs_args(s: Settings, src: Path, dst: Path, gs: str = "gs") -> list[str]:
    """The Ghostscript command line for one pass. Never a shell string."""
    image_dict = f"<< /QFactor {s.qfactor:.2f} /Blend 1 /HSamples [2 1 1 2] /VSamples [2 1 1 2] >>"
    distiller = (
        f"<< /ColorImageDict {image_dict} /ColorACSImageDict {image_dict}"
        f" /GrayImageDict {image_dict} /GrayACSImageDict {image_dict} >> setdistillerparams"
    )
    args = [
        *_gs.base_args(src, gs),
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dPassThroughJPEGImages=false",
    ]
    for kind in ("Color", "Gray"):
        args += [
            f"-dDownsample{kind}Images=true",
            f"-d{kind}ImageDownsampleType=/Bicubic",
            f"-d{kind}ImageResolution={s.dpi}",
            f"-d{kind}ImageDownsampleThreshold={DOWNSAMPLE_THRESHOLD}",
        ]
    # Bilevel scans lose legibility fast, so they keep at least 200 dpi.
    args += ["-dDownsampleMonoImages=true", f"-dMonoImageResolution={max(2 * s.dpi, 200)}"]
    if s.grayscale:
        args += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"]
    return [*args, f"-sOutputFile={dst}", "-c", distiller, "-f", str(src)]


def run_ghostscript(args: list[str], timeout_s: float, what: str) -> None:
    """One Ghostscript run with this tool's messages (see :func:`filex.tools._gs.run_ghostscript`)."""
    _gs.run_ghostscript(
        args,
        timeout_s,
        failed=f"{what} could not be compressed.",
        slow=f"{what} took too long to compress.",
        unavailable="The compression engine could not start.",
    )


def save_compact(pdf: Any, target: Path, what: str) -> Path:
    """Lossless pikepdf rewrite: unused resources dropped, streams compressed, object streams
    generated, no encryption."""
    import pikepdf

    try:
        pdf.remove_unreferenced_resources()
        pdf.save(
            target,
            encryption=False,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
        )
    except pikepdf.PdfError as e:
        target.unlink(missing_ok=True)
        raise StepError("INVALID_PDF", f"{what} is damaged and could not be saved.") from e
    return target


def ghostscript_pass(s: Settings, src: Path, work: Path, what: str, timeout_s: float) -> Path:
    """One Ghostscript pass over ``src`` (a file inside ``work``) plus the pikepdf clean-up,
    which also puts back ``src``'s Info dictionary and XMP metadata (Ghostscript rewrites
    them). Returns the cleaned file in ``work``."""
    import pikepdf

    raw = work / "gs.pdf"
    raw.unlink(missing_ok=True)
    run_ghostscript(gs_args(s, src, raw), timeout_s, what)
    try:
        with pikepdf.open(raw) as pdf, pikepdf.open(src) as orig:
            if "/Info" in orig.trailer:
                pdf.trailer.Info = pdf.copy_foreign(orig.trailer.Info)
            if "/Metadata" in orig.Root:
                pdf.Root.Metadata = pdf.copy_foreign(orig.Root.Metadata)
            return save_compact(pdf, work / "lossy.pdf", what)
    except pikepdf.PdfError as e:
        raise StepError("ENGINE_FAILED", f"{what} could not be compressed.") from e
    finally:
        raw.unlink(missing_ok=True)


def smallest(candidates: list[Path], limit: int) -> Path | None:
    """The smallest candidate strictly below ``limit`` bytes, else None (never grow)."""
    best = min(candidates, key=lambda p: p.stat().st_size, default=None)
    return best if best is not None and best.stat().st_size < limit else None


def mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 100_000 else f"{max(n // 1000, 1)} KB"


@tool(
    id="pdf.compress",
    title="Compress PDF",
    summary="Make a PDF smaller by lowering image resolution and quality. Text stays selectable.",
    category="optimize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["ghostscript", "pikepdf"],
    limits=Limits(timeout_s=300, memory_mb=1024),
    examples=[Example(inputs=["pdf/scanned_3p.pdf"], options={"level": "MEDIUM"})],
    warnings=[
        "INPUT_REPAIRED",
        "RESTRICTIONS_REMOVED",
        "NOT_SMALLER",
        "LOSSLESS_ONLY",
        "TARGET_NOT_REACHED",
    ],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["shrink", "reduce", "smaller", "size", "optimize", "squeeze", "email"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    what = label(ctx)
    size_in = src.stat().st_size
    out = ctx.out_dir / "compressed.pdf"
    target = None if opts.target_mb is None else int(opts.target_mb * 1_000_000)
    with tempfile.TemporaryDirectory(prefix="compress-") as tmp:
        work = Path(tmp)
        with open_pdf(ctx, src, what) as pdf:
            restricted = pdf.is_encrypted
            has_form = bool(pdf.acroform.exists and pdf.acroform.fields)
            lossless = save_compact(pdf, work / "lossless.pdf", what)
        if has_form:  # Ghostscript drops form fields: never trade a working form for bytes
            ctx.warn(
                "LOSSLESS_ONLY",
                f"{what} has fillable form fields, which image compression would remove; "
                "only a lossless clean-up was done.",
            )
            candidates = [lossless]
        elif (
            target is not None
            and not opts.grayscale
            and (min(size_in, lossless.stat().st_size) <= target)
        ):
            candidates = [lossless]  # already under the target without losing anything
        elif target is not None:
            found = target_search(ctx, opts, target, lossless, work, what)
            candidates = [found_path(work, found.settings)]
            if not found.reached and not opts.grayscale:
                candidates.append(lossless)
        else:
            ctx.progress(0.1, "Recompressing images")
            lossy = ghostscript_pass(settings_for(opts), lossless, work, what, GS_TIMEOUT_S)
            candidates = [lossy] if opts.grayscale else [lossy, lossless]
        ctx.progress(0.9, "Saving")
        best = smallest(candidates, size_in)
        if best is not None:
            shutil.move(best, out)
        else:
            ctx.warn(
                "NOT_SMALLER",
                f"{what} is already compact ({mb(size_in)}); compressing would not make it "
                "smaller, so its size was kept.",
            )
            if restricted:  # keep the promise that restrictions are removed
                shutil.move(min(candidates, key=lambda p: p.stat().st_size), out)
            else:
                shutil.copyfile(src, out)
    size_out = out.stat().st_size
    if target is not None and size_out > target:
        whom = what if what.startswith('"') else "the file"
        ctx.warn(
            "TARGET_NOT_REACHED",
            f"Best we could do: {size_text(size_out)} (target {size_text(target)}) for {whom}.",
        )
    return [out]


def found_path(work: Path, s: Settings) -> Path:
    return work / f"try-{s.dpi}-{s.qfactor:.2f}.pdf"


def target_search(
    ctx: StepContext, opts: Options, target: int, lossless: Path, work: Path, what: str
) -> Found:
    """:func:`search_target` with real Ghostscript passes over the lossless rewrite; each
    result stays in ``work`` as :func:`found_path` until the search discards it."""
    tries = 0

    def attempt(s: Settings, timeout_s: float) -> int:
        nonlocal tries
        tries += 1
        ctx.progress(0.1 + 0.8 * (tries - 1) / MAX_PASSES, "Recompressing images")
        path = ghostscript_pass(s, lossless, work, what, min(timeout_s, GS_TIMEOUT_S))
        return path.replace(found_path(work, s)).stat().st_size

    return search_target(
        target,
        attempt,
        lambda s: found_path(work, s).unlink(missing_ok=True),
        max_dpi=opts.max_dpi,
        grayscale=opts.grayscale,
    )
