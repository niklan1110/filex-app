"""``markup.convert``: Markdown, HTML, DOCX, EPUB and FB2 to each other and to plain text with
Pandoc (spec 6.4, plan task 51).

Every run is ``pandoc --sandbox`` (ADR-0007 Appendix A, review H6): Pandoc reads nothing but
its input and its own data files and fetches no URL, so images or links pointing at ``file:``
paths, other files or the network are left out of the output. User options never reach
``--filter``, ``--lua-filter``, ``--template`` or ``--resource-path`` (there are none). The
heap is capped with ``+RTS -M``, below the step's ``RLIMIT_DATA`` (GHC reserves a large address
space up front, so ``RLIMIT_AS`` would break it).

Pandoc reads UTF-8 only: Markdown, HTML and FB2 inputs are re-encoded first (FB2 from its XML
declaration, the others from a BOM, else UTF-8, else Windows-1251 or -1252 judged from the
bytes). DOCX and EPUB pass the archive pre-check (review H3) and EPUB the DRM check; FB2 is
read up to its root element with ``defusedxml``. HTML output is standalone with images
embedded as ``data:`` URIs. TXT as a target drops all formatting, so those edges are lossy;
it keeps the title block (``--standalone``: a DOCX Title paragraph is metadata to Pandoc) and
shows an image as its alt text, or nothing (Pandoc would write ``[alt]`` or ``[]``). For that,
the input goes to Pandoc's JSON AST first and Python replaces the images (no Lua filter).
"""

from __future__ import annotations

import codecs
import json
import re
import shutil
import tempfile
from pathlib import Path

from filex.formats import FORMATS
from filex.registry import Edge, Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label
from filex.tools.ebook import _books, _engine

SOURCES = ("md", "html", "docx", "epub", "fb2")
TARGETS = ("md", "html", "docx", "epub", "fb2", "txt")
READERS = {"md": "markdown", "html": "html", "docx": "docx", "epub": "epub", "fb2": "fb2"}
READERS["json"] = "json"  # the AST between the two TXT passes, never a user format
WRITERS = {**READERS, "txt": "plain"}
TIMEOUT_S = 180
ENGINE_TIMEOUT_S = 150
MEMORY_MB = 1024
HEAP_MB = MEMORY_MB - 128  # Pandoc's own heap cap, under RLIMIT_DATA
TEXT_SOURCES = ("md", "html", "fb2")

EDGES = [Edge(a, b, lossy=b == "txt") for a in SOURCES for b in TARGETS if a != b]

_XML_DECL = re.compile(rb"^<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")


@tool(
    id="markup.convert",
    title="Convert Markdown, HTML and documents",
    summary="Convert between Markdown, HTML, Word, EPUB and FB2, or extract plain text.",
    category="ebook",
    group="ebook",
    converts=EDGES,
    files=Files(min=1, max=20, each=True),
    engines=["pandoc"],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=MEMORY_MB, rlimit_data=True),
    examples=[Example(inputs=["ebook/book.fb2"], target="md")],
    errors=["DRM_PROTECTED"],
    keywords=["markdown", "md", "html", "docx", "epub", "fb2", "pandoc", "text", "web"],
)
def run(ctx: StepContext, src: Path, opts: ToolOptions) -> list[Path]:
    what = label(ctx)
    fmt, dst = _engine.format_of(src, SOURCES), ctx.target
    if dst is None:
        raise StepError("INVALID_OPTIONS", "Choose the format to convert to.")
    if dst not in TARGETS or dst == fmt:
        raise StepError(
            "INVALID_OPTIONS",
            f"{what} ({_engine.title(fmt)}) cannot be converted to {_engine.title(dst)} here.",
        )
    ctx.progress(0.0, "Checking the file")
    _books.check_input(ctx, src, fmt, what)
    work = Path(tempfile.mkdtemp(prefix="pandoc-"))  # under TMPDIR, the step's scratch dir
    try:
        book = src.resolve()
        if fmt in TEXT_SOURCES:
            book = work / f"in{src.suffix}"
            book.write_bytes(utf8(src.read_bytes(), fmt, what))
        ext = FORMATS[dst].extensions[0]
        out = work / f"out{ext}"
        ctx.progress(0.1, "Converting with Pandoc")
        if dst == "txt":
            ast = work / "ast.json"
            _pandoc(command(book, fmt, "json", ast), ast, work, what)
            try:
                tree = drop_images(json.loads(ast.read_bytes()))
            except (ValueError, RecursionError) as e:  # a pathologically deep document
                raise failure(0, "", what) from e
            ast.write_text(json.dumps(tree), encoding="utf-8")
            book, fmt = ast, "json"
        _pandoc(command(book, fmt, dst, out), out, work, what)
        return [Path(shutil.move(out, ctx.out_dir / f"converted{ext}"))]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _pandoc(args: list[str], out: Path, work: Path, what: str) -> None:
    code, log = _engine.run(args, cwd=work, timeout_s=ENGINE_TIMEOUT_S, what=what, engine="Pandoc")
    if code or not out.is_file() or out.stat().st_size == 0:
        raise failure(code, log, what)


def command(src: Path, fmt: str, dst: str, out: Path) -> list[str]:
    args = ["pandoc", "+RTS", f"-M{HEAP_MB}m", "-RTS", "--sandbox"]
    args += ["-f", READERS[fmt], "-t", WRITERS[dst], "-o", str(out)]
    if dst == "html":
        args += ["--standalone", "--embed-resources"]
    elif dst == "txt":
        args += ["--standalone"]  # the title block: title, authors, date
    return [*args, str(src)]  # our scratch path: never starts with "-"


def drop_images(node: object) -> object:
    """A Pandoc JSON AST with every image replaced by its alt text; paragraphs left empty
    are removed."""
    if isinstance(node, dict):
        return {k: drop_images(v) for k, v in node.items()}
    if not isinstance(node, list):
        return node
    out: list[object] = []
    for n in node:
        if isinstance(n, dict) and n.get("t") == "Image":
            out += drop_images(n["c"][1])  # c = [attr, alt inlines, target]
            continue
        n = drop_images(n)
        if not (isinstance(n, dict) and n.get("t") in ("Para", "Plain") and not n["c"]):
            out.append(n)
    return out


def utf8(data: bytes, fmt: str, what: str) -> bytes:
    """``data`` re-encoded as UTF-8 without a BOM; an FB2's XML declaration then says UTF-8."""
    if fmt == "fb2":
        return _utf8_xml(data, what)
    return _decode(data, _charset(data), what).encode("utf-8")


def _charset(data: bytes) -> str:
    for bom, codec in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
                       (codecs.BOM_UTF16_BE, "utf-16")):  # fmt: skip
        if data.startswith(bom):
            return codec
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    high = sum(b >= 0xC0 for b in data[: 1 << 20])
    ascii_letters = sum(65 <= b <= 90 or 97 <= b <= 122 for b in data[: 1 << 20])
    return "cp1251" if high > ascii_letters else "cp1252"


def _decode(data: bytes, codec: str, what: str) -> str:
    try:
        return data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        raise StepError("INVALID_INPUT", f"{what} is not readable text.") from None


def _utf8_xml(data: bytes, what: str) -> bytes:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = _decode(data, "utf-16", what)
    elif data.startswith(codecs.BOM_UTF8):
        text = _decode(data, "utf-8-sig", what)
    else:
        m = _XML_DECL.match(data)
        text = _decode(data, m[1].decode("ascii") if m else "utf-8", what)
    decl = re.match(r"<\?xml[^>]*\?>", text)
    if decl:
        text = '<?xml version="1.0" encoding="UTF-8"?>' + text[decl.end() :]
    return text.encode("utf-8")


def failure(code: int, log: str, what: str) -> StepError:
    if _engine.memory_failure(code, log):
        return StepError(
            "ENGINE_MEMORY", f"{what} needs more memory to convert than this server allows."
        )
    return StepError(
        "ENGINE_FAILED", f"{what} could not be converted. It may be damaged or unsupported."
    )
