"""Settings, the error type and the dependencies every route shares.

Auth: ``require_principal`` resolves the caller through ``app.state.auth`` (``filex.api.auth``,
ADR-0009) to ``keel:<user id>``, ``token:<name>`` or, with ``FILEX_AUTH=none``, ``anonymous``.
More than 20 failed sign-ins (``401`` or ``403``) per client IP per minute answer ``429``
(``rl:auth:<ip>``). Failed ``/dl`` links count in their own bucket (``rl:dl:<ip>``), so a
resumed download of an expired link cannot lock anyone out of sign-in (review M-2).

Ownership (task 62, review H7): every job belongs to the principal that created it
(``own:<jobId>``). With ``FILEX_AUTH=none`` every caller is the one shared principal
``anonymous``, so all jobs are visible to everyone who can reach the API; that mode is for a
single trusted user only.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, Request
from fastapi import Path as PathParam

from filex.jobs import (
    JobPaths,
    job_key,
    own_key,
    redis_credentials,
    retention_minutes,
    valid_job_id,
)
from filex.registry import Tool

if TYPE_CHECKING:
    from filex.api.auth import Authenticator

MiB = 1024 * 1024
QUEUED_CAP_S = 24 * 3600  # a job that never finishes is gone after 24 h (ADR-0008)
# own:<id> and ownidx:<principal> outlive any job: 24 h queued or running + at most 24 h
# retention (FILEX_RETENTION_MIN is capped at 1440). The worker cannot extend them (ACL).
OWNER_TTL_S = QUEUED_CAP_S + 1440 * 60
AUTH_FAILS_PER_MIN = 20  # failures per client IP and bucket before 429 (review M1 fix 4)


def uvicorn_workers(argv: Sequence[str], env: Mapping[str, str]) -> int:
    """How many processes uvicorn runs, as uvicorn decides it: ``--workers`` wins, then
    ``WEB_CONCURRENCY``. Worker processes are spawned with the parent's ``sys.argv``."""
    for i, arg in enumerate(argv):
        if arg == "--workers" and i + 1 < len(argv):
            return int(argv[i + 1])
        if arg.startswith("--workers="):
            return int(arg.split("=", 1)[1])
    return int(env.get("WEB_CONCURRENCY", "1") or 1)


@dataclass(frozen=True)
class Settings:
    data: Path = Path("/data")
    redis_url: str = "redis://redis:6379/0"
    redis_user: str | None = None
    redis_password: str | None = field(default=None, repr=False)
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    auth: str = ""  # "" or "none" (run without auth on purpose)
    bind: str = "127.0.0.1"  # host address compose publishes the port on (FILEX_BIND)
    keel_url: str = ""
    keel_anon_key: str = ""
    api_tokens: str = ""  # "name:<sha256 hex>,..."
    allowed_user_ids: str = ""  # comma-separated Keel user UUIDs
    trusted_proxies: str = "127.0.0.1"  # peers whose X-Forwarded-For is believed
    max_upload: int = 1024 * MiB  # bytes per job
    max_file: int = 1024 * MiB  # bytes per file
    max_files: int = 50
    retention_min: int = 1440  # minutes outputs are kept (the runner applies it; /config shows it)
    min_free_mb: int = 4096  # 507 when an upload would leave less free space on /data (0 = off)
    max_queued: int = 20  # 429 QUEUE_FULL when a group's stream holds this many steps (0 = off)
    hsts: bool = False  # send Strict-Transport-Security (once served only over HTTPS)
    dl_key_file: Path | None = None  # secret file with the download-link key (multi-process)
    api_workers: int = 1  # uvicorn processes, from --workers / WEB_CONCURRENCY
    web_dir: Path | None = None  # the built UI, served at /

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        upload_mb = int(e.get("FILEX_MAX_UPLOAD_MB", "1024"))
        web = Path(e.get("FILEX_WEB_DIR", "web/dist"))
        redis_user, redis_password = redis_credentials(e)
        return cls(
            data=Path(e.get("FILEX_DATA", "/data")),
            redis_url=e.get("FILEX_REDIS_URL", cls.redis_url),
            redis_user=redis_user,
            redis_password=redis_password,
            allowed_hosts=tuple(
                h.strip()
                for h in e.get("FILEX_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
                if h.strip()
            ),
            auth=e.get("FILEX_AUTH", "").strip().lower(),
            bind=e.get("FILEX_BIND", "").strip() or cls.bind,
            keel_url=e.get("KEEL_URL", "").strip(),
            keel_anon_key=e.get("KEEL_ANON_KEY", "").strip(),
            api_tokens=e.get("FILEX_API_TOKENS", "").strip(),
            allowed_user_ids=e.get("FILEX_ALLOWED_USER_IDS", "").strip(),
            trusted_proxies=e.get("FILEX_TRUSTED_PROXIES", cls.trusted_proxies).strip(),
            max_upload=upload_mb * MiB,
            max_file=int(e.get("FILEX_MAX_FILE_MB", str(upload_mb))) * MiB,
            max_files=int(e.get("FILEX_MAX_FILES", "50")),
            retention_min=retention_minutes(e.get("FILEX_RETENTION_MIN", "1440")),
            min_free_mb=max(0, int(e.get("FILEX_MIN_FREE_MB", "4096"))),
            max_queued=max(0, int(e.get("FILEX_MAX_QUEUED", "20"))),
            hsts=e.get("FILEX_HSTS", "").strip().lower() == "true",
            dl_key_file=Path(e["FILEX_DL_KEY_FILE"]) if e.get("FILEX_DL_KEY_FILE") else None,
            api_workers=uvicorn_workers(sys.argv, e),
            web_dir=web if web.is_dir() else None,
        )


class ApiError(Exception):
    """Answered as ``{"error": {"code", "message", "details"}}`` (ADR-0004)."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.details = status, code, message, details or {}
        self.headers = headers or {}


def job_not_found() -> ApiError:
    return ApiError(404, "JOB_NOT_FOUND", "This job does not exist or has expired.")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


def get_tools(request: Request) -> dict[str, Tool]:
    return request.app.state.tools


SettingsDep = Annotated[Settings, Depends(get_settings)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
ToolsDep = Annotated[dict[str, Tool], Depends(get_tools)]


async def require_principal(request: Request, r: RedisDep) -> str:
    """The caller's principal, read only from ``Authorization: Bearer`` (ADR-0009)."""
    auth: Authenticator = request.app.state.auth
    if auth.mode == "none":
        return "anonymous"
    key = await check_auth_limit(request, r)
    try:
        return await auth.principal(request.headers.get("authorization"))
    except ApiError as e:
        if e.status in (401, 403):  # 403: not allowlisted; still costs a Keel call (review L-1)
            await count_auth_failure(r, key)
        raise


async def check_auth_limit(request: Request, r: aioredis.Redis, bucket: str = "auth") -> str:
    """``429`` once the client IP has 20 failures this minute in ``bucket`` (``auth`` or
    ``dl``); returns its ``rl:<bucket>:<ip>`` key."""
    auth: Authenticator = request.app.state.auth
    key = f"rl:{bucket}:{auth.client_ip(request)}"
    if int(await r.get(key) or 0) >= AUTH_FAILS_PER_MIN:
        retry = max(1, await r.ttl(key))
        raise ApiError(
            429,
            "TOO_MANY_REQUESTS",
            "Too many failed sign-in attempts. Wait a minute and try again.",
            headers={"Retry-After": str(retry)},
        )
    return key


async def count_auth_failure(r: aioredis.Redis, key: str) -> None:
    async with r.pipeline(transaction=True) as p:
        p.incr(key)
        p.expire(key, 60, nx=True)  # fixed one-minute window from the first failure
        await p.execute()


PrincipalDep = Annotated[str, Depends(require_principal)]


@dataclass(frozen=True)
class FoundJob:
    id: str
    record: dict[str, str]  # the job:<id> hash
    paths: JobPaths


# Path parameters are camelCase on the wire, like the JSON (spec 7.1).
JobIdParam = Annotated[str, PathParam(alias="jobId")]
ToolIdParam = Annotated[str, PathParam(alias="toolId")]


async def find_job(
    job_id: JobIdParam, principal: PrincipalDep, r: RedisDep, settings: SettingsDep
) -> FoundJob | None:
    """The caller's job, or None if it is gone or someone else's (the two look the same, so ids
    cannot be probed). A malformed id is 404 before Redis or /data is touched. The owner comes
    only from ``own:<id>``, never from a field of the worker-writable job hash."""
    if not valid_job_id(job_id):
        raise job_not_found()
    async with r.pipeline(transaction=False) as p:
        p.hgetall(job_key(job_id))
        p.get(own_key(job_id))
        record, owner = await p.execute()
    if not record or owner != principal:
        return None
    return FoundJob(job_id, record, JobPaths(settings.data, job_id))


async def require_job(found: Annotated[FoundJob | None, Depends(find_job)]) -> FoundJob:
    """Guards every route with ``{jobId}`` (review H7): unknown, expired or not yours is 404."""
    if found is None:
        raise job_not_found()
    return found


JobDep = Annotated[FoundJob, Depends(require_job)]
MaybeJobDep = Annotated[FoundJob | None, Depends(find_job)]
