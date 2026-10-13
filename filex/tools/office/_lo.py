"""LibreOffice runner shared by the office tools (ADR-0007 Appendix A, review H2 and H4).

One ``soffice --headless --convert-to`` process per conversion, never an ``--accept`` listener.
Each run copies the read-only profile template (``/opt/filex/lo-profile``, built into the
``worker-office`` image: macros off, active content off, link updates never) into its own work
dir under ``TMPDIR`` (the step's scratch dir) and points ``-env:UserInstallation`` at the copy,
so LibreOffice never writes to the template and nothing survives the step. The input filter is
forced with ``--infilter``, so a file cannot pick a different, riskier parser for itself.

soffice runs in its own process group inside the step's session: on our timeout or any error the
whole group (soffice and soffice.bin) is killed. The runner's own deadline kills the step child,
and its escaped-process sweep then kills anything left in the step's session; a new session
(``setsid``) would count as an escape, so it is not used.

LibreOffice's output never reaches the user: failures map to our own messages, and its console
output goes to the step log (stderr) only.
"""

from __future__ import annotations

import codecs
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from filex.formats import FORMATS
from filex.registry import StepError
from filex.tools import _archive, _ooxml
from filex.tools.office import _rtf

TEMPLATE = Path("/opt/filex/lo-profile")

DOCUMENTS = ("doc", "docx", "odt", "rtf", "txt")
SHEETS = ("xls", "xlsx", "ods", "csv")
SLIDES = ("ppt", "pptx", "odp")
OFFICE = (*DOCUMENTS, *SHEETS, *SLIDES)
ZIPPED = ("docx", "odt", "xlsx", "ods", "pptx", "odp")  # archive pre-check first (review H3)
OOXML = ("docx", "xlsx", "pptx")

# LibreOffice filter names for import (--infilter) and export (--convert-to). txt and csv take
# filter options as well, see filter_for().
_FILTERS = {
    "doc": "MS Word 97",
    "docx": "MS Word 2007 XML",
    "odt": "writer8",
    "rtf": "Rich Text Format",
    "xls": "MS Excel 97",
    "xlsx": "Calc MS Excel 2007 XML",
    "ods": "calc8",
    "ppt": "MS PowerPoint 97",
    "pptx": "Impress MS PowerPoint 2007 XML",
    "odp": "impress8",
}
_PDF_EXPORT = {"documents": "writer_pdf_Export", "sheets": "calc_pdf_Export"}
_CSV = "Text - txt - csv (StarCalc)"
# Character set numbers (rtl_TextEncoding) for the CSV filter's third token.
_CSV_CHARSETS = {"UTF8": 76, "UNICODE": 65535, "MS_1251": 34, "MS_1252": 1}
# CSV import only: tokens 5-12 at their import defaults, then token 13 "Import as formulas"
# false, so a cell "=..." stays text (tools review L-5). Token positions:
# https://help.libreoffice.org/latest/en-US/text/shared/guide/csv_params.html
_CSV_IMPORT = ",,0,false,false,true,false,false,0,false"
_BY_EXT = {f.extensions[0]: f.id for f in FORMATS.values()}


def family(fmt: str) -> str:
    return "documents" if fmt in DOCUMENTS else "sheets" if fmt in SHEETS else "slides"


def format_of(src: Path) -> str:
    """The input's format. The runner names scratch inputs ``<n><extension>`` after the sniffed
    format, so the suffix is the format, not the user's file name."""
    fmt = _BY_EXT.get(src.suffix.lower())
    if fmt not in OFFICE:
        raise StepError("INVALID_INPUT", "This file type cannot be opened by the office tools.")
    return fmt


def text_charset(src: Path) -> str:
    """Character set of a txt or csv input: UTF-8 (with or without BOM), UTF-16 with a BOM, else
    8-bit text, read as Windows-1251 when most letters are high bytes (Cyrillic) and as
    Windows-1252 otherwise. Judged from the first megabyte."""
    with src.open("rb") as fh:
        head = fh.read(1 << 20)
    if head.startswith((b"\xff\xfe\0\0", b"\0\0\xfe\xff")):
        raise StepError("INVALID_INPUT", "UTF-32 text is not supported. Save it as UTF-8.")
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "UNICODE"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        return "UTF8"
    except UnicodeDecodeError:
        pass
    high = sum(b >= 0xC0 for b in head)
    ascii_letters = sum(65 <= b <= 90 or 97 <= b <= 122 for b in head)
    return "MS_1251" if high > ascii_letters else "MS_1252"


def filter_for(fmt: str, charset: str = "UTF8") -> str:
    """The filter (with options) that reads or writes ``fmt``; ``charset`` is for txt and csv."""
    if fmt == "txt":
        return f"Text (encoded):{charset}"
    if fmt == "csv":  # comma, double quotes, charset, start at line 1
        return f"{_CSV}:44,34,{_CSV_CHARSETS[charset]},1"
    return _FILTERS[fmt]


def pdf_filter(src_fmt: str, options: Mapping[str, Any]) -> str:
    """``--convert-to`` value for PDF: the export filter for the input's family, with the
    filter options as JSON (LibreOffice 7.4+)."""
    name = _PDF_EXPORT.get(family(src_fmt), "impress_pdf_Export")
    if not options:
        return f"pdf:{name}"
    typed = {
        k: {"type": _TYPES[type(v)], "value": str(v).lower() if isinstance(v, bool) else str(v)}
        for k, v in options.items()
    }
    return f"pdf:{name}:{json.dumps(typed, separators=(',', ':'))}"


_TYPES = {bool: "boolean", int: "long", str: "string"}


def command(src: Path, src_fmt: str, convert_to: str, profile: Path, out: Path) -> list[str]:
    charset = text_charset(src) if src_fmt in ("txt", "csv") else "UTF8"
    return [
        "soffice",
        "--headless",
        "--invisible",
        "--norestore",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        f"-env:UserInstallation={profile.as_uri()}",
        f"--infilter={filter_for(src_fmt, charset)}{_CSV_IMPORT if src_fmt == 'csv' else ''}",
        "--convert-to",
        convert_to,
        "--outdir",
        str(out),
        str(src),
    ]


def convert(
    src: Path, dst_fmt: str, convert_to: str, out_dir: Path, what: str, timeout_s: float
) -> Path:
    """Convert ``src`` with one soffice run and move the result to ``out_dir``. ``convert_to``
    is the ``--convert-to`` value (target extension and export filter)."""
    src_fmt = format_of(src)
    if src_fmt in ZIPPED:  # embedded workbooks and OLE objects are packages: allow_nested
        _archive.check(src, what, allow_nested=True)
    if src_fmt in OOXML:  # links to outside files: defence in depth (task 64, _ooxml.py)
        _ooxml.check_links(src)
    if src_fmt == "rtf":  # LibreOffice follows INCLUDEPICTURE fields in RTF (task 64)
        _rtf.check_file(src)
    work = Path(tempfile.mkdtemp(prefix="lo-"))  # under TMPDIR, the step's scratch dir
    try:
        profile = work / "profile"
        shutil.copytree(TEMPLATE, profile)  # never run LibreOffice on the template itself
        out = work / "out"
        out.mkdir()
        args = command(src.resolve(), src_fmt, convert_to, profile, out)
        log = run_soffice(args, work, timeout_s, what)
        ext = FORMATS[dst_fmt].extensions[0]
        produced = out / f"{src.stem}{ext}"
        if not produced.is_file() or produced.stat().st_size == 0:
            sys.stderr.write(log[-2000:])
            raise failure(log, what, "PageRange" in convert_to)
        return Path(shutil.move(produced, out_dir / f"converted{ext}"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_soffice(args: list[str], work: Path, timeout_s: float, what: str) -> str:
    """Run soffice in its own process group, kill the group when it ends or times out, and
    return its console output (for the log and failure(), never for the user)."""
    env = {**os.environ, "HOME": str(work), "TMPDIR": str(work)}
    try:
        proc = subprocess.Popen(
            args,
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            process_group=0,
        )
    except OSError as e:
        raise StepError("ENGINE_FAILED", "LibreOffice could not start.") from e
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise StepError("ENGINE_FAILED", f"{what} took too long to convert.") from e
    finally:
        _kill_group(proc.pid)
        proc.wait()
    return out.decode("utf-8", "replace")


def _kill_group(pgid: int) -> None:
    """SIGKILL the group (soffice.bin too, if it outlived soffice). Repeated a few times: a
    process forked while the first signal was sent can miss it. The group is usually empty."""
    for _ in range(3):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)


def failure(log: str, what: str, page_range: bool) -> StepError:
    """Our message for a run that produced no output, judged from LibreOffice's console."""
    if "source file could not be loaded" in log:
        return StepError(
            "INVALID_INPUT",
            f"{what} could not be opened. It may be damaged or password-protected.",
        )
    if page_range and "impl_store" in log:  # an empty page selection fails the store
        return StepError("INVALID_OPTIONS", f"{what} has no pages in the selected range.")
    return StepError("ENGINE_FAILED", f"{what} could not be converted.")
