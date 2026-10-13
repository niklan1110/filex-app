"""``pdf.metadata-edit``: set or clear title, author, subject, keywords, creator, producer and
dates (spec 6.6, plan task 30).

A PDF keeps these twice, in the Info dictionary and in XMP; readers differ in which one they
show, so every change is written to both (pikepdf's XMP editing mirrors each field into Info).
Fields left out are kept as they are ("update"); "replace" removes everything that is not filled
in, custom Info keys and the whole old XMP packet included. An empty string clears a field in
either mode. Dates take ISO 8601 (``2024-05-01`` or ``2024-05-01T14:30``, with an optional UTC
offset); a date without an offset is stored without one. pikepdf is told not to stamp itself as
the producer, so the producer is exactly what was given or kept. Full pikepdf rewrite.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from filex.registry import Example, Files, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, open_pdf, save

# option name → XMP property (pikepdf mirrors it into the Info key in the comment)
XMP = {
    "title": "dc:title",  # /Title
    "author": "dc:creator",  # /Author
    "subject": "dc:description",  # /Subject
    "keywords": "pdf:Keywords",  # /Keywords
    "creator": "xmp:CreatorTool",  # /Creator
    "producer": "pdf:Producer",  # /Producer
    "created": "xmp:CreateDate",  # /CreationDate
    "modified": "xmp:ModifyDate",  # /ModDate
}
INFO = {
    "dc:title": "/Title",
    "dc:creator": "/Author",
    "dc:description": "/Subject",
    "pdf:Keywords": "/Keywords",
    "xmp:CreatorTool": "/Creator",
    "pdf:Producer": "/Producer",
    "xmp:CreateDate": "/CreationDate",
    "xmp:ModifyDate": "/ModDate",
}


def _iso(text: str | None) -> str | None:
    if not text:
        return text
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError("Use a date like 2024-05-01 or 2024-05-01T14:30.") from None


Text = Annotated[str | None, Field(max_length=1000)]
Date = Annotated[str | None, AfterValidator(_iso)]


class Options(ToolOptions):
    mode: Literal["update", "replace"] = Field(
        "update",
        title="Fields left blank",
        json_schema_extra={"x-enum-titles": ["Keep them", "Remove them"]},
    )
    title: Text = Field(None, title="Title")
    author: Text = Field(None, title="Author")
    subject: Text = Field(None, title="Subject")
    keywords: Text = Field(None, title="Keywords")
    creator: Text = Field(None, title="Creator (the app that made the original)")
    producer: Text = Field(None, title="Producer (the app that made the PDF)")
    created: Date = Field(None, title="Created (YYYY-MM-DD or YYYY-MM-DDTHH:MM)", max_length=40)
    modified: Date = Field(None, title="Modified (YYYY-MM-DD or YYYY-MM-DDTHH:MM)", max_length=40)


@tool(
    id="pdf.metadata-edit",
    title="Edit PDF properties",
    summary="Change or remove the title, author, subject, keywords and dates a PDF carries.",
    category="edit",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),
    options=Options,
    engines=["pikepdf"],
    examples=[
        Example(
            inputs=["pdf/metadata.pdf"],
            options={"title": "Annual report", "author": "", "keywords": "report, 2024"},
        )
    ],
    warnings=["INPUT_REPAIRED", "RESTRICTIONS_REMOVED"],
    errors=["INVALID_PDF", "ENCRYPTED_PDF", "TOO_MANY_PAGES"],
    keywords=["metadata", "properties", "title", "author", "keywords", "xmp", "info"],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import pikepdf

    what = label(ctx)
    with open_pdf(ctx, src, what) as pdf:
        if opts.mode == "replace":
            _drop(pdf.trailer, "/Info")
            _drop(pdf.Root, "/Metadata")
        try:
            with pdf.open_metadata(set_pikepdf_as_editor=False) as xmp:
                # pikepdf rewrites Info from XMP on exit: first copy over Info fields the XMP
                # lacks, so a file without XMP does not lose them.
                info = pdf.docinfo
                xmp.load_from_docinfo(
                    {k: info[k] for xp, k in INFO.items() if k in info and xp not in xmp}
                )
                for name, prop in XMP.items():
                    value = getattr(opts, name)
                    if value is None:
                        continue
                    if value == "":
                        if prop in xmp:
                            del xmp[prop]
                        _drop(info, INFO[prop])
                    else:
                        xmp[prop] = [value] if prop == "dc:creator" else value
        except (pikepdf.PdfError, ValueError) as e:
            raise StepError("INVALID_PDF", f"{what} has metadata that cannot be edited.") from e
        ctx.progress(0.5, "Saving")
        return [save(pdf, ctx.out_dir / "edited.pdf", what)]


def _drop(d, key: str) -> None:
    if key in d:
        del d[key]
