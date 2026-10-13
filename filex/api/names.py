"""User-facing file names: what an upload is shown as (review M4) and what a download is saved
as (review M5). Stored names never come from here: they are ``in/<n>.<ext>`` (``filex.jobs``)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import quote

from filex.jobs import ext

MAX_DISPLAY = 200
MAX_DOWNLOAD = 150
# Quotes and path separators, plus what Windows forbids in names (":" also means an alternate
# data stream): stripped from download and zip entry names (review L-6).
UNSAFE_IN_NAME = r'["\\/:*?<>|]'


def _printable(text: str) -> str:
    """Drop control, format (bidi overrides), surrogate and unassigned characters."""
    return "".join(c for c in text if unicodedata.category(c)[0] != "C")


def display_name(raw: str | None) -> str:
    """NFC, no path parts or control characters, at most 200 characters; may be empty."""
    if not raw:
        return ""
    name = re.split(r"[/\\]", unicodedata.normalize("NFC", raw))[-1]
    name = _printable(name).strip()
    return "" if name in (".", "..") else name[:MAX_DISPLAY]


def download_name(display: str, fmt: str, job_id: str) -> str:
    """The display name with the output format's extension, at most 150 characters."""
    suffix = ext(fmt)
    stem = display
    if PurePosixPath(display).suffix:
        stem = display[: -len(PurePosixPath(display).suffix)]
    stem = _printable(re.sub(UNSAFE_IN_NAME, "", stem)).strip()
    if not stem:
        return f"filex-{job_id[2:10]}{suffix}"
    return stem[: MAX_DOWNLOAD - len(suffix)] + suffix


def content_disposition(name: str, job_id: str) -> str:
    """``attachment`` with an ASCII fallback and the exact name as RFC 5987 ``filename*``."""
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""

    def to_ascii(text: str) -> str:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return re.sub(r'[^\x20-\x7e]|["\\]', "", text).strip()

    ascii_stem, suffix = to_ascii(stem), to_ascii(suffix)
    ascii_name = (ascii_stem or f"filex-{job_id[2:10]}") + dot + suffix
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def unique(names: list[str]) -> list[str]:
    """``a.pdf``, ``a (2).pdf``, ... so a zip of results never overwrites an entry."""
    seen: set[str] = set()
    out = []
    for name in names:
        stem, dot, suffix = name.rpartition(".")
        if not dot:
            stem, suffix = name, ""
        candidate, k = name, 1
        while candidate.lower() in seen:
            k += 1
            candidate = f"{stem} ({k}){dot}{suffix}"
        seen.add(candidate.lower())
        out.append(candidate)
    return out
