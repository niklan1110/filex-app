"""Catalog and job routes under ``/api/v1`` (architecture section 5, spec 7.1).

The job record follows the field table in ``filex.jobs``; the API adds ``tool`` and
``createdAt``, which the runner ignores. Stream entries carry only ``job`` and ``step``.

``public_router`` holds the routes without a principal: ``GET /dl/...`` carries its own
signature (ADR-0009 "Downloads").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, get_args

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import SecretStr, ValidationError
from redis.exceptions import WatchError

from filex import planner
from filex.api import schemas
from filex.api.deps import (
    OWNER_TTL_S,
    QUEUED_CAP_S,
    ApiError,
    FoundJob,
    JobDep,
    JobIdParam,
    MaybeJobDep,
    MiB,
    PrincipalDep,
    RedisDep,
    SettingsDep,
    ToolIdParam,
    ToolsDep,
    check_auth_limit,
    count_auth_failure,
    job_not_found,
    require_principal,
)
from filex.api.janitor import UPLOAD_MARKER
from filex.api.names import content_disposition, display_name, download_name, unique
from filex.api.safe_files import JobDir, OpenFileResponse
from filex.api.upload import Upload, insufficient_storage, receive
from filex.formats import FORMATS
from filex.jobs import (
    TERMINAL,
    JobPaths,
    job_key,
    new_job_id,
    own_key,
    ownidx_key,
    queue_key,
    sec_key,
    valid_job_id,
)
from filex.registry import Files, Limits, Tool, ToolOptions

log = logging.getLogger("filex.api")

VERSION = "0.1.0"
MAX_STEPS = 65  # the runner accepts step indexes 0..64
OWNIDX_CAP = 1000  # newest job ids kept per principal for GET /jobs (older ones stay reachable)
DOWNLOAD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "Cache-Control": "no-store",
}

_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": schemas.ErrorEnvelope, "description": text}
    for status, text in [
        (401, "UNAUTHENTICATED: no valid Bearer token."),
        (403, "USER_NOT_ALLOWED: signed in, but not on the FileX allowlist."),
        (429, "TOO_MANY_REQUESTS: too many failed sign-ins from this address."),
        (503, "AUTH_UNAVAILABLE: the sign-in server cannot be reached; see Retry-After."),
    ]
}
router = APIRouter(
    prefix="/api/v1", dependencies=[Depends(require_principal)], responses=_AUTH_ERRORS
)
public_router = APIRouter(prefix="/api/v1")


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw) if raw is not None else default
    except ValueError:
        return default


# --- catalog


async def online_tools(r) -> set[str]:
    """Tool ids in live worker heartbeats (``worker:<group>:<id>``, 30 s TTL)."""
    found: set[str] = set()
    async for key in r.scan_iter(match="worker:*", count=100):
        beat = _load(await r.get(key), {})
        if isinstance(beat, dict) and isinstance(beat.get("tools"), list):
            found.update(t for t in beat["tools"] if isinstance(t, str))
    return found


def _summary(t: Tool, online: set[str]) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "summary": t.summary,
        "category": t.category,
        "group": t.group,
        "inputs": list(t.inputs),
        "outputs": list(t.outputs),
        "available": t.id in online,
        "profile": t.group,
        "keywords": list(t.keywords),
    }


def _tool(tools: dict[str, Tool], tool_id: str) -> Tool:
    tool = tools.get(tool_id)
    if tool is None:
        raise ApiError(404, "TOOL_NOT_FOUND", "There is no tool with this id.")
    return tool


@router.get("/tools", response_model=list[schemas.ToolSummary])
async def list_tools(tools: ToolsDep, r: RedisDep) -> list[dict[str, Any]]:
    online = await online_tools(r)
    return [_summary(t, online) for t in tools.values()]


@router.get("/tools/{toolId}", response_model=schemas.ToolDetail)
async def get_tool(tool_id: ToolIdParam, tools: ToolsDep, r: RedisDep) -> dict[str, Any]:
    t = _tool(tools, tool_id)
    aux = {
        s: {"title": a.title, "formats": list(a.formats), "required": a.required}
        for s, a in t.files.aux.items()
    }
    return _summary(t, await online_tools(r)) | {
        "files": {"min": t.files.min, "max": t.files.max, "each": t.files.each, "aux": aux},
        "options_schema": t.options_schema,
        "converts": [{"src": e.src, "dst": e.dst, "lossy": e.lossy} for e in t.converts],
        "target_option": t.target_option
        and (t.options.model_fields[t.target_option].alias or t.target_option),
        "warnings": list(t.warnings),
        "errors": list(t.errors),
    }


@router.get("/formats", response_model=list[schemas.Format])
def list_formats() -> list[dict[str, Any]]:
    return [
        {"id": f.id, "title": f.title, "extensions": list(f.extensions), "mime": f.mime}
        for f in FORMATS.values()
    ]


@router.get("/status", response_model=schemas.Status)
async def get_status(tools: ToolsDep, r: RedisDep) -> dict[str, Any]:
    """Worker groups with live heartbeats, their engine versions and step isolation (review L2:
    not on ``/health``). ``isolation`` is the container baseline every step has (ADR-0007); each
    group adds its seccomp and Landlock state."""
    groups = {
        t.group: {"name": t.group, "online": False, "workers": 0, "engines": {}}
        for t in tools.values()
    }
    async for key in r.scan_iter(match="worker:*", count=100):
        group = key.split(":")[1] if key.count(":") >= 2 else ""
        beat = _load(await r.get(key), None)
        if not group or not isinstance(beat, dict):
            continue
        g = groups.setdefault(group, {"name": group, "online": False, "workers": 0, "engines": {}})
        g["online"], g["workers"] = True, g["workers"] + 1
        engines = beat.get("engines")
        if isinstance(engines, dict):
            g["engines"].update(
                {k: v for k, v in engines.items() if isinstance(k, str) and isinstance(v, str)}
            )
        iso = _isolation(beat.get("isolation"))
        if g.get("isolation") is None or _strength(iso) < _strength(g["isolation"]):
            g["isolation"] = iso
    return {
        "groups": sorted(groups.values(), key=lambda g: g["name"]),
        "isolation": "container",
        "version": VERSION,
    }


def _isolation(raw: Any) -> dict[str, Any]:
    """A heartbeat's isolation, untrusted; a worker that predates it reports nothing."""
    raw = raw if isinstance(raw, dict) else {}
    landlock = raw.get("landlock")
    ok = isinstance(landlock, str) and re.fullmatch(r"abi\d{1,3}|unavailable|off", landlock)
    return {"seccomp": raw.get("seccomp") is True, "landlock": landlock if ok else "unavailable"}


def _strength(iso: dict[str, Any]) -> tuple[bool, int]:
    ll = iso["landlock"]
    return iso["seccomp"], int(ll[3:]) if ll.startswith("abi") else 0


# --- jobs


def _split_options(tool: Tool, raw: bytes | None) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate by alias only (the wire names); return (options, secrets), both by alias."""
    try:
        obj = json.loads(raw) if raw else {}
    except ValueError:
        raise ApiError(422, "INVALID_OPTIONS", "The options part is not valid JSON.") from None
    if not isinstance(obj, dict):
        raise ApiError(422, "INVALID_OPTIONS", "The options part must be a JSON object.")
    try:
        opts = tool.options.model_validate(obj, by_alias=True, by_name=False)
    except ValidationError as e:
        # No input values and no context: a rejected secret must not come back in the error.
        errors = [
            {"loc": list(err["loc"]), "message": err["msg"], "type": err["type"]}
            for err in e.errors(include_url=False, include_input=False, include_context=False)
        ]
        raise ApiError(
            422, "INVALID_OPTIONS", "Some options are not valid.", {"errors": errors}
        ) from None
    hidden = {
        name
        for name, f in type(opts).model_fields.items()
        if isinstance(f.json_schema_extra, dict) and f.json_schema_extra.get("writeOnly")
    }
    secret_values = {}
    for name in hidden:
        value = getattr(opts, name)
        if isinstance(value, SecretStr):
            field = type(opts).model_fields[name]
            secret_values[field.alias or name] = value.get_secret_value()
    return opts.model_dump(mode="json", by_alias=True, exclude=hidden), secret_values


def _target(
    tool: Tool, formats: list[str], requested: str | None, options: dict[str, Any]
) -> str | None:
    """The job's target format (ADR-0005): the requested one, else the tool's
    ``target_option`` value, else the only format every input converts to. Converters only."""
    if not tool.converts:
        if requested is not None:
            raise ApiError(422, "INVALID_TARGET", f"{tool.title} does not take a target format.")
        return None
    reachable = tool.targets(formats)
    if requested is None and tool.target_option:
        field = tool.options.model_fields[tool.target_option]
        requested = options.get(field.alias or tool.target_option)
    details = {"targets": sorted(reachable)}
    if not reachable:
        raise ApiError(
            422, "INVALID_TARGET", "These files have no common format to convert to.", details
        )
    if requested is None:
        if len(reachable) == 1:
            return next(iter(reachable))
        raise ApiError(422, "TARGET_REQUIRED", "Choose the format to convert to.", details)
    if requested not in reachable:
        shown = FORMATS[requested].title if requested in FORMATS else "that format"
        raise ApiError(
            422,
            "INVALID_TARGET",
            f"{tool.title} cannot convert these files to {shown}.",
            details | {"target": requested[:32]},
        )
    return requested


@router.post(
    "/tools/{toolId}/jobs",
    status_code=202,
    response_model=schemas.Job,
    responses={
        409: {"model": schemas.ErrorEnvelope},
        413: {"model": schemas.ErrorEnvelope},
        422: {"model": schemas.ErrorEnvelope},
        429: {
            "model": schemas.ErrorEnvelope,
            "description": "QUEUE_FULL: the tool's worker group already holds FILEX_MAX_QUEUED "
            "waiting or running steps; see Retry-After. TOO_MANY_REQUESTS: too many failed "
            "sign-ins from this address.",
        },
        507: {
            "model": schemas.ErrorEnvelope,
            "description": "INSUFFICIENT_STORAGE: the upload would leave less than "
            "FILEX_MIN_FREE_MB free on the data volume.",
        },
    },
)
async def create_job(
    tool_id: ToolIdParam,
    request: Request,
    response: Response,
    tools: ToolsDep,
    r: RedisDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Multipart: ``files`` parts, one part per auxiliary slot, an ``options`` JSON part and,
    for converters, a ``target`` format part."""
    tool = _tool(tools, tool_id)
    online = await online_tools(r)
    if tool.id not in online:
        hint = (
            "Start the core worker."
            if tool.group == "core"
            else f"Add '{tool.group}' to COMPOSE_PROFILES and restart FileX."
        )
        raise ApiError(
            409,
            "TOOL_UNAVAILABLE",
            f"{tool.title} needs the {tool.group} worker, which is not running. {hint}",
            {"profile": tool.group},
        )
    await _check_capacity(request, r, settings, tool)

    def plan(upload: Upload) -> _Planned:
        options, secret_values = _split_options(tool, upload.options)
        target = _target(tool, [f.format for f in upload.files], upload.target or None, options)
        steps = [
            {"tool": tool.id, "options": options, "target": target, "secret": bool(secret_values)}
        ]
        steps, warnings = _planned(tools, tool, options, online, steps)
        return steps, warnings, secret_values

    return await _accept(request, response, tools, r, settings, principal, tool, plan)


_Planned = tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]


async def _accept(
    request: Request,
    response: Response,
    tools: dict[str, Tool],
    r,
    settings,
    principal: str,
    tool: Tool,
    plan: Callable[[Upload], _Planned],
    queue_check: bool = False,
) -> dict[str, Any]:
    """Receive the upload for ``tool``, plan the steps (``plan``: steps, warnings, secrets) and
    store and enqueue the job. ``queue_check``: the first step's group is known only now."""
    job_id = new_job_id()  # 128-bit; every path below is built from it (task 61)
    paths = JobPaths(settings.data, job_id)
    uploading: set[str] = request.app.state.uploading
    uploading.add(job_id)  # before the directory exists, until the hash does (janitor)
    try:
        upload = await receive(request, tool, paths, settings)
        steps, warnings, secret_values = plan(upload)
        group = tools[steps[0]["tool"]].group
        if queue_check:
            await _check_queue(r, settings, group)
        now = datetime.now(UTC).replace(microsecond=0)
        record = {
            "status": "QUEUED",
            "tool": tool.id,
            "steps": json.dumps(steps),
            "inputs": json.dumps(
                [{"format": f.format, "name": f.name, "size": f.size} for f in upload.files]
            ),
            "aux": json.dumps(upload.aux),
            "progress": "0",
            "warnings": json.dumps(warnings),
            "createdAt": _iso(now),
            "expiresAt": _iso(now + timedelta(seconds=QUEUED_CAP_S)),
        }
        now_ms, idx = time.time_ns() // 1_000_000, ownidx_key(principal)
        async with r.pipeline(transaction=True) as p:
            p.hset(job_key(job_id), mapping=record)
            p.expire(job_key(job_id), QUEUED_CAP_S)
            p.set(own_key(job_id), principal, ex=OWNER_TTL_S)
            p.zadd(idx, {job_id: now_ms})
            p.zremrangebyscore(idx, "-inf", now_ms - OWNER_TTL_S * 1000)
            p.zremrangebyrank(idx, 0, -(OWNIDX_CAP + 1))
            p.expire(idx, OWNER_TTL_S)
            if secret_values:
                p.set(sec_key(job_id, 0), json.dumps(secret_values), ex=QUEUED_CAP_S)
            p.xadd(queue_key(group), {"job": job_id, "step": "0"})
            await p.execute()
        (paths.in_dir / UPLOAD_MARKER).unlink(missing_ok=True)  # the hash exists now
    except BaseException:
        # Sync on purpose: an await here would not run if the request was cancelled.
        shutil.rmtree(paths.dir, ignore_errors=True)
        raise
    finally:
        uploading.discard(job_id)
    total = sum(f.size for f in upload.files)
    created = {"job": job_id, "tool": tool.id, "principal": principal}
    log.info("job created", extra=created | {"files": len(upload.files), "bytes": total})
    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    response.headers["Cache-Control"] = "no-store"
    return job_view(job_id, record, tools)


def _planned(
    tools: dict[str, Tool],
    tool: Tool,
    options: dict[str, Any],
    online: set[str],
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A static multi-step tool's plan (ADR-0005 amendment): the steps after its own and the
    job's warnings, fixed now. A plan naming an unknown tool, invalid options or an undeclared
    warning code is a bug in the tool, so it fails the request (500), never the job later."""
    if tool.plan is None:
        return steps, []
    plan = tool.plan(tool.options.model_validate(options, by_alias=True), frozenset(online))
    for follow in plan.steps:
        nxt = tools[follow.tool]
        opts = nxt.options.model_validate(dict(follow.options), by_alias=True, by_name=False)
        steps.append(
            {
                "tool": nxt.id,
                "options": opts.model_dump(mode="json", by_alias=True),
                "target": None,
                "secret": False,
            }
        )
    warnings = []
    for code, message in plan.warnings:
        if code not in tool.warnings:
            raise ValueError(f"{tool.id}: plan warning {code!r} is not declared")
        warnings.append({"code": code, "message": message[:300], "step": 0})
    return steps, warnings


# --- convert (ADR-0005): routes over the converters' edges, among the tools online now

CONVERT_MAX_FILES = 50
_FORMAT_ID = Query(max_length=32)


def convert_tool(tools: dict[str, Tool]) -> Tool:
    """The built-in ``convert`` tool as the upload sees it: every converter source format, no
    options, no aux files. It is never run: a job's steps are its route's hops."""
    converters = [t for t in tools.values() if t.converts]
    return Tool(
        id="convert",
        title="Convert",
        summary="Convert files to another format; FileX picks the route, across worker groups.",
        category="convert",
        group="core",
        inputs=tuple(sorted({e.src for t in converters for e in t.converts})),
        outputs=tuple(sorted({e.dst for t in converters for e in t.converts})),
        converts=(),
        files=Files(min=1, max=CONVERT_MAX_FILES),
        options=ToolOptions,
        engines=(),
        limits=Limits(),
        examples=(),
        warnings=tuple(sorted({"LOSSY_STEP", *(w for t in converters for w in t.warnings)})),
        errors=tuple(sorted({e for t in converters for e in t.errors})),
        fn=lambda *a: None,
        module=__name__,
    )


def _profile_hint(groups: list[str]) -> str:
    hints = ["Start the core worker."] if "core" in groups else []
    extra = [g for g in groups if g != "core"]
    if extra:
        names = ", ".join(f"'{g}'" for g in extra)
        hints.append(f"Add {names} to COMPOSE_PROFILES and restart FileX.")
    return " ".join(hints)


def _shown(fmt: str) -> str:
    return FORMATS[fmt].title if fmt in FORMATS else fmt


def convert_route(
    tools: dict[str, Tool], sources: list[str], target: str, online: set[str], files: int = 1
) -> planner.Route:
    """The route ``plan`` and the job use, or the 422 that explains why there is none."""
    sources = sorted(set(sources))
    unknown = [f[:32] for f in [*sources, target] if f not in FORMATS]
    if unknown:
        raise ApiError(
            422, "UNKNOWN_FORMAT", "There is no format with this id.", {"formats": unknown}
        )
    details: dict[str, Any] = {"sources": sources, "target": target, "profiles": []}
    if target in sources:
        some = "These files are" if sources == [target] else "Some of these files are"
        raise ApiError(422, "NO_ROUTE", f"{some} already {_shown(target)}.", details)
    found = planner.route(tools.values(), sources, target, online, files)
    if found is not None:
        return found
    one = planner.route(tools.values(), sources, target, online) if files > 1 else None
    if one is not None:
        most = min(tools[h.tool].files.max for h in one.hops)
        raise ApiError(
            422,
            "TOO_MANY_FILES",
            f"Convert at most {most} of these files at once.",
            details | {"max": most},
        )
    every = planner.route(tools.values(), sources, target, None, files)
    shown = ", ".join(_shown(s) for s in sources)
    message = f"FileX cannot convert {shown} to {_shown(target)}."
    if every is not None:
        details["profiles"] = sorted({h.group for h in every.hops if h.tool not in online})
        message += " " + _profile_hint(details["profiles"])
    raise ApiError(422, "NO_ROUTE", message, details)


def _route_steps(tools: dict[str, Tool], route: planner.Route) -> list[dict[str, Any]]:
    return [
        {
            "index": n,
            "tool": h.tool,
            "title": tools[h.tool].title,
            "group": h.group,
            "src": list(h.src),
            "dst": h.dst,
            "lossy": h.lossy,
        }
        for n, h in enumerate(route.hops)
    ]


def _lossy_warnings(tools: dict[str, Tool], route: planner.Route) -> list[dict[str, Any]]:
    return [
        {
            "code": "LOSSY_STEP",
            "message": f"Step {n + 1} ({tools[h.tool].title}, "
            f"{' and '.join(_shown(s) for s in h.src)} to {_shown(h.dst)}) may lose "
            "formatting, layout or quality.",
            "step": n,
        }
        for n, h in enumerate(route.hops)
        if h.lossy
    ]


_CONVERT_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "model": schemas.ErrorEnvelope,
        "description": "NO_ROUTE: no route among the workers online now; ``details.profiles`` "
        "names the worker groups (compose profiles) that would create one (empty: none would). "
        "TOO_MANY_FILES: a step takes fewer files (``details.max``). UNKNOWN_FORMAT.",
    }
}


@router.get("/convert/plan", response_model=schemas.ConvertPlan, responses=_CONVERT_ERRORS)
async def plan_conversion(
    tools: ToolsDep,
    r: RedisDep,
    sources: Annotated[
        list[str],
        Query(
            alias="from",
            min_length=1,
            max_length=10,
            description="Input format id; repeat it for mixed files (all go one route).",
        ),
    ],
    to: Annotated[str, Query(max_length=32, description="Target format id.")],
    files: Annotated[int, Query(ge=1, le=CONVERT_MAX_FILES, description="Number of files.")] = 1,
) -> dict[str, Any]:
    """Preview the route ``POST /convert/jobs`` would take now (ADR-0005)."""
    found = convert_route(tools, sources, to, await online_tools(r), files)
    return {
        "sources": sorted(set(sources)),
        "target": to,
        "cost": found.cost,
        "lossy": any(h.lossy for h in found.hops),
        "steps": _route_steps(tools, found),
        "warnings": _lossy_warnings(tools, found),
    }


@router.get("/convert/targets", response_model=list[schemas.ConvertSource])
async def conversion_targets(
    tools: ToolsDep,
    r: RedisDep,
    sources: Annotated[
        list[str] | None,
        Query(alias="from", max_length=50, description="Only these source formats."),
    ] = None,
) -> list[dict[str, Any]]:
    """Every format reachable now from each source format, for the from/to pickers and the
    "X to Y" tiles. One file per job; sources with no reachable target are left out."""
    online = await online_tools(r)
    wanted = sorted(set(sources or convert_tool(tools).inputs) & set(FORMATS))
    out = []
    for src in wanted:
        found = planner.routes_from(tools.values(), [src], online)
        if found:
            targets = [
                {
                    "format": dst,
                    "steps": len(rt.hops),
                    "lossy": any(h.lossy for h in rt.hops),
                    "groups": rt.groups,
                }
                for dst, rt in sorted(found.items())
            ]
            out.append({"src": src, "targets": targets})
    return out


@router.post(
    "/convert/jobs",
    status_code=202,
    response_model=schemas.Job,
    responses={
        413: {"model": schemas.ErrorEnvelope},
        422: {"model": schemas.ErrorEnvelope},
        429: {"model": schemas.ErrorEnvelope, "description": "QUEUE_FULL (first step's group)."},
        507: {"model": schemas.ErrorEnvelope},
    },
)
async def create_convert_job(
    request: Request,
    response: Response,
    tools: ToolsDep,
    r: RedisDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Multipart: ``files`` parts and a ``target`` format part. The route is planned now among
    the workers online and stored as the job's steps, one converter per step with default
    options; a ``LOSSY_STEP`` warning per lossy step (ADR-0005)."""
    tool = convert_tool(tools)
    online = await online_tools(r)
    await _check_disk(request, settings)

    def plan(upload: Upload) -> _Planned:
        _split_options(tool, upload.options)  # no options: only {} is accepted
        if not upload.target:
            raise ApiError(422, "TARGET_REQUIRED", "Choose the format to convert to.")
        found = convert_route(
            tools, [f.format for f in upload.files], upload.target, online, len(upload.files)
        )
        steps = [
            {
                "tool": h.tool,
                "options": tools[h.tool].options().model_dump(mode="json", by_alias=True),
                "target": h.dst,
                "secret": False,
            }
            for h in found.hops
        ]
        return steps, _lossy_warnings(tools, found), {}

    return await _accept(
        request, response, tools, r, settings, principal, tool, plan, queue_check=True
    )


RETRY_QUEUE_FULL_S = 30


async def _check_queue(r, settings, group: str) -> None:
    if settings.max_queued and await r.xlen(queue_key(group)) >= settings.max_queued:
        raise ApiError(
            429,
            "QUEUE_FULL",
            f"FileX is busy: the {group} queue is full. Try again in a minute.",
            {"group": group, "limit": settings.max_queued},
            headers={"Retry-After": str(RETRY_QUEUE_FULL_S)},
        )


def _free_bytes(data: Path) -> int:
    data.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(data).free


async def _check_capacity(request: Request, r, settings, tool: Tool) -> None:
    """Back-pressure before any byte is read (ADR-0001, ADR-0008): ``429 QUEUE_FULL`` when the
    group's stream holds ``FILEX_MAX_QUEUED`` entries (acked entries are deleted, so ``XLEN`` is
    waiting + running), ``507`` when the declared ``Content-Length`` would leave less than
    ``FILEX_MIN_FREE_MB`` free on ``/data``."""
    await _check_queue(r, settings, tool.group)
    await _check_disk(request, settings)


async def _check_disk(request: Request, settings) -> None:
    if settings.min_free_mb:
        length = request.headers.get("content-length", "")
        declared = int(length) if length.isdigit() else 0
        free = await anyio.to_thread.run_sync(_free_bytes, settings.data)
        if free - declared < settings.min_free_mb * MiB:
            raise insufficient_storage()  # checked again every 64 MB while the bytes arrive


# Job hash fields the worker writes (status, step:<n>, progress, outputs, warnings, error) are
# untrusted (review L-6): anything off-contract is dropped or replaced so a view never answers 500.
_STATUSES = frozenset(get_args(schemas.JobStatus))


def _number(v: Any) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


def _status(v: Any, default: str) -> str:
    return v if v in _STATUSES else default


def _valid(model: type[schemas.ApiModel], items: Any) -> list[dict]:
    if not isinstance(items, list):
        return []
    kept = []
    for item in items:
        try:
            model.model_validate(item)
        except ValidationError:
            continue
        kept.append(item)
    return kept


def _inputs(h: dict[str, str]) -> list[dict]:
    return _valid(schemas.JobInput, _load(h.get("inputs"), []))


def valid_outputs(raw: str | None) -> list[dict]:
    """The runner writes ``index`` = position and a known format. If any entry is off, none is
    served: dropping one would shift the positions that ``/outputs/{n}`` and links use."""
    outputs = _load(raw, [])
    if not isinstance(outputs, list):
        return []
    for n, o in enumerate(outputs):
        ok = (
            isinstance(o, dict)
            and type(o.get("index")) is int
            and o["index"] == n
            and o.get("format") in FORMATS
            and type(o.get("size")) is int
            and o.get("role") in ("RESULT", "REPORT")
        )
        if not ok:
            log.warning("job outputs are malformed; serving none")
            return []
    return outputs


def _output_names(job_id: str, inputs: list[dict], outputs: list[dict]) -> list[str]:
    """Output i is named as the tool named it, else after input i (per-file tools) or input 0,
    with its own extension. Names from Redis are cleaned again: it is not trusted."""
    names = []
    for i, o in enumerate(outputs):
        given = display_name(o["name"]) if isinstance(o.get("name"), str) else ""
        if not given:
            src = inputs[i] if i < len(inputs) else (inputs[0] if inputs else {})
            given = str(src.get("name", ""))
        names.append(download_name(given, o["format"], job_id))
    return unique(names)


def job_view(job_id: str, h: dict[str, str], tools: dict[str, Tool]) -> dict[str, Any]:
    steps = [s for s in _load(h.get("steps"), []) if isinstance(s, dict)]
    inputs = _inputs(h)
    outputs = valid_outputs(h.get("outputs"))
    view_steps = []
    for n, spec in enumerate(steps):
        state = _load(h.get(f"step:{n}"), {})
        state = state if isinstance(state, dict) else {}
        tool_id = spec.get("tool") if isinstance(spec.get("tool"), str) else ""
        tool = tools.get(tool_id)
        message = state.get("message")
        view_steps.append(
            {
                "index": n,
                "tool": tool_id,
                "group": tool.group if tool else "",
                "status": _status(state.get("status"), "QUEUED"),
                "progress": _number(state.get("progress")),
                "message": message if isinstance(message, str) else "",
            }
        )
    names = _output_names(job_id, inputs, outputs)
    error = _valid(schemas.JobError, [_load(h.get("error"), None)])
    return {
        "id": job_id,
        "tool": h.get("tool", view_steps[0]["tool"] if view_steps else ""),
        "status": _status(h.get("status"), "FAILED"),
        "progress": _number(_load(h.get("progress"), 0)),
        "created_at": h.get("createdAt"),
        "finished_at": h.get("finishedAt"),
        "expires_at": h.get("expiresAt"),
        "inputs": [
            {"name": i.get("name", ""), "format": i["format"], "size": i.get("size", 0)}
            for i in inputs
        ],
        "steps": view_steps,
        "warnings": _valid(schemas.JobWarning, _load(h.get("warnings"), [])),
        "outputs": [o | {"name": name} for o, name in zip(outputs, names)],
        "error": error[0] if error else None,
    }


@router.get("/jobs", response_model=schemas.JobList)
async def list_jobs(
    response: Response,
    principal: PrincipalDep,
    r: RedisDep,
    tools: ToolsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[
        int | None, Query(ge=0, description="``nextBefore`` of the previous page.")
    ] = None,
) -> dict[str, Any]:
    """The caller's own jobs, newest first. There is no list across principals (review H7).

    Reads ``ownidx:<principal>``; ids whose job hash has expired are dropped from the index.
    """
    idx = ownidx_key(principal)
    top = f"({before}" if before is not None else "+inf"
    items: list[dict[str, Any]] = []
    gone: list[str] = []
    offset, last = 0, None
    while len(items) < limit:
        batch = await r.zrevrangebyscore(
            idx, top, "-inf", start=offset, num=limit - len(items), withscores=True
        )
        if not batch:
            break
        offset += len(batch)
        async with r.pipeline(transaction=False) as p:
            for job_id, _ in batch:
                p.hgetall(job_key(job_id))
                p.get(own_key(job_id))
            found = await p.execute()
        for (job_id, score), record, owner in zip(batch, found[::2], found[1::2]):
            if record and owner == principal:
                items.append(job_view(job_id, record, tools))
                last = int(score)
            else:
                gone.append(job_id)
    if gone:
        await r.zrem(idx, *gone)
    response.headers["Cache-Control"] = "no-store"
    return {"items": items, "next_before": last if len(items) == limit else None}


@router.get(
    "/jobs/{jobId}",
    response_model=schemas.Job,
    responses={304: {"description": "Not modified"}, 404: {"model": schemas.ErrorEnvelope}},
)
async def get_job(job: JobDep, request: Request, tools: ToolsDep) -> Response:
    body = schemas.Job.model_validate(job_view(job.id, job.record, tools))
    content = body.model_dump_json(by_alias=True).encode()
    etag = '"' + hashlib.sha256(content).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    sent = request.headers.get("if-none-match", "")
    if etag in (t.strip().removeprefix("W/") for t in sent.split(",")):
        return Response(status_code=304, headers=headers)
    return Response(content, media_type="application/json", headers=headers)


@router.delete("/jobs/{jobId}", status_code=204, response_class=Response)
async def delete_job(job: MaybeJobDep, r: RedisDep, principal: PrincipalDep) -> Response:
    """Cancel, or delete now. Idempotent: a job that is already gone is ``204`` too.

    - Queued or running: the job is marked ``CANCELLED``; the runner kills a running step
      within a second and owns the cleanup of its ``in/``, ``steps/``, ``out/`` and ``sec:*``
      keys when it next sees the job. A queued job that no worker ever picks up keeps its
      24 h hash TTL; once the hash expires the janitor removes its directory.
    - Finished (``SUCCEEDED``/``FAILED``/``CANCELLED``): no worker touches it again, so the
      API deletes the hash and the directory itself ("Delete now", review M7 fix 1).
    Secrets of steps that never started are deleted either way. Another principal's job
    answers ``204`` and is left alone, exactly like a job that does not exist.
    """
    if job is None:
        return Response(status_code=204)
    key, secrets_keys = job_key(job.id), [sec_key(job.id, n) for n in range(MAX_STEPS)]
    async with r.pipeline(transaction=True) as p:
        while True:
            try:
                await p.watch(key)
                status = await p.hget(key, "status")
                p.multi()
                if status is not None and status not in TERMINAL:
                    now = _iso(datetime.now(UTC).replace(microsecond=0))
                    p.hset(key, mapping={"status": "CANCELLED", "finishedAt": now})
                    p.delete(*secrets_keys)
                else:
                    p.delete(key, own_key(job.id), *secrets_keys)
                    p.zrem(ownidx_key(principal), job.id)
                await p.execute()
                break
            except WatchError:
                continue
    if status is None or status in TERMINAL:
        await anyio.to_thread.run_sync(shutil.rmtree, job.paths.dir, True)
        log.info("job deleted", extra={"job": job.id, "principal": principal})
    else:
        log.info("job cancelled", extra={"job": job.id, "principal": principal})
    return Response(status_code=204)


# --- downloads: Bearer routes for scripts, signed links for the browser (ADR-0009, task 59)

DL_TTL_S = 300  # a link lives at most 5 minutes
_DL_TARGET = re.compile(r"result|0|[1-9][0-9]{0,4}")
_DL_EXP = re.compile(r"[1-9][0-9]{0,11}")
_DL_SIG = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url of 32 bytes, no padding
_JOB_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": schemas.ErrorEnvelope, "description": "JOB_NOT_FOUND or OUTPUT_NOT_FOUND."},
    409: {"model": schemas.ErrorEnvelope, "description": "JOB_NOT_READY: no result yet."},
}


def _finished_outputs(job: FoundJob) -> list[dict]:
    if job.record.get("status") != "SUCCEEDED":
        raise ApiError(409, "JOB_NOT_READY", "The job has no result yet.")
    return valid_outputs(job.record.get("outputs"))


def _gone() -> ApiError:
    return ApiError(404, "OUTPUT_NOT_FOUND", "This output is gone.")


def _open(job: FoundJob, *names: str) -> tuple[int, os.stat_result]:
    """``names`` below the job dir, opened without following links (review H-1)."""
    try:
        with JobDir(job.paths.root, "jobs", job.id, *names[:-1]) as d:
            return d.open_regular(names[-1])
    except OSError:
        raise _gone() from None


def _download(opened: tuple[int, os.stat_result], name: str, fmt: str, job_id: str) -> FileResponse:
    """The same attachment headers on every route that serves output bytes (review C2, M5)."""
    headers = DOWNLOAD_HEADERS | {"Content-Disposition": content_disposition(name, job_id)}
    fd, st = opened
    return OpenFileResponse(fd, st, media_type=FORMATS[fmt].download_mime, headers=headers)


def _build_zip(job: FoundJob, members: list[tuple[dict, str]]) -> None:
    """``result.zip`` from descriptors opened like downloads; written through the job dir's
    descriptor so a swapped-in link is never written through (review H-1)."""
    tmp = f".result.zip.{secrets.token_hex(4)}"
    with (
        JobDir(job.paths.root, "jobs", job.id) as jd,
        JobDir(job.paths.root, "jobs", job.id, "out") as od,
    ):
        try:
            with os.fdopen(jd.create(tmp), "wb") as raw, zipfile.ZipFile(raw, "w") as zf:
                for o, name in members:
                    fd, st = od.open_regular(job.paths.output(o["index"], o["format"]).name)
                    with os.fdopen(fd, "rb") as src:
                        info = zipfile.ZipInfo(name, time.localtime(st.st_mtime)[:6])
                        info.file_size = st.st_size
                        with zf.open(info, "w") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
            jd.replace(tmp, "result.zip")  # concurrent builders race harmlessly: same content
        finally:
            jd.unlink(tmp)


def _results(job: FoundJob) -> list[tuple[dict, str]]:
    outputs = _finished_outputs(job)
    names = _output_names(job.id, _inputs(job.record), outputs)
    results = [(o, n) for o, n in zip(outputs, names) if o.get("role") == "RESULT"]
    if not results:
        raise ApiError(404, "OUTPUT_NOT_FOUND", "This job has no result.")
    return results


def _output(job: FoundJob, index: int) -> tuple[dict, str]:
    outputs = _finished_outputs(job)
    if index >= len(outputs):
        raise ApiError(404, "OUTPUT_NOT_FOUND", "This job has no output with that number.")
    return outputs[index], _output_names(job.id, _inputs(job.record), outputs)[index]


def _output_file(job: FoundJob, o: dict) -> tuple[int, os.stat_result]:
    return _open(job, "out", job.paths.output(o["index"], o["format"]).name)


async def _result_response(job: FoundJob) -> FileResponse:
    results = _results(job)
    if len(results) == 1:
        o, name = results[0]
        return _download(_output_file(job, o), name, o["format"], job.id)
    for o, _ in results:  # every member must still be a plain file before a zip is offered
        os.close(_output_file(job, o)[0])
    try:
        opened = _open(job, "result.zip")
    except ApiError:
        try:
            await anyio.to_thread.run_sync(_build_zip, job, results)
        except OSError:
            raise _gone() from None
        opened = _open(job, "result.zip")
    first = _inputs(job.record)[:1] or [{}]
    name = download_name(str(first[0].get("name", "")), "zip", job.id)
    return _download(opened, name, "zip", job.id)


def _output_response(job: FoundJob, index: int) -> FileResponse:
    o, name = _output(job, index)
    return _download(_output_file(job, o), name, o["format"], job.id)


@router.get("/jobs/{jobId}/result", response_class=FileResponse, responses=_JOB_ERRORS)
async def get_result(job: JobDep) -> FileResponse:
    """The single ``RESULT`` output, or a zip of all of them (reports: ``/outputs/{n}``)."""
    return await _result_response(job)


@router.get("/jobs/{jobId}/outputs/{index}", response_class=FileResponse, responses=_JOB_ERRORS)
async def get_output(job: JobDep, index: Annotated[int, PathParam(ge=0)]) -> FileResponse:
    return _output_response(job, index)


def dl_signature(key: bytes, job_id: str, target: str, exp: int | str) -> str:
    mac = hmac.new(key, f"dl\n{job_id}\n{target}\n{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def _link(request: Request, response: Response, job_id: str, target: str) -> dict[str, str]:
    exp = int(time.time()) + DL_TTL_S
    sig = dl_signature(request.app.state.dl_key, job_id, target, exp)
    response.headers["Cache-Control"] = "no-store"
    return {
        "url": f"/api/v1/dl/{job_id}/{target}?exp={exp}&sig={sig}",
        "expires_at": _iso(datetime.fromtimestamp(exp, UTC)),
    }


@router.post(
    "/jobs/{jobId}/result/link", response_model=schemas.DownloadLink, responses=_JOB_ERRORS
)
async def link_result(job: JobDep, request: Request, response: Response) -> dict[str, str]:
    """A 5-minute link to ``/result`` that needs no header, for browser downloads (A8)."""
    _results(job)
    return _link(request, response, job.id, "result")


@router.post(
    "/jobs/{jobId}/outputs/{index}/link",
    response_model=schemas.DownloadLink,
    responses=_JOB_ERRORS,
)
async def link_output(
    job: JobDep, index: Annotated[int, PathParam(ge=0)], request: Request, response: Response
) -> dict[str, str]:
    """A 5-minute link to ``/outputs/{index}`` that needs no header (A8)."""
    _output(job, index)
    return _link(request, response, job.id, str(index))


async def _redeem(
    request: Request, r, settings, job_id: str, target: str, exp: str, sig: str
) -> FoundJob:
    """Review 5.1 order: formats and expiry, then the HMAC, and only then Redis."""
    if not (valid_job_id(job_id) and _DL_TARGET.fullmatch(target) and _DL_EXP.fullmatch(exp)):
        raise job_not_found()
    now = int(time.time())
    if not now < int(exp) <= now + DL_TTL_S or not _DL_SIG.fullmatch(sig):
        raise job_not_found()
    expected = dl_signature(request.app.state.dl_key, job_id, target, exp)
    if not hmac.compare_digest(expected.encode(), sig.encode()):
        raise job_not_found()
    async with r.pipeline(transaction=False) as p:
        p.hgetall(job_key(job_id))
        p.exists(own_key(job_id))
        record, owned = await p.execute()
    if not record or not owned:
        raise job_not_found()
    return FoundJob(job_id, record, JobPaths(settings.data, job_id))


@public_router.get(
    "/dl/{jobId}/{target}",
    response_class=FileResponse,
    responses={
        404: {"model": schemas.ErrorEnvelope, "description": "JOB_NOT_FOUND, for every failure."},
        429: {"model": schemas.ErrorEnvelope, "description": "TOO_MANY_REQUESTS."},
    },
)
async def signed_download(
    request: Request,
    r: RedisDep,
    settings: SettingsDep,
    job_id: JobIdParam,
    target: Annotated[str, PathParam(description="``result`` or an output index.")],
    exp: Annotated[str, Query(description="Unix time the link expires.")] = "",
    sig: Annotated[str, Query(description="base64url HMAC-SHA256 of the link.")] = "",
) -> FileResponse:
    """Redeem a link from ``POST .../link``: no ``Authorization`` header, ``Range`` works.

    Every failure (bad id, target or ``exp``, bad signature, gone job or output) is the same
    ``404`` and counts toward the per-IP ``rl:dl`` limit (not ``rl:auth``, review M-2).
    """
    key = await check_auth_limit(request, r, "dl")
    try:
        job = await _redeem(request, r, settings, job_id, target, exp, sig)
        if target == "result":
            return await _result_response(job)
        return _output_response(job, int(target))
    except ApiError:
        await count_auth_failure(r, key)
        raise job_not_found() from None
