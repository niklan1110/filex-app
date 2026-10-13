"""Helpers shared by the PDF tools: opening with error mapping (pikepdf, pypdfium2), page
ranges, page copies.

Engine imports stay inside the functions (registry rule 1), because tool modules import this
module at module level.

Page ranges (spec 6.1, the UI's ``page-range`` field): comma-separated ``N``, ``N-M``, ``N-``
(to the last page) and ``-M`` (from page 1); spaces are ignored; pages count from 1. A backwards
range such as ``5-3`` is rejected rather than guessed at. A range that runs past the last page
stops at the last page; one that starts past it is an error naming it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any

from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError

from filex.registry import StepContext, StepError

PageRange = tuple[str, int, int | None]  # (token as written, first page, last page or None)

_TOKEN = re.compile(r"(\d+)(?:(-)(\d*))?|-(\d+)")
_HINT = "Use page numbers and ranges, like 1-3, 5, 8-."


def parse_ranges(text: str) -> list[PageRange]:
    """The ranges in ``text``, in the order written; ``[]`` for blank text. Raises
    ``PydanticCustomError`` naming the bad entry, so a model validator turns it into a 422."""
    if not text.strip():
        return []
    ranges = []
    for raw in text.split(","):
        token = "".join(raw.split())
        m = _TOKEN.fullmatch(token)
        if not token:
            raise PydanticCustomError("page_range", f"There is an empty entry. {_HINT}")
        if not m:
            raise PydanticCustomError(
                "page_range", '"{token}" is not a page or a range. ' + _HINT, {"token": token}
            )
        if m[4]:
            first, last = 1, int(m[4])
        else:
            first = int(m[1])
            last = None if m[2] and not m[3] else int(m[3] or m[1])
        if first == 0 or last == 0:
            raise PydanticCustomError(
                "page_range", '"{token}": pages are numbered from 1.', {"token": token}
            )
        if last is not None and last < first:
            raise PydanticCustomError(
                "page_range",
                '"{token}" goes backwards. Write it as {last}-{first}.',
                {"token": token, "first": first, "last": last},
            )
        ranges.append((token, first, last))
    return ranges


def _check(text: str) -> str:
    parse_ranges(text)
    return text


def _check_required(text: str) -> str:
    if not parse_ranges(text):
        raise PydanticCustomError("page_range", f"Enter at least one page. {_HINT}")
    return text


_SCHEMA = {"format": "page-range"}  # the UI renders a page-range field (OptionsForm.tsx)

# Options fields. ``Pages`` may be blank (the tool decides what blank means); ``RequiredPages``
# needs at least one page.
Pages = Annotated[str, AfterValidator(_check), Field(max_length=200, json_schema_extra=_SCHEMA)]
RequiredPages = Annotated[
    str, AfterValidator(_check_required), Field(max_length=200, json_schema_extra=_SCHEMA)
]


def select(ranges: list[PageRange], count: int, what: str) -> list[range]:
    """0-based page indices per range, for a file of ``count`` pages."""
    out = []
    for token, first, last in ranges:
        if first > count:
            raise StepError(
                "INVALID_OPTIONS",
                f'{what} has {_pages(count)}, so "{token}" selects no page.',
            )
        out.append(range(first - 1, min(last or count, count)))
    return out


def label(ctx: StepContext) -> str:
    """How messages name the input of a one-file run: its upload name in quotes, else
    "The file"."""
    name = ctx.input_names[0] if ctx.input_names else ""
    return f'"{name}"' if name else "The file"


def open_pdf(ctx: StepContext, path: Path, what: str, *, password: str = "") -> Any:
    """Open an input with pikepdf: password-protected → ENCRYPTED_PDF (WRONG_PASSWORD when a
    ``password`` was given and does not open it), unreadable or empty → INVALID_PDF, more than
    ``ctx.max_pages`` → TOO_MANY_PAGES. Warns INPUT_REPAIRED and, when opened without a password,
    RESTRICTIONS_REMOVED, so a tool using it declares those warnings. The password is passed
    in-process and never appears in a message (review H5)."""
    import pikepdf

    try:
        pdf = pikepdf.open(path, password=password)
    except pikepdf.PasswordError as e:
        if password:
            raise StepError("WRONG_PASSWORD", f"The password does not open {what}.") from None
        raise StepError(
            "ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first."
        ) from e
    except pikepdf.PdfError as e:
        raise StepError("INVALID_PDF", f"{what} is not a readable PDF.") from e
    try:
        count = len(pdf.pages)
    except pikepdf.PdfError as e:
        pdf.close()
        raise StepError("INVALID_PDF", f"{what} is damaged and cannot be read.") from e
    if not count:
        pdf.close()
        raise StepError("INVALID_PDF", f"{what} has no pages.")
    if count > ctx.max_pages:
        pdf.close()
        raise StepError(
            "TOO_MANY_PAGES", f"{what} has {count} pages; the limit is {ctx.max_pages}."
        )
    if pdf.get_warnings():
        ctx.warn("INPUT_REPAIRED", f"{what} was damaged and has been repaired.")
    if pdf.is_encrypted and not password:  # opened without a password: owner restrictions
        ctx.warn("RESTRICTIONS_REMOVED", f"{what}: editing restrictions were removed.")
    return pdf


def open_pdfium(ctx: StepContext, path: Path, what: str) -> Any:
    """Open an input with pypdfium2 (rendering, text), with the same error mapping as
    :func:`open_pdf`: ENCRYPTED_PDF, INVALID_PDF, TOO_MANY_PAGES. Close it when done."""
    import pypdfium2
    import pypdfium2.raw as pdfium_c

    try:
        pdf = pypdfium2.PdfDocument(path)
    except pypdfium2.PdfiumError as e:
        if e.err_code == pdfium_c.FPDF_ERR_PASSWORD:
            raise StepError(
                "ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first."
            ) from e
        raise StepError("INVALID_PDF", f"{what} is not a readable PDF.") from e
    count = len(pdf)
    if not count:
        pdf.close()
        raise StepError("INVALID_PDF", f"{what} has no pages.")
    if count > ctx.max_pages:
        pdf.close()
        raise StepError(
            "TOO_MANY_PAGES", f"{what} has {count} pages; the limit is {ctx.max_pages}."
        )
    return pdf


def open_mupdf(ctx: StepContext, path: Path, what: str) -> Any:
    """Open an input with PyMuPDF (layout text, images, tables), with the same error mapping as
    :func:`open_pdf`: ENCRYPTED_PDF, INVALID_PDF, TOO_MANY_PAGES. Close it when done. MuPDF's
    own messages are kept off stderr; a file it had to repair warns INPUT_REPAIRED."""
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    # find_tables() prints an advert for pymupdf_layout to stdout (PyMuPDF 1.26+).
    getattr(pymupdf, "no_recommend_layout", lambda: None)()
    try:
        pdf = pymupdf.open(path, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as e:
        raise StepError("INVALID_PDF", f"{what} is not a readable PDF.") from e
    if pdf.needs_pass:
        pdf.close()
        raise StepError("ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first.")
    count = len(pdf)
    if not count:
        pdf.close()
        raise StepError("INVALID_PDF", f"{what} has no pages.")
    if count > ctx.max_pages:
        pdf.close()
        raise StepError(
            "TOO_MANY_PAGES", f"{what} has {count} pages; the limit is {ctx.max_pages}."
        )
    if pdf.is_repaired:
        ctx.warn("INPUT_REPAIRED", f"{what} was damaged and has been repaired.")
    return pdf


# Stream keys that make a reader load data from another file (ISO 32000-2, 7.3.8.2 external
# streams, 8.10.4 reference XObjects, 14.11.7 OPI).
_EXTERNAL_STREAM_KEYS = ("/F", "/FFilter", "/FDecodeParms", "/OPI")


def strip_external_refs(src: Path, dst: Path, what: str) -> Path:
    """``src``, or when it names outside files a sanitised copy written to ``dst``, for engines
    that might resolve those names (Calibre's PDF input reads through poppler; task 64).

    Removed from every stream: ``/F`` (an external stream: the data comes from that file or URL,
    a ``/FS /URL`` file spec included), ``/FFilter``, ``/FDecodeParms``, ``/OPI`` (an external
    high-resolution image), and ``/Ref`` from form XObjects (a reference XObject imports a page
    of another PDF). Sanitised rather than refused: a reader without external-stream support
    draws the stream's own data and the form's own content (its proxy), so the page looks the
    same as before for such readers, and FileX never draws the outside file. Password-protected
    → ENCRYPTED_PDF, unreadable → INVALID_PDF."""
    import pikepdf

    try:
        with pikepdf.open(src) as pdf:
            changed = False
            for obj in pdf.objects:
                if not isinstance(obj, pikepdf.Stream):
                    continue
                keys = [k for k in _EXTERNAL_STREAM_KEYS if k in obj]
                if obj.get("/Subtype") == "/Form" and "/Ref" in obj:
                    keys.append("/Ref")
                for k in keys:
                    del obj[k]
                changed = changed or bool(keys)
            return save(pdf, dst, what) if changed else src
    except pikepdf.PasswordError as e:
        raise StepError(
            "ENCRYPTED_PDF", f"{what} is password-protected. Remove the password first."
        ) from e
    except pikepdf.PdfError as e:
        raise StepError("INVALID_PDF", f"{what} is not a readable PDF.") from e


def append_pages(out: Any, pdf: Any, indices: Iterable[int]) -> dict[Any, int]:
    """Copy ``pdf``'s pages at ``indices`` to the end of ``out``, keeping form fields working
    (qpdf renames clashing field names with a ``+1`` suffix). Returns source page → new index
    (the first copy, if a page is copied twice), for :func:`copy_outline`."""
    form = pdf.acroform
    new: dict[Any, int] = {}
    for i in indices:
        page = pdf.pages[i]
        out.pages.append(page)
        new.setdefault(page.objgen, len(out.pages) - 1)
        if form.exists:  # register the copied widgets as fields
            out.acroform.fix_copied_annotations(out.pages[-1], page, form)
    return new


def copy_outline(pdf: Any, new: dict[Any, int]) -> list[Any]:
    """``pdf``'s bookmarks, pointing at the new page positions in ``new``. A bookmark to a page
    that was not copied is dropped, unless it has bookmarks under it that were kept; then it
    stays as a plain title. Named or external destinations are kept as plain titles."""
    import pikepdf

    source = {p.objgen for p in pdf.pages}

    def copy(items: Any) -> list[Any]:
        copied = []
        for item in items:
            children = copy(item.children)
            page = _target(pikepdf, item, source)
            if page is not None and page not in new and not children:
                continue
            node = pikepdf.OutlineItem(str(item.title), new.get(page))
            node.children.extend(children)
            copied.append(node)
        return copied

    with pdf.open_outline() as outline:
        return copy(outline.root)


def top_level_bookmarks(pdf: Any) -> list[int]:
    """Sorted 0-based indices of the pages the top-level bookmarks point at."""
    import pikepdf

    pages = {p.objgen: i for i, p in enumerate(pdf.pages)}
    with pdf.open_outline() as outline:
        found = {_target(pikepdf, item, pages) for item in outline.root}
    return sorted(pages[k] for k in found if k is not None)


def _target(pikepdf: Any, item: Any, source: Any) -> Any:
    """The source page (objgen) an outline item points at, or None."""
    dest = item.destination
    if dest is None and item.action is not None and item.action.get("/S") == "/GoTo":
        dest = item.action.get("/D")
    if (
        isinstance(dest, pikepdf.Array)
        and len(dest)
        and isinstance(dest[0], pikepdf.Dictionary)
        and dest[0].objgen in source
    ):
        return dest[0].objgen
    return None


def write_pages(
    pdf: Any, indices: Iterable[int], target: Path, what: str, turns: Sequence[int] = ()
) -> Path:
    """A new PDF with ``pdf``'s pages at ``indices``, in that order; an index may repeat (each
    copy is its own page). ``turns`` (optional, aligned with ``indices``) adds a clockwise turn
    in degrees to each copy's ``/Rotate``. Form fields and the bookmarks to copied pages come
    along (a bookmark goes to the first copy); the Info dictionary, XMP metadata and page labels
    do not (same policy as ``pdf.merge``)."""
    import pikepdf

    with pikepdf.new() as out:
        try:
            outline = copy_outline(pdf, append_pages(out, pdf, indices))
            for page, angle in zip(out.pages, turns, strict=False):
                if angle:
                    page.rotate(angle, relative=True)
        except pikepdf.PdfError as e:
            raise StepError("INVALID_PDF", f"{what} is damaged and cannot be read.") from e
        if outline:
            with out.open_outline() as root:
                root.root.extend(outline)
        return save(out, target, what)


def save(pdf: Any, target: Path, what: str, **options: Any) -> Path:
    """Full rewrite to ``target`` (review M8), without encryption unless ``options`` (passed to
    ``pikepdf.Pdf.save``) give one. ``what`` names the input in the error message."""
    import pikepdf

    try:
        pdf.save(target, **({"encryption": False} | options))
    except pikepdf.PdfError as e:
        target.unlink(missing_ok=True)
        raise StepError("INVALID_PDF", f"{what} is damaged and could not be saved.") from e
    return target


def _pages(n: int) -> str:
    return f"{n} page" if n == 1 else f"{n} pages"
