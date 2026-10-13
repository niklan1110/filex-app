"""RTF pre-scan before LibreOffice opens the file (plan task 64, review H2).

LibreOffice's RTF import follows ``INCLUDEPICTURE`` fields: the picture at the named path (another
job's input, absolute or relative) ends up in the output, and no profile setting turns that off.
So the office tools refuse an RTF that names outside files at all, before LibreOffice sees it.

What is refused (field names from the RTF 1.9.1 spec, "Fields", and Word's field reference):

- a field instruction (``\\fldinst``) for a field that reads another file: ``INCLUDEPICTURE``,
  ``INCLUDETEXT``, ``INCLUDE`` (Word 6 name of INCLUDETEXT), ``IMPORT`` (old name of
  INCLUDEPICTURE), ``LINK``, ``DDE``, ``DDEAUTO``, ``DATABASE`` (its ``\\d`` file), ``RD``
  (referenced document); ``INCLUDEPICTURE`` and ``INCLUDETEXT`` anywhere in an instruction
  (``IF`` fields can carry them), the rest as the field name;
- ``HYPERLINK`` to a local file: ``file:``, an absolute or drive path, or ``..``;
- a shape property naming a linked picture file: ``pibName``, ``fillBlipName``;
- linked OLE objects: ``\\objlink``, ``\\objautlink``.

The check reads a normalised view of each instruction, so an obfuscated name cannot slip past:
``\\'hh`` and ``\\uN`` escapes decoded, ``\\bin`` data skipped (as LibreOffice does), control
words, braces and line breaks dropped, case ignored, only letters compared. Text that LibreOffice
might or might not count is judged both ways: with and without the ``\\uN`` fallback characters,
with and without ``\\*`` destinations and nested groups. Ordinary fields (PAGE, REF, TOC, web
hyperlinks...) pass. A file nested deeper than ``MAX_DEPTH`` groups, or with more than
``MAX_FIELDS`` field instructions open inside each other, is refused: it is not worth deciding. Files without any field, shape property or object link are not parsed at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from filex.registry import StepError

MAX_DEPTH = 256
MAX_FIELDS = 16  # field instructions and shape properties open at once
LINKS = "This RTF links to files outside the document, which FileX does not allow."
NESTED = "This RTF is nested too deeply to check safely."

_ANYWHERE = ("INCLUDEPICTURE", "INCLUDETEXT")
_NAMES = ("INCLUDE", "IMPORT", "LINK", "DDE", "DATABASE", "RD")
_SHAPE_LINKS = {"PIBNAME", "FILLBLIPNAME"}
_OBJECT_LINKS = {"objlink", "objautlink"}
_WATCH = re.compile(rb"\\(?:fldinst|sn|objautlink|objlink)(?![a-zA-Z])", re.IGNORECASE)
_TOKEN = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?"  # control word, its delimiting space is not text
    rb"|\\'([0-9a-fA-F]{2})"  # hex escape
    rb"|\\(.)"  # control symbol
    rb"|([{}])"
    rb"|[\r\n]+"  # line breaks are not text
    rb"|([^\\{}\r\n]+)",  # text
    re.DOTALL,
)
_SYMBOLS = {b"\\": "\\", b"{": "{", b"}": "}", b"~": " ", b"_": "-", b"\t": " "}
_SPACES = {"par", "line", "tab", "sect", "page", "cell", "row"}


@dataclass
class _Collector:
    """Text of one ``\\fldinst`` or ``\\sn`` group: ``(text, nested, starred, fallback)``."""

    kind: str
    depth: int
    pieces: list[tuple[str, bool, bool, bool]] = field(default_factory=list)

    def views(self) -> set[str]:
        out = set()
        for keep_fallback in (False, True):
            for drop in ("", "starred", "nested"):
                out.add(
                    "".join(
                        t
                        for t, nested, starred, fb in self.pieces
                        if (keep_fallback or not fb)
                        and not (drop == "nested" and nested)
                        and not (drop == "starred" and starred)
                    ).upper()
                )
        return out


@dataclass
class _Group:
    uc: int = 1
    stars: tuple[int, ...] = ()  # depths of the \* destinations this group is inside


def check_file(path: Path) -> None:
    check(path.read_bytes())


def check(data: bytes) -> None:
    """Raise INVALID_INPUT when the RTF ``data`` refers to outside files (module docstring)."""
    if not _WATCH.search(data):
        return
    stack = [_Group()]
    active: list[_Collector] = []
    skip = 0  # \uN fallback characters still to mark
    pos, end = 0, len(data)
    while pos < end:
        m = _TOKEN.match(data, pos)
        if m is None:  # a lone backslash at the very end
            break
        pos = m.end()
        word, num, hexa, sym, brace, text = m.groups()
        top, depth = stack[-1], len(stack) - 1

        if brace == b"{":
            if len(stack) > MAX_DEPTH:
                raise StepError("INVALID_INPUT", NESTED)
            stack.append(_Group(top.uc, top.stars))
            skip = 0
        elif brace == b"}":
            skip = 0
            _close(active, depth)
            if len(stack) > 1:
                stack.pop()
        elif word is not None:
            name = word.decode().lower()
            n = int(num) if num else None
            if name == "bin" and n:  # binary data: skipped, never text
                pos = min(end, pos + max(n, 0))
            elif skip:
                skip -= 1
            elif name in ("fldinst", "sn"):
                if len(active) >= MAX_FIELDS:  # each one copies all text after it (review L-4)
                    raise StepError("INVALID_INPUT", NESTED)
                active.append(_Collector(name, depth))
            elif name in _OBJECT_LINKS:
                raise StepError("INVALID_INPUT", LINKS)
            elif name == "uc" and n is not None:
                top.uc = max(n, 0)
            elif name == "u" and n is not None:
                _emit(active, top, depth, chr(n % 65536))
                skip = top.uc
            elif name in _SPACES:
                _emit(active, top, depth, " ")
        elif hexa is not None:
            _emit(active, top, depth, chr(int(hexa, 16)), fallback=skip > 0)
            skip = max(skip - 1, 0)
        elif sym is not None:
            if sym == b"*":
                top.stars = (*top.stars, depth)
            elif skip:
                skip -= 1
            else:
                _emit(active, top, depth, _SYMBOLS.get(sym, ""))
        elif text is not None:
            s = text.decode("latin-1")
            if skip:
                _emit(active, top, depth, s[:skip], fallback=True)
                s, skip = s[skip:], max(skip - len(s), 0)
            if s:
                _emit(active, top, depth, s)
    _close(active, -1)


def _emit(
    active: list[_Collector], top: _Group, depth: int, s: str, fallback: bool = False
) -> None:
    for c in active:
        starred = any(d > c.depth for d in top.stars)
        c.pieces.append((s, depth > c.depth, starred, fallback))


def _close(active: list[_Collector], depth: int) -> None:
    """Check and drop the collectors whose group (at ``depth``) ends; -1 ends them all."""
    for c in [c for c in active if c.depth >= depth or depth < 0]:
        active.remove(c)
        for view in c.views():
            if _refused(c.kind, view):
                raise StepError("INVALID_INPUT", LINKS)


def _refused(kind: str, text: str) -> bool:
    letters = re.sub(r"[^A-Z]", "", text)
    if kind == "sn":
        return letters in _SHAPE_LINKS
    if any(k in letters for k in _ANYWHERE) or letters.startswith(_NAMES):
        return True
    return letters.startswith("HYPERLINK") and _local_target(text)


def _local_target(instr: str) -> bool:
    """Whether a HYPERLINK instruction (upper case) points at a local file."""
    if "FILE:" in re.sub(r"\s", "", instr):
        return True
    for quoted, bare in re.findall(r'"([^"]*)"|([^\s"]+)', instr):
        arg = (quoted or bare).strip()
        if bare and re.fullmatch(r"\\[A-Z*@#!]", arg):  # a switch like \l
            continue
        if arg.startswith(("/", "\\")) or re.match(r"[A-Z]:", arg):
            return True
        if re.search(r"\.\.[/\\]", arg):
            return True
    return False
