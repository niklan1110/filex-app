"""``ebook.convert``: ebooks between EPUB, FB2, MOBI, AZW3, DOCX, PDF, TXT and HTML with Calibre
(spec 6.4, plan task 51).

One ``ebook-convert`` run per file, with the settings of ADR-0007 Appendix A (see
:mod:`filex.tools.ebook._engine`): fresh config, temp and cache dirs, headless Qt, a 300 s
timeout and ``RLIMIT_DATA`` (Qt WebEngine reserves far more address space than it uses, so
``RLIMIT_AS`` would break it). The input reader is chosen by the scratch file's extension, which
is the sniffed format, so only the R1 readers ever run (no CHM, LIT, LRF, PDB, RB, SNB, TCR).

Before Calibre sees a file:

- EPUB and DOCX pass the archive pre-check (review H3), FB2 is read up to its root element with
  ``defusedxml`` (entity declarations refused), a PDF's page count is checked;
- a DOCX that links to outside files is refused (:mod:`filex.tools._ooxml`: Calibre embeds an
  external ``file:`` image), and a PDF reaches Calibre without external streams, reference
  XObjects or OPI links (:func:`filex.tools._pdf.strip_external_refs`: Calibre reads PDFs
  through poppler); task 64;
- DRM is detected and refused with ``DRM_PROTECTED``: an EPUB with ``rights.xml``, ``sinf.xml``
  or an ``encryption.xml`` that encrypts more than fonts; a MOBI/AZW3 whose MOBI header names an
  encryption type. A ``DRMError`` from Calibre maps to the same code;
- HTML is copied alone into an empty folder. Calibre loads only resources inside the HTML's own
  folder (``--allow-local-files-outside-root`` is never passed) and follows no links
  (``--max-levels 0``), so ``file:`` and absolute references load nothing; the worker has no
  network for remote ones.

Edges: every pair of the formats, except HTML as a target (Calibre writes HTML only as a zip of
files; ``markup.convert`` makes single-file HTML). PDF sources are lossy (Calibre cannot recover
reflowable structure from a PDF), and so is TXT as a target (formatting is dropped).

PDF output memory grows with the book's length (Calibre 9.16 in the worker-ebook image: about
1.3 GB for the whole container at 455 pages). The binding limit is the per-process
``RLIMIT_DATA`` (``MEMORY_MB``): a book over it does not fail fast, Calibre's renderer dies and
``ebook-convert`` waits until the 300 s timeout (measured: 271 estimated pages convert under a
900 MB ``RLIMIT_DATA``, 331 hang). So a PDF target is refused up front with ``TOO_MANY_PAGES``
when the book's estimated length (from its text size, :func:`estimated_pages`, which errs long)
is over what the smaller of ``MEMORY_MB`` and the container's memory limit allows
(:func:`pdf_page_cap`): about 230 estimated pages (about 175 Calibre pages of plain text) with
the defaults, less below ``FILEX_MEM_EBOOK=1g``. Other targets have no such cap.
"""

from __future__ import annotations

import math
import re
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from filex.formats import FORMATS
from filex.registry import Edge, Example, Files, Limits, StepContext, StepError, ToolOptions, tool
from filex.tools._pdf import label, strip_external_refs
from filex.tools.ebook import _books, _engine

SOURCES = ("epub", "fb2", "mobi", "azw3", "docx", "pdf", "txt", "html")
TARGETS = ("epub", "fb2", "mobi", "azw3", "docx", "pdf", "txt")
TIMEOUT_S = 360
ENGINE_TIMEOUT_S = 300  # ADR-0007 Appendix A; the rest leaves time to check and move files
MEMORY_MB = 900  # RLIMIT_DATA per process (Appendix A)

# PDF output: memory per page and fixed cost (the runner, the step child and Calibre itself),
# measured in the worker-ebook image; text bytes per PDF page with Calibre's defaults.
PDF_BASE_MB, PDF_MB_PER_PAGE, BYTES_PER_PAGE = 300, 2.6, 2200
CGROUP_MEMORY = (
    Path("/sys/fs/cgroup/memory.max"),
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)

_TEXT_ENTRY = re.compile(r"\.(x?html?|xml)$", re.IGNORECASE)

# Planner cost +1 (ADR-0005) where another engine does better: Word documents (LibreOffice,
# Pandoc) and PDF sources (pypdfium2 text). Calibre still wins where it is the only route.
EDGES = [
    Edge(a, b, lossy=a == "pdf" or b == "txt", cost=int(a in ("pdf", "docx") or b == "docx"))
    for a in SOURCES
    for b in TARGETS
    if a != b
]


@tool(
    id="ebook.convert",
    title="Convert ebooks",
    summary="Convert ebooks between EPUB, MOBI, AZW3, FB2, Word, PDF and plain text.",
    category="ebook",
    group="ebook",
    converts=EDGES,
    files=Files(min=1, max=20, each=True),
    engines=["calibre", "pypdfium2", "pikepdf"],
    limits=Limits(timeout_s=TIMEOUT_S, memory_mb=MEMORY_MB, rlimit_data=True),
    examples=[Example(inputs=["ebook/book.epub"], target="mobi")],
    errors=["DRM_PROTECTED", "TOO_MANY_PAGES", "ENCRYPTED_PDF", "INVALID_PDF"],
    keywords=["epub", "mobi", "azw3", "kindle", "fb2", "fictionbook", "calibre", "ebook", "book"],
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
    ctx.progress(0.0, "Checking the book")
    pages = _books.check_input(ctx, src, fmt, what)
    if dst == "pdf":
        container = memory_limit_mb()
        cap = pdf_page_cap(ctx.max_pages, min(MEMORY_MB, container or MEMORY_MB))
        estimate = pages if fmt == "pdf" else estimated_pages(src, fmt)
        if estimate > cap:
            more = (
                " or ask the admin to give the ebook worker more memory (FILEX_MEM_EBOOK)"
                if container is not None and container < MEMORY_MB
                else ""
            )
            raise StepError(
                "TOO_MANY_PAGES",
                f"{what} would make a PDF of about {estimate} pages; this server makes PDFs of "
                f"up to about {cap} pages from ebooks. Convert it to EPUB, MOBI or AZW3 "
                f"instead{more}.",
            )
    work = Path(tempfile.mkdtemp(prefix="calibre-"))  # under TMPDIR, the step's scratch dir
    try:
        if fmt == "html":  # alone in an empty folder: the only "root" Calibre may read from
            (work / "in").mkdir()
            book = Path(shutil.copyfile(src, work / "in" / "book.html"))
        elif fmt == "pdf":  # poppler may resolve external streams: drop them (task 64)
            book = strip_external_refs(src, work / "book.pdf", what).resolve()
        else:
            book = src.resolve()
        ext = FORMATS[dst].extensions[0]
        out = work / f"book{ext}"
        args = ["ebook-convert", str(book), str(out), *calibre_options(fmt, dst)]
        ctx.progress(0.05, "Converting with Calibre")
        code, log = _engine.run(
            args,
            cwd=work,
            timeout_s=ENGINE_TIMEOUT_S,
            what=what,
            engine="Calibre",
            env=_engine.calibre_env(work),
        )
        if code or not out.is_file() or out.stat().st_size == 0:
            raise failure(code, log, what)
        return [Path(shutil.move(out, ctx.out_dir / f"converted{ext}"))]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def calibre_options(src_fmt: str, dst: str) -> list[str]:
    opts = ["--max-levels", "0"] if src_fmt == "html" else []
    if dst == "pdf":
        opts += ["--paper-size", "a4"]
    return opts


def estimated_pages(src: Path, fmt: str) -> int:
    """About how many PDF pages the book's text fills with Calibre's defaults. Counted in bytes
    of text (markup and multi-byte letters count too, so it errs on the long side)."""
    if fmt == "txt":
        size = src.stat().st_size
    elif fmt == "html":
        size = src.stat().st_size // 2
    elif fmt == "epub":
        with zipfile.ZipFile(src) as zf:
            size = sum(i.file_size for i in zf.infolist() if _TEXT_ENTRY.search(i.filename))
        size = size * 4 // 5
    elif fmt == "docx":
        with zipfile.ZipFile(src) as zf:
            try:
                size = zf.getinfo("word/document.xml").file_size // 4
            except KeyError:
                size = 0
    elif fmt == "fb2":
        size = fb2_text_bytes(src)
    elif fmt in ("mobi", "azw3"):
        with src.open("rb") as fh:
            head = fh.read(4096)
        rec0 = struct.unpack(">I", head[78:82])[0]
        size = struct.unpack(">I", head[rec0 + 4 : rec0 + 8])[0] * 3 // 5  # PalmDOC text length
    else:
        size = 0
    return math.ceil(size / BYTES_PER_PAGE)


def fb2_text_bytes(src: Path, chunk: int = 1 << 20) -> int:
    """Bytes of an FB2 outside its ``<binary>`` sections (base64 images), streamed."""
    total, inside, tail = 0, False, b""
    with src.open("rb") as fh:
        while data := fh.read(chunk):
            data, pos = tail + data, 0
            keep = len(data) - 10  # a tag may be cut at the chunk end
            while True:
                tag = b"</binary>" if inside else b"<binary"
                at = data.find(tag, pos)
                if at < 0:
                    end = max(pos, keep)
                    if not inside:
                        total += end - pos
                    tail, pos = data[end:], end
                    break
                if not inside:
                    total += at - pos
                inside, pos = not inside, at + len(tag)
    return total + (0 if inside else len(tail))


def memory_limit_mb(paths: tuple[Path, ...] = CGROUP_MEMORY) -> int | None:
    """The container's memory limit in MB, or None when there is none (or not on Linux)."""
    for p in paths:
        try:
            raw = p.read_text().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 50:  # cgroup v1 writes a huge number for "none"
            return int(raw) // 2**20
        return None
    return None


def pdf_page_cap(max_pages: int, memory_mb: int | None) -> int:
    """The longest PDF Calibre can make within ``memory_mb``, at most ``max_pages``."""
    if memory_mb is None:
        return max_pages
    return max(0, min(max_pages, int((memory_mb - PDF_BASE_MB) / PDF_MB_PER_PAGE)))


def failure(code: int, log: str, what: str) -> StepError:
    """Our message for a Calibre run that produced no output."""
    if "DRMError" in log:
        return _books.drm_error(what)
    if _engine.memory_failure(code, log):
        return StepError(
            "ENGINE_MEMORY", f"{what} needs more memory to convert than this server allows."
        )
    return StepError(
        "ENGINE_FAILED", f"{what} could not be converted. It may be damaged or unsupported."
    )
