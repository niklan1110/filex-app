"""Streamed multipart upload straight into ``/data/jobs/<id>/in`` (review M4, spec 5.2).

Bytes are counted while they arrive, per file and per job, and the upload stops with ``413`` the
moment a cap is crossed; nothing is spooled in memory or in a temp file first. Form limits: at
most ``max_files`` file parts, 10 other fields, a 64 KB ``options`` part and a short ``target``
part (converters: the output format id, ADR-0005). Each file is sniffed
by its bytes against the tool's declared formats and renamed to ``in/<n>.<ext>`` (or
``in/aux/<slot>.<ext>``); the client's name is kept for display only. The caller deletes the job
directory when this raises.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from shutil import disk_usage
from typing import BinaryIO

import anyio
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect, Request

from filex import formats
from filex.api.deps import ApiError, MiB, Settings
from filex.api.janitor import UPLOAD_MARKER
from filex.api.names import display_name
from filex.jobs import JobPaths
from filex.registry import Tool

MAX_FIELDS = 10
MAX_OPTIONS = 64 * 1024
MAX_TARGET = 32  # a format id
DISK_CHECK_BYTES = 64 * MiB  # free space is checked again this often while bytes arrive
FORM_OVERHEAD = 1024 * 1024  # part headers and boundaries on top of the payload caps


@dataclass
class Stored:
    name: str  # display name
    format: str
    size: int


@dataclass
class Upload:
    files: list[Stored] = field(default_factory=list)
    aux: dict[str, str] = field(default_factory=dict)  # slot -> format
    options: bytes | None = None
    target: str | None = None  # the raw ``target`` field, stripped


def insufficient_storage() -> ApiError:
    return ApiError(
        507,
        "INSUFFICIENT_STORAGE",
        "The server is almost out of disk space. Try again later or with smaller files.",
    )


def _too_large(code: str, what: str, limit: int) -> ApiError:
    mb = limit / (1024 * 1024)
    return ApiError(413, code, f"{what} is larger than {mb:g} MB.", {"limitBytes": limit})


@dataclass
class _Part:
    field: str
    raw_name: str | None
    slot: str | None = None  # aux slot; None for main files and fields
    path: Path | None = None
    fh: BinaryIO | None = None
    size: int = 0
    buf: bytearray | None = None  # the options or target part


class _Receiver:
    """The parser's callbacks. They run in a worker thread, so file writes never block the
    event loop; anything they raise aborts the upload."""

    def __init__(self, tool: Tool, paths: JobPaths, settings: Settings) -> None:
        self.tool, self.paths, self.settings = tool, paths, settings
        self.upload = Upload()
        self.parts: list[_Part] = []  # file parts, in order, waiting to be sniffed
        self.part: _Part | None = None
        self.headers: dict[str, str] = {}
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.n_files = self.n_fields = self.total = 0
        self.next_disk_check = DISK_CHECK_BYTES
        self.ended = False

    def callbacks(self) -> dict:
        def data(attr):
            return lambda d, s, e: getattr(self, attr).extend(d[s:e])

        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": data("header_field"),
            "on_header_value": data("header_value"),
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_end": self.on_end,
        }

    def on_part_begin(self) -> None:
        self.headers = {}

    def on_header_end(self) -> None:
        # Header bytes as latin-1 round-trip; names are UTF-8 in practice, decoded below.
        key = self.header_field.decode("latin-1").strip().lower()
        self.headers[key] = self.header_value.decode("latin-1").strip()
        self.header_field.clear()
        self.header_value.clear()

    def on_headers_finished(self) -> None:
        kind, params = parse_options_header(self.headers.get("content-disposition", ""))
        name = params.get(b"name", b"").decode("utf-8", "replace")
        raw = params.get(b"filename")
        raw_name = raw.decode("utf-8", "replace") if raw is not None else None
        if kind != b"form-data" or not name:
            raise ApiError(422, "INVALID_REQUEST", "A form part has no name.")
        if raw is None:
            self.n_fields += 1
            if self.n_fields > MAX_FIELDS:
                raise ApiError(422, "TOO_MANY_FIELDS", f"At most {MAX_FIELDS} form fields.")
        else:
            self.n_files += 1
            if self.n_files > self.settings.max_files:
                raise ApiError(
                    422, "TOO_MANY_FILES", f"At most {self.settings.max_files} files per job."
                )
        part = _Part(name, raw_name)
        if name == "options":
            if self.upload.options is not None:
                raise ApiError(422, "INVALID_REQUEST", "Send the options part once.")
            part.buf = bytearray()
        elif name == "target":
            if self.upload.target is not None:
                raise ApiError(422, "INVALID_REQUEST", "Send the target part once.")
            part.buf = bytearray()
        elif name == "files":
            n = len([p for p in self.parts if p.slot is None])
            if n >= self.tool.files.max:
                raise ApiError(
                    422, "TOO_MANY_FILES", f"This tool takes at most {self.tool.files.max} files."
                )
            part.path = self.paths.in_dir / f"{n}.part"
        elif name in self.tool.files.aux:
            if any(p.slot == name for p in self.parts):
                raise ApiError(422, "INVALID_REQUEST", f"Send the {name} part once.")
            part.slot = name
            self.paths.aux_dir.mkdir(exist_ok=True)
            part.path = self.paths.aux_dir / f"{name}.part"
        elif name == "inputs":  # reserved for job outputs as inputs (ADR-0003 A5)
            raise ApiError(
                422, "NOT_SUPPORTED", "Using job outputs as inputs is not supported yet."
            )
        else:
            raise ApiError(422, "UNKNOWN_PART", "This tool has no form part with that name.")
        if part.path is not None:
            part.fh = part.path.open("xb")
            self.parts.append(part)
        self.part = part

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        part, chunk = self.part, data[start:end]
        if part is None:
            return
        if part.buf is not None:
            if part.field == "target" and len(part.buf) + len(chunk) > MAX_TARGET:
                raise ApiError(422, "INVALID_TARGET", "The target is not a format id.")
            if len(part.buf) + len(chunk) > MAX_OPTIONS:
                raise _too_large("OPTIONS_TOO_LARGE", "The options part", MAX_OPTIONS)
            part.buf.extend(chunk)
            return
        part.size += len(chunk)
        self.total += len(chunk)
        if part.size > self.settings.max_file:
            raise _too_large("FILE_TOO_LARGE", "A file", self.settings.max_file)
        if self.total > self.settings.max_upload:
            raise _too_large("UPLOAD_TOO_LARGE", "The upload", self.settings.max_upload)
        if self.settings.min_free_mb and self.total >= self.next_disk_check:
            # Review L-2: the check before the upload trusts Content-Length (0 when chunked)
            # and cannot see uploads running in parallel.
            self.next_disk_check += DISK_CHECK_BYTES
            if disk_usage(self.paths.root).free < self.settings.min_free_mb * MiB:
                raise insufficient_storage()
        part.fh.write(chunk)

    def on_part_end(self) -> None:
        part, self.part = self.part, None
        if part is None:
            return
        if part.field == "target":
            self.upload.target = part.buf.decode("utf-8", "replace").strip()
        elif part.buf is not None:
            self.upload.options = bytes(part.buf)
        elif part.fh is not None:
            part.fh.close()

    def on_end(self) -> None:
        self.ended = True

    def close(self) -> None:
        for p in self.parts:
            if p.fh is not None and not p.fh.closed:
                p.fh.close()

    def finish(self) -> Upload:
        """Sniff and rename every file; check the counts the tool declares."""
        for p in self.parts:
            allowed = self.tool.files.aux[p.slot].formats if p.slot else self.tool.inputs
            name = display_name(p.raw_name)
            fmt = _sniff(p.path, name)
            if fmt not in allowed:
                shown = name or "A file"
                raise ApiError(
                    422,
                    "INVALID_INPUT_FORMAT",
                    f"{shown} is not a file type {self.tool.title} accepts.",
                    {"part": p.field, "format": fmt, "accepted": list(allowed)},
                )
            if p.slot:
                os.replace(p.path, self.paths.aux(p.slot, fmt))
                self.upload.aux[p.slot] = fmt
            else:
                n = len(self.upload.files)
                os.replace(p.path, self.paths.input(n, fmt))
                self.upload.files.append(Stored(name, fmt, p.size))
        files = self.tool.files
        if not files.min <= len(self.upload.files) <= files.max:
            raise ApiError(
                422,
                "INVALID_FILE_COUNT",
                f"This tool takes {files.min} to {files.max} files.",
                {"min": files.min, "max": files.max},
            )
        missing = [s for s, a in files.aux.items() if a.required and s not in self.upload.aux]
        if missing:
            raise ApiError(422, "MISSING_PART", "A required file is missing.", {"parts": missing})
        return self.upload


def _sniff(path: Path, name: str) -> str | None:
    fmt = formats.sniff(path, name)
    if fmt is None:
        with path.open("rb") as fh:
            is_zip = fh.read(4) in (b"PK\x03\x04", b"PK\x05\x06")
            try:
                count = formats.zip_entry_count(fh, formats.MAX_ARCHIVE_ENTRIES) if is_zip else 0
            except (OSError, ValueError, OverflowError):  # struct.error is a ValueError
                count = 0
        if count and count > formats.MAX_ARCHIVE_ENTRIES:
            raise ApiError(
                422,
                "TOO_MANY_ARCHIVE_ENTRIES",
                f"The archive has more than {formats.MAX_ARCHIVE_ENTRIES} entries.",
                {"limit": formats.MAX_ARCHIVE_ENTRIES},
            )
    return fmt


async def receive(request: Request, tool: Tool, paths: JobPaths, settings: Settings) -> Upload:
    """Stream the request body into ``paths.in_dir``. Raises ApiError; never cleans up."""
    kind, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise ApiError(422, "INVALID_REQUEST", "Send the job as multipart/form-data.")
    body_cap = settings.max_upload + MAX_OPTIONS + FORM_OVERHEAD
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > body_cap:  # an early hint only; bytes are counted
        raise _too_large("UPLOAD_TOO_LARGE", "The upload", settings.max_upload)

    paths.in_dir.mkdir(parents=True)
    (paths.in_dir / UPLOAD_MARKER).touch(exist_ok=False)  # the caller removes it (janitor)
    rx = _Receiver(tool, paths, settings)
    parser = MultipartParser(boundary, rx.callbacks())
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > body_cap:
                raise _too_large("UPLOAD_TOO_LARGE", "The upload", settings.max_upload)
            if chunk:
                await anyio.to_thread.run_sync(parser.write, chunk)
    except ClientDisconnect:
        raise ApiError(400, "INVALID_REQUEST", "The upload was interrupted.") from None
    except FormParserError:
        raise ApiError(422, "INVALID_REQUEST", "The upload is not valid multipart.") from None
    finally:
        rx.close()
    if not rx.ended:
        raise ApiError(422, "INVALID_REQUEST", "The upload is incomplete.")
    return await anyio.to_thread.run_sync(rx.finish)
