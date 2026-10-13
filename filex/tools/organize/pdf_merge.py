"""``pdf.merge``: join PDFs into one, in upload order (spec section 6, plan task 7).

Pages keep their size and rotation; form fields keep working (clashing names get a ``+1``
suffix from qpdf); each file's own bookmarks are kept. Input metadata (Info, XMP) is not
carried over. Inputs over FILEX_MAX_PAGES are refused, and so is a merge whose pages add up to
more. The output is a full pikepdf rewrite (review M8 / ADR-0007 appendix).
Bookmarks and messages name each file by its upload name, or "File N" when it has none. The
download is named by the API after the first file (``filex.api.names``), so the name written
here is internal only.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

from pydantic import Field

from filex.registry import Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import append_pages, copy_outline, open_pdf


class Options(ToolOptions):
    bookmarks: bool = Field(False, title="Add a bookmark for each file")


@tool(
    id="pdf.merge",
    title="Merge PDFs",
    summary="Combine several PDFs into one, in the order you add them.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=2, max=50),
    options=Options,
    engines=["pikepdf"],
    examples=[
        Example(inputs=["pdf/text_1p.pdf", "pdf/scanned_3p.pdf"], options={"bookmarks": True})
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["combine", "join", "append", "concatenate", "unite", "bind"],
)
def run(ctx: StepContext, src: list[Path], opts: Options) -> list[Path]:
    import pikepdf

    with ExitStack() as stack:
        out = stack.enter_context(pikepdf.new())
        bookmarks: list[Any] = []
        names = list(ctx.input_names)
        for n, path in enumerate(src, 1):
            name = names[n - 1] if n <= len(names) else ""
            what = f'"{name}"' if name else f"File {n}"
            ctx.progress((n - 1) / len(src), f"Adding file {n} of {len(src)}")
            pdf = stack.enter_context(open_pdf(ctx, path, what))
            first = len(out.pages)
            if first + len(pdf.pages) > ctx.max_pages:
                raise StepError(
                    "TOO_MANY_PAGES",
                    f"The merged file would have over {ctx.max_pages} pages, the limit "
                    f"(reached at {what}).",
                )
            try:
                kept = copy_outline(pdf, append_pages(out, pdf, range(len(pdf.pages))))
            except pikepdf.PdfError as e:
                raise StepError("INVALID_PDF", f"{what} is damaged and cannot be read.") from e
            if opts.bookmarks:
                title = name[:-4] if name.lower().endswith(".pdf") else name
                item = pikepdf.OutlineItem(title or f"File {n}", first)
                item.children.extend(kept)
                bookmarks.append(item)
            else:
                bookmarks.extend(kept)
        if bookmarks:
            with out.open_outline() as outline:
                outline.root.extend(bookmarks)
        target = ctx.out_dir / "merged.pdf"
        try:
            out.save(target)  # stream data is read from the inputs here, so keep them open
        except pikepdf.PdfError as e:
            target.unlink(missing_ok=True)
            raise StepError("INVALID_PDF", "A file is damaged and could not be merged.") from e
    return [target]
