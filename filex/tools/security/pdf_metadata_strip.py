"""``pdf.metadata-strip``: remove hidden information from a PDF (spec 6.7, review M8, task 30).

Removed:
- the Info dictionary (title, author, subject, keywords, creator, producer, dates, custom keys);
- every XMP packet: the document's and those on pages, images, fonts and forms;
- the document ID (a new one is derived from the output's content, so it links to nothing);
- embedded files: the attachment list, file-attachment annotations and associated files (/AF);
- JavaScript: the document's scripts, JavaScript actions on links, bookmarks and fields, and
  every additional-actions (/AA) trigger, which disables form scripts such as number formats;
- the document-level open action;
- page piece info and last-modified dates (private data some editors store), page thumbnails;
- the author, dates and unique names (/NM) of comments; with "remove comments", the comments
  themselves (all annotations except links and form fields);
- article thread info (title, author), web-capture source data (/SpiderInfo) and the
  document's permission dictionary (/Perms).

Signed input is refused (SIGNED_PDF): a signature holds the signer's name, signing time and
certificate, and the rewrite would break it anyway. Remove the signature first.

Not removed: what the pages show (text, images, the names in them), metadata inside image
bytes (such as EXIF in a JPEG), font names, bookmarks, form field names and values, layer names,
page labels, XFA form data, and anything inside text hidden under images. A file whose
contents must go needs redaction, not this tool.

The output is a full rewrite (no incremental update keeps the old data), and is re-opened and
checked: if any removed item is still there, the job fails with STRIP_INCOMPLETE instead of
returning a file that only looks clean.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import Field

from filex.registry import Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, open_pdf, save

KEPT_ANNOTS = ("/Link", "/Widget")  # not comments: they are part of how the document works
DROP_EVERYWHERE = ("/Metadata", "/PieceInfo", "/LastModified", "/AA", "/Thumb", "/AF")
AUTHORSHIP = ("/T", "/M", "/CreationDate")  # on comments; /T on a form field is its name
SIG_TYPES = ("/Sig", "/DocTimeStamp")
ROOT_DROP = ("/OpenAction", "/Perms", "/SpiderInfo")


class Options(ToolOptions):
    remove_comments: bool = Field(
        False, title="Also remove comments and other annotations (links and form fields stay)"
    )


@tool(
    id="pdf.metadata-strip",
    title="Remove PDF metadata",
    summary=(
        "Strip the author, dates, XMP, attachments and scripts hidden in a PDF. "
        "What the pages show stays: that needs redaction."
    ),
    category="security",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[Example(inputs=["pdf/metadata.pdf"], options={"removeComments": True})],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES", "SIGNED_PDF", "STRIP_INCOMPLETE"],
    keywords=["scrub", "privacy", "anonymize", "clean", "metadata", "xmp", "hidden"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import pikepdf

    what = label(ctx)
    target = ctx.out_dir / "stripped.pdf"
    with open_pdf(ctx, src, what) as pdf:
        if _signed(pdf):
            raise StepError(
                "SIGNED_PDF",
                f"{what} is digitally signed; the signature holds the signer's name and "
                "certificate, and cleaning would break it. Use a copy without the signature.",
            )
        old_id = _first_id(pdf)
        try:
            _strip(pdf, opts)
        except pikepdf.PdfError as e:
            raise StepError("INVALID_PDF", f"{what} is damaged and cannot be cleaned.") from e
        ctx.progress(0.5, "Saving")
        save(pdf, target, what, deterministic_id=True)
    ctx.progress(0.8, "Checking the result")
    with pikepdf.open(target) as out:
        left = _findings(out, opts)
        if old_id is not None and _first_id(out) == old_id:
            left.append("document ID")
    if left:
        target.unlink(missing_ok=True)
        raise StepError(
            "STRIP_INCOMPLETE",
            f"{what} could not be fully cleaned; still present: {', '.join(sorted(set(left)))}.",
        )
    return [target]


def _strip(pdf: Any, opts: Options) -> None:
    import pikepdf

    for key in ("/Info", "/ID"):
        _drop(pdf.trailer, key)
    for key in ROOT_DROP:
        _drop(pdf.Root, key)
    for thread in _threads(pdf):
        _drop(thread, "/I")
    names = pdf.Root.get("/Names")
    if names is not None:
        _drop(names, "/JavaScript")
        _drop(names, "/EmbeddedFiles")
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        keep = []
        for a in annots:
            if not isinstance(a, pikepdf.Dictionary):
                continue
            kind = a.get("/Subtype")
            if kind == "/FileAttachment" or (opts.remove_comments and kind not in KEPT_ANNOTS):
                for key in list(a.keys()):  # emptied: the structure tree may still point here
                    del a[key]
                continue
            _drop(a, "/NM")
            if kind != "/Widget":
                for key in AUTHORSHIP:
                    _drop(a, key)
            keep.append(a)
        page.obj.Annots = pikepdf.Array(keep)
    for d in _dicts(pdf):
        for key in DROP_EVERYWHERE:
            _drop(d, key)
        if d.get("/Type") == "/Thread":  # also reached from page beads
            _drop(d, "/I")
        for key in ("/A", "/OpenAction", "/Next"):
            if _is_js(d.get(key)):
                del d[key]


def _findings(pdf: Any, opts: Options) -> list[str]:
    """What a strip should have removed and is still in ``pdf``, by plain name."""
    found = []
    if "/Info" in pdf.trailer:
        found.append("Info dictionary")
    if "/OpenAction" in pdf.Root:
        found.append("open action")
    if "/Perms" in pdf.Root:
        found.append("signature permissions")
    if "/SpiderInfo" in pdf.Root:
        found.append("web capture info")
    if any("/I" in t for t in _threads(pdf)):
        found.append("article thread info")
    names = pdf.Root.get("/Names")
    if names is not None and "/EmbeddedFiles" in names:
        found.append("embedded files")
    if names is not None and "/JavaScript" in names:
        found.append("JavaScript")
    for d in _dicts(pdf):
        if "/Metadata" in d:
            found.append("XMP metadata")
        if "/PieceInfo" in d or "/LastModified" in d:
            found.append("page piece info")
        if "/AA" in d or "/JS" in d or d.get("/S") == "/JavaScript":
            found.append("JavaScript")
        if "/Thumb" in d:
            found.append("thumbnails")
        if _is_sig_value(d):
            found.append("signature value")
        if d.get("/Type") == "/Thread" and "/I" in d:
            found.append("article thread info")
        if "/AF" in d or "/EF" in d or d.get("/Type") == "/EmbeddedFile":
            found.append("embedded files")
        if d.get("/Type") == "/Annot" or ("/Rect" in d and "/Subtype" in d):
            kind = d.get("/Subtype")
            if "/NM" in d:
                found.append("annotation names")
            if kind == "/FileAttachment":
                found.append("embedded files")
            elif kind != "/Widget" and any(k in d for k in AUTHORSHIP):
                found.append("comment authors")
            if opts.remove_comments and kind not in KEPT_ANNOTS:
                found.append("comments")
    return found


def _signed(pdf: Any) -> bool:
    """Signed: the AcroForm says so (as in pdf.to-pdfa and OCR), or any signature value is
    reachable, flag or not."""
    form = pdf.Root.get("/AcroForm")
    if form is not None and int(form.get("/SigFlags", 0)) & 1:
        return True
    return any(_is_sig_value(d) for d in _dicts(pdf))


def _is_sig_value(d: Any) -> bool:
    """A signature dictionary (a /Sig field's /V): signer name, time, certificate."""
    return d.get("/Type") in SIG_TYPES or "/ByteRange" in d


def _threads(pdf: Any) -> list[Any]:
    import pikepdf

    threads = pdf.Root.get("/Threads")
    if not isinstance(threads, pikepdf.Array):
        return []
    return [t for t in threads if isinstance(t, pikepdf.Dictionary)]


def _dicts(pdf: Any) -> Iterator[Any]:
    """Every dictionary and stream reachable from the trailer, each indirect object once. A
    caller may delete keys of the yielded object; what they pointed to is then not visited."""
    import pikepdf

    seen: set[tuple[int, int]] = set()
    stack = [pdf.trailer]
    while stack:
        obj = stack.pop()
        if not isinstance(obj, pikepdf.Object):
            continue  # numbers, booleans and None come back as Python values
        if obj.is_indirect:
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            yield obj
            stack.extend(obj.values())
        elif isinstance(obj, pikepdf.Array):
            stack.extend(obj)


def _is_js(action: Any) -> bool:
    import pikepdf

    if isinstance(action, pikepdf.Array):
        return any(_is_js(a) for a in action)
    return isinstance(action, pikepdf.Dictionary) and (
        action.get("/S") == "/JavaScript" or _is_js(action.get("/Next"))
    )


def _first_id(pdf: Any) -> bytes | None:
    ids = pdf.trailer.get("/ID")
    return bytes(ids[0]) if ids is not None and len(ids) else None


def _drop(d: Any, key: str) -> None:
    if key in d:
        del d[key]
