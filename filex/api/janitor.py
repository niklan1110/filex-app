"""The janitor (ADR-0008): runs in the API process at startup and every 60 s.

One pass removes:

- ``/data/jobs/<id>`` whose ``job:<id>`` hash is gone (expired, deleted, lost with a Redis
  restart, or a "Delete now" that a running step raced and briefly recreated);
- ``/data/jobs/<id>`` whose ``expiresAt`` has passed, and that hash with it;
- ``in/`` and ``steps/`` of every finished job, and ``out/`` and ``result.zip`` of a job that
  did not succeed (the runner normally did this already);
- scratch dirs ``/data/tmp/<id>.<n>`` of jobs that are gone or finished;
- ids in ``ownidx:<principal>`` whose job hash is gone.

Safety: only entries named exactly like a job id (``<id>.<n>`` in ``/data/tmp``) are looked at;
links are never followed (``lstat``, ``rmtree``'s fd-based walk); at most ``MAX_REMOVALS``
paths are removed per pass. A Redis error skips the pass, so an unreachable Redis never looks
like "every job is gone".

Uploads write ``in/<n>.part`` before the job hash exists. A directory without a hash is left
alone while this process is receiving its upload (``busy``, read after the directory listing
and before Redis, so a finished upload always has its hash by then), while it holds a ``.part``
file or the ``in/.uploading`` marker written in the last ``UPLOAD_GRACE_S``, or an empty
``in/`` that new (uploads served by another uvicorn process). The marker lives from the
``mkdir`` until the job hash is written, which covers the moment after ``finish()`` renamed the
``.part`` files and before the hash exists (review L-3).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import re
import shutil
import stat
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import redis.asyncio as aioredis

from filex.jobs import JOB_ID, TERMINAL, JobPaths, job_key, valid_job_id

log = logging.getLogger("filex.janitor")

INTERVAL_S = 60
UPLOAD_GRACE_S = 600  # a .part file untouched this long belongs to a dead upload
UPLOAD_MARKER = ".uploading"  # in/ holds it from the upload's mkdir until the hash exists
MAX_ENTRIES = 20_000  # directory entries listed per pass in jobs/ and in tmp/ each
MAX_REMOVALS = 200  # paths removed per pass; the rest wait for the next pass
MAX_INDEXES = 200  # ownidx:* keys trimmed per pass
_SCRATCH = re.compile(rf"({JOB_ID.pattern})\.(0|[1-9][0-9]?)")
_CHUNK = 500


@dataclass
class Pass:
    removed: int = 0  # paths deleted under /data
    expired: int = 0  # job hashes deleted after expiresAt
    unindexed: int = 0  # ownidx entries dropped


def _entries(path: Path):
    try:
        with os.scandir(path) as it:
            yield from itertools.islice(it, MAX_ENTRIES)
    except (FileNotFoundError, NotADirectoryError):
        return


def _uploading(job_dir: Path, now: float) -> bool:
    in_dir = job_dir / "in"
    try:
        st = in_dir.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    fresh, empty = now - UPLOAD_GRACE_S, True
    dirs = [in_dir]
    for d in dirs:  # in/ and in/aux/, never through a link
        for e in itertools.islice(_entries(d), 200):
            empty = False
            if d is in_dir and e.name == "aux" and e.is_dir(follow_symlinks=False):
                dirs.append(Path(e.path))
            elif (
                (e.name.endswith(".part") or (d is in_dir and e.name == UPLOAD_MARKER))
                and e.is_file(follow_symlinks=False)
                and e.stat(follow_symlinks=False).st_mtime > fresh
            ):
                return True
    return empty and st.st_mtime > fresh


def _list(data: Path, now: float) -> tuple[dict[str, bool], list[tuple[str, str]]]:
    """Job dirs (id -> an upload may still be writing it) and scratch dirs (name, job id)."""
    jobs = {
        e.name: _uploading(Path(e.path), now)
        for e in _entries(data / "jobs")
        if valid_job_id(e.name) and e.is_dir(follow_symlinks=False)
    }
    scratch = []
    for e in _entries(data / "tmp"):
        m = _SCRATCH.fullmatch(e.name)
        if m and e.is_dir(follow_symlinks=False):
            scratch.append((e.name, m.group(1)))
    return jobs, scratch


def _remove(paths: list[Path]) -> int:
    """Delete up to ``MAX_REMOVALS`` existing paths without following links."""
    removed = 0
    for path in paths:
        if removed >= MAX_REMOVALS:
            break
        try:
            st = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(st.st_mode):
            # Links inside are unlinked, not followed (shutil.rmtree.avoids_symlink_attacks).
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        removed += 1
    return removed


def _past(expires_at: str | None, now: float) -> bool:
    try:
        t = datetime.strptime(expires_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return t.timestamp() <= now


async def _states(r: aioredis.Redis, ids: list[str]) -> dict[str, tuple[str, str | None]]:
    """``id -> (status, expiresAt)`` for every id whose job hash exists."""
    found = {}
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        async with r.pipeline(transaction=False) as p:
            for job_id in chunk:
                p.hmget(job_key(job_id), "status", "expiresAt")
            rows = await p.execute()
        for job_id, (status, expires) in zip(chunk, rows):
            if status is not None:
                found[job_id] = (status, expires)
    return found


async def _trim_indexes(r: aioredis.Redis) -> int:
    removed, seen = 0, 0
    async for key in r.scan_iter(match="ownidx:*", count=100):
        ids = await r.zrange(key, 0, -1)  # at most 1000: trimmed on every job creation
        if ids:
            async with r.pipeline(transaction=False) as p:
                for job_id in ids:
                    p.exists(job_key(job_id))
                alive = await p.execute()
            gone = [j for j, a in zip(ids, alive) if not a]
            if gone:
                removed += await r.zrem(key, *gone)
        seen += 1
        if seen >= MAX_INDEXES:
            break
    return removed


async def sweep(
    r: aioredis.Redis, data: Path, busy: Collection[str] = (), now: float | None = None
) -> Pass:
    """One janitor pass. Raises on Redis errors before anything is deleted."""
    now = time.time() if now is None else now
    jobs, scratch = await anyio.to_thread.run_sync(_list, data, now)
    held = frozenset(busy)  # after the listing, before Redis: see the module docstring
    states = await _states(r, sorted(set(jobs) | {job_id for _, job_id in scratch}))
    doomed: list[Path] = []
    expired: list[str] = []
    for job_id, uploading in jobs.items():
        paths, state = JobPaths(data, job_id), states.get(job_id)
        if state is None:
            if job_id not in held and not uploading:
                doomed.append(paths.dir)
        elif _past(state[1], now):
            expired.append(job_id)
            doomed.append(paths.dir)
        elif state[0] in TERMINAL:
            doomed += [paths.in_dir, paths.steps_dir]
            if state[0] != "SUCCEEDED":
                doomed += [paths.out_dir, paths.dir / "result.zip"]
    for name, job_id in scratch:
        state = states.get(job_id)
        if state is None or state[0] in TERMINAL or _past(state[1], now):
            doomed.append(data / "tmp" / name)
    if expired:
        await r.delete(*(job_key(j) for j in expired))
    report = Pass(expired=len(expired))
    report.removed = await anyio.to_thread.run_sync(_remove, doomed)
    report.unindexed = await _trim_indexes(r)
    return report


async def run(r: aioredis.Redis, data: Path, busy: Collection[str]) -> None:
    """A pass at startup, then one every ``INTERVAL_S``, until cancelled."""
    while True:
        try:
            done = await sweep(r, data, busy)
            if done.removed or done.expired or done.unindexed:
                log.info(
                    "janitor: %d paths removed, %d expired jobs, %d index entries dropped",
                    done.removed,
                    done.expired,
                    done.unindexed,
                )
        except Exception:  # Redis down or a disk error: nothing was guessed, try next pass
            log.warning("janitor pass skipped", exc_info=True)
        await asyncio.sleep(INTERVAL_S)
