"""Dummy tools for the job API tests (task 6). Test fixtures, never shipped."""

import shutil
import sys
import zipfile
from typing import Literal

from pydantic import Field, SecretStr

from filex.registry import Aux, Edge, Files, Output, Secret, StepError, ToolOptions, tool


class PdfOptions(ToolOptions):
    page_size: Literal["A4", "LETTER"] = Field("A4", title="Page size")
    password: SecretStr | None = Secret(title="Password", optional=True)


@tool(
    id="api.pdf",
    title="Copy PDF",
    summary="Copies the first PDF.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(
        min=1,
        max=3,
        aux={"certificate": Aux(formats=["p12"], title="Certificate", required=False)},
    ),
    options=PdfOptions,
    errors=["WRONG_PASSWORD"],
    keywords=["copy", "duplicate"],
)
def copy_pdf(ctx, src, opts):
    if opts.password is not None and opts.password.get_secret_value() != "correct horse":
        raise StepError("WRONG_PASSWORD", "The password is wrong.")
    out = ctx.out_dir / "out.pdf"
    shutil.copyfile(src[0], out)
    return [out]


@tool(
    id="api.each",
    title="Copy each",
    summary="Copies every PDF.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    files=Files(min=1, max=3, each=True),
)
def copy_each(ctx, src, opts):
    out = ctx.out_dir / "out.pdf"
    shutil.copyfile(src, out)
    return [out]


@tool(
    id="api.zip",
    title="Zip in",
    summary="Takes a zip.",
    category="convert",
    group="core",
    inputs=["zip"],
    outputs=["pdf"],
)
def zip_in(ctx, src, opts):
    raise StepError("NOT_RUN", "Never run in tests.")


@tool(
    id="api.office",
    title="Office thing",
    summary="Needs the office worker.",
    category="convert",
    group="office",
    inputs=["pdf"],
    outputs=["pdf"],
)
def office(ctx, src, opts):
    raise StepError("NOT_RUN", "Never run in tests.")


class NoisyOptions(ToolOptions):
    label: str = Field("", title="Label", max_length=100)
    password: SecretStr | None = Secret(title="Password", optional=True)
    fail: bool = Field(False, title="Fail")


@tool(
    id="api.noisy",
    title="Noisy",
    summary="Echoes its options and file names to stderr, like a chatty engine.",
    category="organize",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    options=NoisyOptions,
)
def noisy(ctx, src, opts):
    secret = opts.password.get_secret_value() if opts.password else ""
    print(f"engine says: {opts.label} {secret} {ctx.input_names}", file=sys.stderr)
    print("x" * 5000, file=sys.stderr)  # pushes the start out of the 2 KB tail
    print(f"engine says again: {opts.label} {secret}", file=sys.stderr)
    if opts.fail:
        raise RuntimeError(f"engine broke on {opts.label} {secret} {ctx.input_names}")
    out = ctx.out_dir / "out.pdf"
    shutil.copyfile(src[0], out)
    return [Output(out, f"{opts.label}-result.pdf")]


def office_file(path, fmt):
    """The smallest file ``filex.formats.sniff`` takes for ``fmt`` (docx, odt or rtf)."""
    if fmt == "rtf":
        path.write_bytes(rb"{\rtf1 converted}")
        return path
    with zipfile.ZipFile(path, "w") as zf:
        if fmt == "odt":
            zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            zf.writestr("content.xml", "<office:document-content/>")
        else:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/document.xml", "<w:document/>")
    return path


@tool(
    id="api.convert",
    title="Convert documents",
    summary="Stands in for office.convert: writes an empty file of the job's target format.",
    category="convert",
    group="core",
    converts=[Edge("docx", "odt"), Edge("docx", "rtf", lossy=True), Edge("odt", "docx")],
    files=Files(min=1, max=3, each=True),
)
def convert(ctx, src, opts):
    if ctx.target is None:
        raise StepError("INVALID_OPTIONS", "No target.")
    return [office_file(ctx.out_dir / f"converted.{ctx.target}", ctx.target)]
