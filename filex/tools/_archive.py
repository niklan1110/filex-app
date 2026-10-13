"""Safe checks and unpacking for archive inputs: zip-based (CBZ, EPUB, DOCX, XLSX, PPTX, ODF) and
RAR (CBR). Review H3 fixes 1-3, plan task 63.

Every tool that unpacks an archive, or hands a zip-based file to an engine that unpacks it
itself (LibreOffice, Calibre, Pandoc), calls :func:`check` or :func:`unpack` first. Any refusal
is ``StepError("INVALID_INPUT", ...)``, raised before the engine runs.

Checks, all before anything is written:

- entries at most ``FILEX_MAX_ARCHIVE_ENTRIES`` (5000; zip: counted from the central directory
  before ``zipfile`` builds one object per entry, reusing :func:`filex.formats.zip_entry_count`);
- names: no NUL or other control characters, no absolute path, drive letter or ``..`` part
  after ``\\`` becomes ``/``; names are compared after NFC and case folding, so two entries that
  would land on the same file on some system are refused;
- entry types: regular files and directories only. Symlinks, hard links and devices are refused
  (zip: the Unix file type in ``external_attr``; RAR: libarchive's entry type);
- encrypted entries are refused;
- unpacked size at most ``FILEX_MAX_UNPACKED_MB`` (1024): first from the declared sizes, then
  from the bytes actually read, stopping in the middle of an entry once over;
- compression ratio at most 200:1 for any zip entry over 1 MB (declared, then actual). RAR does
  not give a packed size per entry (solid archives have none), so the ratio is checked for the
  whole archive: bytes read against the archive's size;
- depth 1: an entry that is itself an archive (zip, RAR, 7z, gzip, bzip2, xz, zstd, tar, cab)
  is refused unless the caller passes ``allow_nested=True`` (OOXML and ODF files embed other
  packages, such as a chart's workbook). Nested archives are never unpacked.

:func:`unpack` writes each entry with our own loop, never ``extractall``: generated names
(``0001.jpg``, the extension kept only if it is 1-8 letters or digits) in an empty directory,
opened with ``O_CREAT | O_EXCL | O_NOFOLLOW``. Archive names are for sorting and messages only.

Zip uses the stdlib. RAR uses libarchive through ``libarchive-c`` (``import libarchive`` inside
the functions, registry rule 1), with the RAR4 or RAR5 reader picked from the magic bytes and no
filters, so a file cannot choose another parser. The listing pass runs before the data pass.
"""

from __future__ import annotations

import os
import re
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from filex.formats import MAX_ARCHIVE_ENTRIES, zip_entry_count
from filex.registry import StepError

MAX_UNPACKED_BYTES = int(os.environ.get("FILEX_MAX_UNPACKED_MB", "1024")) * 2**20  # review H3
MAX_RATIO = 200
RATIO_FLOOR = 2**20  # the ratio rule applies to entries (RAR: archives) unpacking to more
CHUNK = 2**16

_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_RAR4, _RAR5 = b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00"
_NESTED = (
    *_ZIP_MAGIC,
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b",  # gzip
    b"BZh",
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",  # zstd
    b"MSCF",  # cab
)
_DRIVE = re.compile(r"[A-Za-z]:")
_EXT = re.compile(r"\.[a-z0-9]{1,8}")
_S_IFMT, _S_IFREG, _S_IFDIR = 0o170000, 0o100000, 0o040000
_OPEN_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
)


@dataclass(frozen=True)
class ArchiveLimits:
    """The limits a check applies; the defaults come from the environment (review H3)."""

    entries: int = MAX_ARCHIVE_ENTRIES
    unpacked: int = MAX_UNPACKED_BYTES
    ratio: int = MAX_RATIO
    ratio_floor: int = RATIO_FLOOR


DEFAULT_LIMITS = ArchiveLimits()


@dataclass(frozen=True)
class Entry:
    """One archive entry that passed the name and type checks."""

    name: str  # NFC, "/"-separated, no empty or "." parts: for sorting and messages, never a path
    size: int  # declared unpacked size, 0 when the archive does not say
    is_dir: bool
    index: int  # position in the archive


@dataclass(frozen=True)
class Unpacked:
    entry: Entry
    path: Path  # the generated file in ``dest``


def safe_name(raw: str) -> str | None:
    """The normalised entry name, or None if it is unsafe (control character, absolute path,
    drive letter, ``..`` part, or nothing left)."""
    if any(unicodedata.category(c) == "Cc" for c in raw):
        return None
    name = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if name.startswith("/") or _DRIVE.match(name):
        return None
    parts = name.split("/")
    if ".." in parts:
        return None
    return "/".join(p for p in parts if p not in ("", ".")) or None


def check(
    path: Path, what: str, *, allow_nested: bool = False, limits: ArchiveLimits = DEFAULT_LIMITS
) -> list[Entry]:
    """Every check above, reading every entry to the end without writing anything; returns
    the entries. For inputs an engine unpacks itself (DOCX, EPUB, ...)."""
    return _run(path, None, what, None, None, allow_nested, limits)[0]


def unpack(
    path: Path,
    dest: Path,
    what: str,
    *,
    select: Callable[[Entry], bool] | None = None,
    max_pages: int | None = None,
    allow_nested: bool = False,
    limits: ArchiveLimits = DEFAULT_LIMITS,
) -> list[Unpacked]:
    """Check, then unpack the regular files ``select`` accepts (default: all) into ``dest``,
    which must be empty or not exist yet. Returns them in archive order. More selected files
    than ``max_pages`` is ``TOO_MANY_PAGES`` (a tool passing it declares that code), raised
    before anything is unpacked. Unselected entries are name- and type-checked but not read."""
    dest.mkdir(mode=0o700, exist_ok=True)
    if any(dest.iterdir()):
        raise ValueError(f"unpack needs an empty directory, {dest} is not")
    return _run(path, dest, what, select, max_pages, allow_nested, limits)[1]


# --- shared loop


class _Budget:
    """Running totals over the bytes actually read."""

    def __init__(self, what: str, limits: ArchiveLimits, archive_size: int | None) -> None:
        self.what, self.limits, self.archive_size, self.total = what, limits, archive_size, 0

    def pump(
        self, chunks: Iterator[bytes], out: int | None, packed: int | None, allow_nested: bool
    ) -> None:
        lim, read = self.limits, 0
        for chunk in chunks:
            if not read and not allow_nested and _is_archive(chunk):
                raise _refuse(self.what, "contains another archive inside it")
            read += len(chunk)
            self.total += len(chunk)
            if self.total > lim.unpacked:
                raise _too_big(self.what, lim)
            if packed is not None and read > lim.ratio_floor and read > lim.ratio * packed:
                raise _bomb(self.what, lim)
            if (
                self.archive_size is not None
                and self.total > lim.ratio_floor
                and self.total > lim.ratio * self.archive_size
            ):
                raise _bomb(self.what, lim)
            if out is not None:
                view = memoryview(chunk)
                while view:
                    view = view[os.write(out, view) :]


def _run(
    path: Path,
    dest: Path | None,
    what: str,
    select: Callable[[Entry], bool] | None,
    max_pages: int | None,
    allow_nested: bool,
    limits: ArchiveLimits,
) -> tuple[list[Entry], list[Unpacked]]:
    with path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(_ZIP_MAGIC):
        reader = _ZipReader(path, what, limits)
    elif head.startswith((_RAR4, _RAR5)):
        reader = _RarReader(path, what, limits, "rar5" if head.startswith(_RAR5) else "rar")
    else:
        raise _refuse(what, "is not a zip or RAR archive")
    with reader:
        entries = reader.entries
        _check_listing(entries, what, limits)
        wanted = [e for e in entries if not e.is_dir and (select is None or select(e))]
        if max_pages is not None and len(wanted) > max_pages:
            raise StepError(
                "TOO_MANY_PAGES", f"{what} has {len(wanted)} pages; the limit is {max_pages}."
            )
        budget = _Budget(what, limits, reader.archive_size)
        done: list[Unpacked] = []
        for n, (entry, chunks, packed) in enumerate(reader.data({e.index for e in wanted}), 1):
            if dest is None:
                budget.pump(chunks, None, packed, allow_nested)
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            target = dest / f"{n:04d}{ext if _EXT.fullmatch(ext) else ''}"
            fd = os.open(target, _OPEN_FLAGS, 0o600)
            try:
                budget.pump(chunks, fd, packed, allow_nested)
            except BaseException:
                os.close(fd)
                target.unlink(missing_ok=True)
                raise
            os.close(fd)
            done.append(Unpacked(entry, target))
        return entries, done


def _check_listing(entries: list[Entry], what: str, limits: ArchiveLimits) -> None:
    seen: set[str] = set()
    for e in entries:
        if e.is_dir:
            continue
        key = e.name.casefold()
        if key in seen:
            raise _refuse(what, "contains two files with the same name")
        seen.add(key)
    if sum(e.size for e in entries) > limits.unpacked:
        raise _too_big(what, limits)


# --- zip (stdlib)


class _ZipReader:
    archive_size = None  # zip checks the ratio per entry

    def __init__(self, path: Path, what: str, limits: ArchiveLimits) -> None:
        self.what = what
        self.fh = path.open("rb")
        try:
            count = zip_entry_count(self.fh, limits.entries)
            if count is not None and count > limits.entries:
                raise _refuse(what, f"has more than {limits.entries} files")
            self.zf = zipfile.ZipFile(self.fh)
            self.infos = self.zf.infolist()
        except StepError:
            self.fh.close()
            raise
        except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError) as e:
            self.fh.close()
            raise _unreadable(what) from e
        if len(self.infos) > limits.entries:
            self.close()
            raise _refuse(what, f"has more than {limits.entries} files")
        try:
            self.entries = [self._entry(i, info, limits) for i, info in enumerate(self.infos)]
        except StepError:
            self.close()
            raise

    def _entry(self, index: int, info: zipfile.ZipInfo, limits: ArchiveLimits) -> Entry:
        name = safe_name(info.filename)
        if name is None:
            raise _refuse(self.what, "contains a file with an unsafe name")
        kind = (info.external_attr >> 16) & _S_IFMT
        if kind not in (0, _S_IFREG, _S_IFDIR):
            raise _refuse(self.what, "contains a link or device entry")
        if info.flag_bits & 1:
            raise _refuse(self.what, "is password-protected")
        size = info.file_size
        if size > limits.ratio_floor and size > limits.ratio * info.compress_size:
            raise _bomb(self.what, limits)
        return Entry(name, size, info.is_dir() or kind == _S_IFDIR, index)

    def data(self, wanted: set[int]) -> Iterator[tuple[Entry, Iterator[bytes], int]]:
        for e in self.entries:
            if e.index in wanted:
                info = self.infos[e.index]
                yield e, self._chunks(info), info.compress_size

    def _chunks(self, info: zipfile.ZipInfo) -> Iterator[bytes]:
        try:
            with self.zf.open(info) as src:
                while chunk := src.read(CHUNK):
                    yield chunk
        except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError) as e:
            raise _unreadable(self.what) from e

    def close(self) -> None:
        self.zf.close()
        self.fh.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --- RAR (libarchive)


class _RarReader:
    def __init__(self, path: Path, what: str, limits: ArchiveLimits, fmt: str) -> None:
        import libarchive

        self.path, self.what, self.fmt, self.lib = path, what, fmt, libarchive
        self.archive_size = path.stat().st_size
        self.entries: list[Entry] = []
        try:
            with self._open() as archive:
                for index, item in enumerate(archive):  # headers only: data is skipped
                    if index >= limits.entries:
                        raise _refuse(what, f"has more than {limits.entries} files")
                    self.entries.append(self._entry(index, item))
        except libarchive.ArchiveError as e:
            raise _unreadable(what) from e

    def _open(self):  # libarchive's context manager
        return self.lib.file_reader(str(self.path), format_name=self.fmt, filter_name="none")

    def _entry(self, index: int, item: object) -> Entry:
        raw = item.pathname
        if isinstance(raw, bytes):  # not decodable in the archive's charset: a display name only
            raw = raw.decode("utf-8", "replace")
        name = safe_name(raw or "")
        if name is None:
            raise _refuse(self.what, "contains a file with an unsafe name")
        if item.islnk or not (item.isreg or item.isdir):  # a hard link also reports isreg
            raise _refuse(self.what, "contains a link or device entry")
        if getattr(item, "is_encrypted", False):
            raise _refuse(self.what, "is password-protected")
        return Entry(name, item.size or 0, bool(item.isdir), index)

    def data(self, wanted: set[int]) -> Iterator[tuple[Entry, Iterator[bytes], None]]:
        try:
            with self._open() as archive:
                for index, item in enumerate(archive):
                    if index >= len(self.entries):
                        raise _unreadable(self.what)  # changed between passes
                    if index in wanted:
                        yield self.entries[index], self._chunks(item), None
        except self.lib.ArchiveError as e:
            raise _unreadable(self.what) from e

    def _chunks(self, item: object) -> Iterator[bytes]:
        try:
            yield from item.get_blocks(CHUNK)
        except self.lib.ArchiveError as e:
            raise _unreadable(self.what) from e

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


# --- messages


def _is_archive(chunk: bytes) -> bool:
    return chunk.startswith(_NESTED) or chunk[257:262] == b"ustar"


def _refuse(what: str, reason: str) -> StepError:
    return StepError("INVALID_INPUT", f"{what} {reason}, so FileX will not open it.")


def _too_big(what: str, limits: ArchiveLimits) -> StepError:
    return _refuse(what, f"would unpack to more than {limits.unpacked // 2**20} MB")


def _bomb(what: str, limits: ArchiveLimits) -> StepError:
    return _refuse(what, f"is compressed more than {limits.ratio}:1, like a zip bomb")


def _unreadable(what: str) -> StepError:
    return StepError("INVALID_INPUT", f"{what} is damaged, password-protected or not readable.")
