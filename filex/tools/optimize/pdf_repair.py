"""``pdf.repair``: rebuild a damaged PDF (spec 6.2, plan task 27).

Two attempts, the second automatic:

1. **qpdf** (through pikepdf): opening reconstructs a broken cross-reference table and recovers
   stream lengths; a full rewrite then writes clean objects. qpdf's warnings say what it fixed;
   they are sorted into our own plain sentences (``INPUT_REPAIRED``), never copied into a message.
   The rewrite is re-opened and must read back without warnings and with the same page count.
2. **Ghostscript re-write**, only when qpdf cannot read the file, finds no pages or cannot write
   it: Ghostscript's PDF interpreter has its own repair and re-draws every page it can read
   into a new PDF. Form fields, bookmarks and attachments may not survive, so the result carries
   ``FULL_REBUILD`` saying so and how many pages came back. If Ghostscript fails too, the file
   is ``INVALID_PDF``.

A clean input comes back as a fresh rewrite with ``NOTHING_TO_REPAIR``. The output is never
encrypted (owner restrictions are removed, ``RESTRICTIONS_REMOVED``); a password-protected input
is ``ENCRYPTED_PDF`` (decrypt first), because guessing at it is not repair.

Ghostscript runs as in :mod:`filex.tools._gs` (review H4), only on files starting with ``%PDF-``.
qpdf repair: https://qpdf.readthedocs.io/en/stable/qpdf-job.html (damaged files are recovered on
open); pikepdf ``Pdf.get_warnings``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from filex.registry import Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools import _gs
from filex.tools._pdf import label, save

GS_TIMEOUT_S = 270  # inside the tool's 300 s limit, leaving time for the pikepdf passes

# What qpdf's warning text says it fixed -> our sentence. Checked in order; first match wins.
FIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("xref", "cross-reference", "trailer", "reconstruct"),
        "the cross-reference table was rebuilt",
    ),
    (("stream", "length"), "damaged data streams were fixed"),
    (("object", " obj"), "damaged objects were recovered or dropped"),
)
OTHER_FIX = "other damage was fixed"


class Unreadable(Exception):
    """qpdf could not repair the file; the Ghostscript re-write gets a go."""


def describe_fixes(warnings: list[str], src: Path) -> list[str]:
    """Our sentences for qpdf's warnings about ``src`` (whose path the warnings start with),
    unique, in :data:`FIXES` order. qpdf's "file is damaged" header adds nothing."""
    found = set()
    for w in warnings:
        text = w.replace(str(src), "").lower()
        if "file is damaged" in text:
            continue
        found.add(next((s for keys, s in FIXES if any(k in text for k in keys)), OTHER_FIX))
    return [s for s in (*(s for _, s in FIXES), OTHER_FIX) if s in found]


def qpdf_repair(ctx: StepContext, src: Path, out: Path, what: str) -> list[str]:
    """Rewrite ``src`` to ``out`` with qpdf; returns qpdf's warnings. Raises :class:`Unreadable`
    when qpdf cannot produce a good file."""
    import pikepdf

    try:
        pdf = pikepdf.open(src)
    except pikepdf.PasswordError as e:
        raise StepError(
            "ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first."
        ) from e
    except pikepdf.PdfError as e:
        raise Unreadable from e
    with pdf:
        try:
            count = len(pdf.pages)
        except pikepdf.PdfError as e:
            raise Unreadable from e
        if not count:
            raise Unreadable
        check_pages(ctx, count, what)
        warnings = [str(w) for w in pdf.get_warnings()]
        try:
            pdf.save(
                out,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
        except pikepdf.PdfError as e:
            out.unlink(missing_ok=True)
            raise Unreadable from e
        warnings += [str(w) for w in pdf.get_warnings()]
        restricted = pdf.is_encrypted
    try:
        with pikepdf.open(out) as check:
            good = len(check.pages) == count and not check.get_warnings()
    except pikepdf.PdfError:
        good = False
    if not good:
        out.unlink(missing_ok=True)
        raise Unreadable
    if restricted:
        ctx.warn("RESTRICTIONS_REMOVED", f"{what}: editing restrictions were removed.")
    return warnings


def ghostscript_rebuild(ctx: StepContext, src: Path, out: Path, what: str) -> int:
    """Re-draw ``src`` with Ghostscript into ``out``; returns the page count. INVALID_PDF when
    Ghostscript cannot read it either."""
    import pikepdf

    _gs.require_pdf(src, what)
    damaged = f"{what} is too damaged to repair."
    with tempfile.TemporaryDirectory(prefix="repair-") as tmp:
        raw = Path(tmp) / "gs.pdf"
        _gs.run_ghostscript(
            gs_args(src, raw),
            GS_TIMEOUT_S,
            failed=damaged,
            slow=f"{what} took too long to repair.",
            failed_code="INVALID_PDF",
        )
        try:
            with pikepdf.open(raw) as pdf:
                count = len(pdf.pages)
                if not count:
                    raise StepError("INVALID_PDF", damaged)
                check_pages(ctx, count, what)
                save(pdf, out, what)
        except pikepdf.PdfError as e:
            out.unlink(missing_ok=True)
            raise StepError("INVALID_PDF", damaged) from e
    return count


def gs_args(src: Path, dst: Path, gs: str = "gs") -> list[str]:
    """Ghostscript re-write: pages as they are, images not resampled."""
    return [
        *_gs.base_args(src, gs),
        "-dAutoRotatePages=/None",
        "-dPassThroughJPEGImages=true",
        f"-sOutputFile={dst}",
        "-f",
        str(src),
    ]


def check_pages(ctx: StepContext, count: int, what: str) -> None:
    if count > ctx.max_pages:
        raise StepError(
            "TOO_MANY_PAGES", f"{what} has {count} pages; the limit is {ctx.max_pages}."
        )


def pages(n: int) -> str:
    return f"{n} page" if n == 1 else f"{n} pages"


@tool(
    id="pdf.repair",
    title="Repair PDF",
    summary="Fix a damaged PDF that will not open or shows errors, and save a clean copy.",
    category="optimize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=ToolOptions,
    engines=["pikepdf", "ghostscript"],
    limits=Limits(timeout_s=300, memory_mb=1024),
    examples=[Example(inputs=["pdf/broken_xref.pdf"])],
    warnings=["INPUT_REPAIRED", "NOTHING_TO_REPAIR", "FULL_REBUILD", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["fix", "broken", "damaged", "corrupt", "recover", "rebuild", "open"],
)
def run(ctx: StepContext, src: Path, opts: ToolOptions) -> list[Path]:
    what = label(ctx)
    out = ctx.out_dir / "repaired.pdf"
    _gs.require_pdf(src, what)
    try:
        warnings = qpdf_repair(ctx, src, out, what)
    except Unreadable:
        ctx.progress(0.3, "Rebuilding pages")
        count = ghostscript_rebuild(ctx, src, out, what)
        ctx.warn(
            "FULL_REBUILD",
            f"{what} was too damaged to fix in place, so it was rebuilt from the "
            f"{pages(count)} that could be read. Form fields, bookmarks and attachments may "
            "be missing; check the result.",
        )
        return [out]
    if warnings:
        fixed = "; ".join(describe_fixes(warnings, src))
        ctx.warn("INPUT_REPAIRED", f"{what} was damaged and has been repaired: {fixed}.")
    else:
        ctx.warn("NOTHING_TO_REPAIR", f"{what} showed no damage; a clean copy was saved.")
    return [out]
