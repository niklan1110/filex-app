"""Job files opened by the API without following links (Task 61, review H-1, ADR-0007 item 5).

A step can write anywhere in ``/data``, so any name below ``/data/jobs/<id>`` may be swapped for a
symlink (or a FIFO) between a check and an open. The API therefore never opens a job file by its
full path name:

- ``JobDir`` walks from the data volume to the directory one component at a time with
  ``O_DIRECTORY | O_NOFOLLOW`` and keeps that descriptor;
- ``open_regular`` opens the file relative to it with ``O_NOFOLLOW | O_NONBLOCK`` and checks the
  descriptor with ``fstat`` (regular file, at most two links: the runner's hard link from
  ``steps/`` to ``out/``);
- callers serve and zip from that descriptor only (``OpenFileResponse``).

Windows (local test runs only, never a deployment target) has neither ``dir_fd`` nor
``O_NOFOLLOW``: there each directory is ``lstat``-ed and the opened file must be the inode the
``lstat`` saw. That narrows the race but does not close it. Both checks also run on Linux.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO, Self

import anyio
from starlette.responses import FileResponse
from starlette.types import Send

MAX_LINKS = 2
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_READ = os.O_RDONLY | _BINARY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY | _NOFOLLOW
WALK = bool(_NOFOLLOW) and os.open in os.supports_dir_fd

_open = os.open  # tests patch this to stand in for a swap the kernel would have to race


class JobDir:
    """A directory below the data volume, reached without following a link on the way."""

    def __init__(self, root: Path, *names: str) -> None:
        self.path = root.joinpath(*names)
        self.fd: int | None = None
        if WALK:
            fd = _open(root, _DIR)  # the volume itself is configuration, not job data
            try:
                for name in names:
                    nxt = _open(name, _DIR, dir_fd=fd)
                    os.close(fd)
                    fd = nxt
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd
        else:
            d = root
            for name in names:
                d = d / name
                if not stat.S_ISDIR(os.lstat(d).st_mode):
                    raise NotADirectoryError(f"not a real directory: {name}")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _name(self, name: str) -> str:
        return name if self.fd is not None else str(self.path / name)

    def open_regular(self, name: str) -> tuple[int, os.stat_result]:
        """A read descriptor for ``name`` and its ``fstat``; ``OSError`` if it is anything but
        a regular file with at most two links, or not the file the ``lstat`` saw."""
        seen = os.stat(self._name(name), dir_fd=self.fd, follow_symlinks=False)
        fd = _open(self._name(name), _READ, dir_fd=self.fd)
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(seen.st_mode)
                or not stat.S_ISREG(st.st_mode)
                or st.st_nlink > MAX_LINKS
                or (st.st_dev, st.st_ino) != (seen.st_dev, seen.st_ino)
            ):
                raise FileNotFoundError(f"not a plain file: {name}")
        except BaseException:
            os.close(fd)
            raise
        return fd, st

    def create(self, name: str) -> int:
        """A new file for writing; fails if anything, a link included, has that name."""
        return _open(self._name(name), _NEW, 0o600, dir_fd=self.fd)

    def replace(self, src: str, dst: str) -> None:
        os.replace(self._name(src), self._name(dst), src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def unlink(self, name: str) -> None:
        try:
            os.unlink(self._name(name), dir_fd=self.fd)
        except FileNotFoundError:
            pass


class OpenFileResponse(FileResponse):
    """``FileResponse`` that reads an already opened and checked file instead of opening
    ``path`` again by name. ``Range`` works for one range; several ranges get the whole file
    (``200``), which RFC 9110 section 14.2 allows."""

    def __init__(self, fd: int, st: os.stat_result, **kwargs) -> None:
        self.file: BinaryIO = os.fdopen(fd, "rb")
        super().__init__("", stat_result=st, **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.file.close()

    async def _read(self, start: int, size: int) -> bytes:
        def read() -> bytes:
            self.file.seek(start)
            return self.file.read(size)

        return await anyio.to_thread.run_sync(read)

    async def _send_body(self, send: Send, start: int, end: int) -> None:
        more = start < end
        if not more:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        while more:
            chunk = await self._read(start, min(self.chunk_size, end - start))
            start += len(chunk)
            more = bool(chunk) and start < end
            await send({"type": "http.response.body", "body": chunk, "more_body": more})

    async def _handle_simple(self, send: Send, send_header_only: bool, send_pathsend: bool) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        end = 0 if send_header_only else self.stat_result.st_size
        await self._send_body(send, 0, end)

    async def _handle_single_range(
        self, send: Send, start: int, end: int, file_size: int, send_header_only: bool
    ) -> None:
        headers = [
            (k, v) for k, v in self.raw_headers if k not in (b"content-range", b"content-length")
        ]
        headers += [
            (b"content-range", f"bytes {start}-{end - 1}/{file_size}".encode()),
            (b"content-length", str(end - start).encode()),
        ]
        await send({"type": "http.response.start", "status": 206, "headers": headers})
        await self._send_body(send, start, start if send_header_only else end)

    async def _handle_multiple_ranges(
        self, send: Send, ranges: list[tuple[int, int]], file_size: int, send_header_only: bool
    ) -> None:
        await self._handle_simple(send, send_header_only, False)
