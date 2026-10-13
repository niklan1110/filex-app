"""``pdf.to-pdfa``: convert to archival PDF/A-1b, 2b or 3b with Ghostscript (spec 6.2, plan
task 27).

Steps:

1. pikepdf opens the input (repair, restrictions removed, page limit) and writes the copy
   Ghostscript reads, so Ghostscript only ever gets a file starting with ``%PDF-``. A signed
   input is refused (``SIGNED_PDF``): the conversion rewrites every byte the signature covers.
2. Ghostscript ``pdfwrite`` with ``-dPDFA=<part>``, ``-sColorConversionStrategy=RGB``, an sRGB
   OutputIntent (Ghostscript's own ``srgb.icc``, an ICC v2 profile, which PDF/A-1 needs) set
   with the pdfmarks of Ghostscript's ``PDFA_def.ps``, and ``-dPDFACompatibilityPolicy=1``: a
   feature the level forbids is dropped and the file stays PDF/A. Ghostscript embeds every font
   (a missing one is replaced by a similar embedded font) and writes the XMP ``pdfaid``.
3. The result is checked for the PDF/A markers veraPDF would look at first
   (:func:`pdfa_problems`: ``pdfaid`` part and conformance, an sRGB ``GTS_PDFA1`` OutputIntent,
   no encryption, every font embedded, and no transparency for PDF/A-1b). A miss fails the job
   rather than hand back a file that only claims to be PDF/A. Full veraPDF validation runs in CI
   (plan task 27, spec A1); it is not in the worker images.

Lossy changes are warnings, found by comparing input and output, not by reading Ghostscript's
console: ``FONTS_SUBSTITUTED``, ``FORM_FIELDS_REMOVED``, ``ATTACHMENTS_REMOVED``,
``JAVASCRIPT_REMOVED``, ``TRANSPARENCY_FLATTENED`` (PDF/A-1b only). Colours are converted to RGB.

Ghostscript runs as in :mod:`filex.tools._gs` (review H4). The only extra file it may read is
the ICC profile, a fixed system file outside the job's control.

Ghostscript PDF/A: https://ghostscript.readthedocs.io/en/latest/VectorDevices.html#creating-a-pdf-a-document
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from filex.registry import Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools import _gs
from filex.tools._pdf import label, open_pdf, save

Level = Literal["1b", "2b", "3b"]
GS_TIMEOUT_S = 270  # inside the tool's 300 s limit, leaving time for the pikepdf passes
# Ghostscript's sRGB profile (ICC v2.1): Debian's ghostscript package puts it here.
ICC_PROFILES = (Path("/usr/share/color/icc/ghostscript/srgb.icc"),)
ICC_GLOB = ("/usr/share/ghostscript", "*/iccprofiles/srgb.icc")  # upstream layout


class Options(ToolOptions):
    level: Level = Field(
        "2b",
        title="PDF/A level",
        description="2b suits most archives; 1b for systems that need the oldest level; "
        "3b allows attached files of any type.",
        json_schema_extra={
            "x-enum-titles": ["PDF/A-1b", "PDF/A-2b", "PDF/A-3b"],
            "x-enum-descriptions": [
                "Oldest level, for strict systems",
                "Suits most archives",
                "Allows attached files of any type",
            ],
        },
    )


def icc_profile() -> Path:
    found = [p for p in ICC_PROFILES if p.is_file()]
    found += sorted(Path(ICC_GLOB[0]).glob(ICC_GLOB[1]))
    if not found:
        raise StepError("ENGINE_FAILED", "The PDF/A colour profile is missing from the engine.")
    return found[0]


def _ps_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def pdfa_def(icc: Path) -> str:
    """The OutputIntent pdfmarks of Ghostscript's ``lib/PDFA_def.ps`` for an RGB profile."""
    return (
        "[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark "
        "[{icc_PDFA} << /N 3 >> /PUT pdfmark "
        f"[{{icc_PDFA}} {_ps_string(str(icc))} (r) file /PUT pdfmark "
        "[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark "
        "[{OutputIntent_PDFA} << /Type /OutputIntent /S /GTS_PDFA1 "
        "/DestOutputProfile {icc_PDFA} /OutputConditionIdentifier (sRGB) >> /PUT pdfmark "
        "[{Catalog} << /OutputIntents [ {OutputIntent_PDFA} ] >> /PUT pdfmark"
    )


def gs_args(level: Level, src: Path, dst: Path, icc: Path, gs: str = "gs") -> list[str]:
    """The Ghostscript command line. Never a shell string."""
    return [
        *_gs.base_args(src, gs),
        f"--permit-file-read={icc}",
        f"-dPDFA={level[0]}",
        "-dPDFACompatibilityPolicy=1",
        "-sColorConversionStrategy=RGB",
        "-dAutoRotatePages=/None",
        "-dEmbedAllFonts=true",
        f"-sOutputFile={dst}",
        "-c",
        pdfa_def(icc),
        "-f",
        str(src),
    ]


# --- inspection (pikepdf objects; also used by the tests)


def _walk(obj: Any, seen: set[tuple[int, int]]) -> Any:
    """Every dictionary and stream reachable from ``obj``, each indirect object once."""
    import pikepdf

    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, pikepdf.Array):
            stack.extend(o)
            continue
        if not isinstance(o, pikepdf.Dictionary | pikepdf.Stream):
            continue
        if o.is_indirect:
            if o.objgen in seen:
                continue
            seen.add(o.objgen)
        yield o
        stack.extend(v for k, v in o.items() if k != "/Parent")


def unembedded_fonts(pdf: Any) -> set[str]:
    """Names of the fonts used by the pages without an embedded font program."""
    seen: set[tuple[int, int]] = set()
    missing = set()
    for page in pdf.pages:
        for o in _walk(page.obj, seen):
            if o.get("/Type") != "/Font" or o.get("/Subtype") in ("/Type3", "/Type0"):
                continue  # Type3 glyphs are drawn by the file; Type0 is checked via its child
            desc = o.get("/FontDescriptor")
            if desc is None or not any(
                k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")
            ):
                missing.add(str(o.get("/BaseFont", "unnamed")).lstrip("/"))
    return missing


def has_transparency(pdf: Any) -> bool:
    """Soft masks, constant alpha below 1, blend modes or transparency groups on any page."""
    import pikepdf

    seen: set[tuple[int, int]] = set()
    for page in pdf.pages:
        for o in _walk(page.obj, seen):
            group = o.get("/Group")
            if isinstance(group, pikepdf.Dictionary) and group.get("/S") == "/Transparency":
                return True
            smask = o.get("/SMask")
            if smask is not None and smask != pikepdf.Name.None_:
                return True
            for k in ("/CA", "/ca"):  # numbers only: a widget's /MK /CA is a caption
                alpha = o.get(k)
                if isinstance(alpha, int | Decimal) and alpha < 1:
                    return True
            if o.get("/BM", pikepdf.Name.Normal) not in (
                pikepdf.Name.Normal,
                pikepdf.Name.Compatible,
            ):
                return True
    return False


def has_javascript(pdf: Any) -> bool:
    import pikepdf

    names = pdf.Root.get("/Names")
    action = pdf.Root.get("/OpenAction")
    return bool(
        (names is not None and "/JavaScript" in names)
        or "/AA" in pdf.Root
        or (isinstance(action, pikepdf.Dictionary) and action.get("/S") == "/JavaScript")
    )


def has_form(pdf: Any) -> bool:
    return bool(pdf.acroform.exists and pdf.acroform.fields)


def is_signed(pdf: Any) -> bool:
    form = pdf.Root.get("/AcroForm")
    return form is not None and int(form.get("/SigFlags", 0)) & 1 == 1


def pdfa_problems(pdf: Any, level: Level) -> list[str]:
    """What a PDF/A-``level`` file must have and ``pdf`` lacks (developer text, for the log)."""
    problems = []
    if pdf.is_encrypted:
        problems.append("encrypted")
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        status = meta.pdfa_status
    if status != level.upper():
        problems.append(f"XMP pdfaid is {status!r}, not {level.upper()!r}")
    intents = [
        i
        for i in pdf.Root.get("/OutputIntents", [])
        if i.get("/S") == "/GTS_PDFA1" and "/DestOutputProfile" in i
    ]
    if not intents:
        problems.append("no GTS_PDFA1 OutputIntent with a profile")
    if fonts := unembedded_fonts(pdf):
        problems.append(f"fonts not embedded: {', '.join(sorted(fonts))}")
    if level == "1b" and has_transparency(pdf):
        problems.append("transparency in PDF/A-1")
    return problems


def _features(pdf: Any) -> dict[str, bool]:
    return {
        "form": has_form(pdf),
        "attachments": bool(len(pdf.attachments)),
        "javascript": has_javascript(pdf),
    }


def _warn_losses(
    ctx: StepContext, what: str, level: Level, before: dict[str, Any], after: dict[str, bool]
) -> None:
    name = f"PDF/A-{level}"
    if fonts := before["fonts"]:
        shown = ", ".join(sorted(fonts)[:3]) + (", ..." if len(fonts) > 3 else "")
        ctx.warn(
            "FONTS_SUBSTITUTED",
            f"{what} uses fonts that are not inside the file ({shown}); similar fonts were "
            "embedded instead, so the text may look slightly different.",
        )
    if before["form"] and not after["form"]:
        ctx.warn(
            "FORM_FIELDS_REMOVED",
            f"{what} had fillable form fields; in the {name} copy they can no longer be filled in.",
        )
    if before["attachments"] and not after["attachments"]:
        ctx.warn(
            "ATTACHMENTS_REMOVED",
            f"{what} had attached files, which {name} does not allow; they were removed.",
        )
    if before["javascript"] and not after["javascript"]:
        ctx.warn(
            "JAVASCRIPT_REMOVED",
            f"{what} had scripts, which PDF/A does not allow; they were removed.",
        )
    if before["transparency"]:
        ctx.warn(
            "TRANSPARENCY_FLATTENED",
            f"{what} has see-through elements, which {name} does not allow; they were "
            "flattened and may look different.",
        )


@tool(
    id="pdf.to-pdfa",
    title="PDF to PDF/A",
    summary="Make an archival PDF/A-1b, 2b or 3b copy with fonts and colours built in.",
    category="optimize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["ghostscript", "pikepdf"],
    limits=Limits(timeout_s=300, memory_mb=1024),
    examples=[Example(inputs=["pdf/text_1p.pdf"], options={"level": "2b"})],
    warnings=[
        "INPUT_REPAIRED",
        "RESTRICTIONS_REMOVED",
        "FONTS_SUBSTITUTED",
        "FORM_FIELDS_REMOVED",
        "ATTACHMENTS_REMOVED",
        "JAVASCRIPT_REMOVED",
        "TRANSPARENCY_FLATTENED",
    ],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES", "SIGNED_PDF"],
    keywords=["pdfa", "archive", "archival", "long-term", "iso", "preserve", "standard"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import pikepdf

    what = label(ctx)
    level = opts.level
    out = ctx.out_dir / "pdfa.pdf"
    with tempfile.TemporaryDirectory(prefix="pdfa-") as tmp:
        work = Path(tmp)
        clean, raw = work / "input.pdf", work / "gs.pdf"
        with open_pdf(ctx, src, what) as pdf:
            if is_signed(pdf):
                raise StepError(
                    "SIGNED_PDF",
                    f"{what} is digitally signed; converting it would break the signature. "
                    "Use a copy without the signature.",
                )
            before: dict[str, Any] = _features(pdf)
            before["fonts"] = unembedded_fonts(pdf)
            before["transparency"] = level == "1b" and has_transparency(pdf)
            save(pdf, clean, what)
        _gs.require_pdf(clean, what)
        ctx.progress(0.1, f"Converting to PDF/A-{level}")
        _gs.run_ghostscript(
            gs_args(level, clean, raw, icc_profile()),
            GS_TIMEOUT_S,
            failed=f"{what} could not be converted to PDF/A.",
            slow=f"{what} took too long to convert to PDF/A.",
        )
        ctx.progress(0.9, "Checking the result")
        try:
            with pikepdf.open(raw) as result:
                problems = pdfa_problems(result, level)
                after = _features(result)
        except pikepdf.PdfError as e:
            raise StepError("ENGINE_FAILED", f"{what} could not be converted to PDF/A.") from e
        if problems:
            sys.stderr.write(f"pdf.to-pdfa: output is not PDF/A-{level}: {'; '.join(problems)}\n")
            raise StepError(
                "ENGINE_FAILED", f"{what} could not be converted to valid PDF/A-{level}."
            )
        _warn_losses(ctx, what, level, before, after)
        shutil.move(raw, out)  # Ghostscript's bytes as they are: a rewrite could break PDF/A
    return [out]
