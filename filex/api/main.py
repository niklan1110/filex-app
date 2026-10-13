"""The FileX API: ``uvicorn filex.api.main:app`` (architecture section 5).

Every ``/api/v1`` route except ``/health``, ``/config``, ``DELETE /session`` and the signed
``/dl/...`` goes through ``require_principal`` (ADR-0009). Errors always use the envelope
``{"error": {"code", "message", "details"}}``. Unsafe auth settings stop the API at startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import PurePath
from urllib.parse import urlsplit

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from filex import logs
from filex.api import contract, janitor, schemas
from filex.api.auth import Authenticator, ConfigError
from filex.api.deps import ApiError, MiB, Settings
from filex.api.routes import VERSION, public_router, router
from filex.registry import Tool, discover

log = logging.getLogger("filex.api")

_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
_AUTH_MODES = {"keel": "KEEL", "token": "TOKEN", "none": "NONE"}
DL_KEY_BYTES = 32


class DropQueryString(logging.Filter):
    """Uvicorn's access log writes the full URL; signed links carry ``sig`` in it (review 5.1).
    Keeps only the path, on every route."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def drop_query_strings_from_access_log() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropQueryString) for f in access.filters):
        access.addFilter(DropQueryString())


def shell_headers(settings: Settings, auth: Authenticator) -> dict[str, str]:
    """Headers on the UI (review C2 fix 2): the strict CSP, built at startup from ``KEEL_URL``
    (its origin only), no referrer, no framing."""
    connect = "'self'"
    if auth.mode == "keel":
        u = urlsplit(auth.keel_url)
        connect += f" {u.scheme}://{u.netloc.lower()}"
    csp = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
        f"connect-src {connect}; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    )
    headers = {
        "Content-Security-Policy": csp,
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    }
    if settings.hsts:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


def download_key(settings: Settings) -> bytes:
    """32 random bytes in memory (one uvicorn process), or a Docker secret file when the API
    runs more processes, never Redis or ``.env`` (ADR-0009 Downloads, review 5.1)."""
    if settings.dl_key_file is None:
        if settings.api_workers > 1:
            raise ConfigError(
                "The API runs more than one uvicorn worker: set FILEX_DL_KEY_FILE to a secret "
                "file with at least 32 random bytes so download links work in every worker."
            )
        return secrets.token_bytes(DL_KEY_BYTES)
    try:
        # Only a trailing newline is stripped: a binary key may end in any byte (review L-7).
        key = settings.dl_key_file.read_bytes().removesuffix(b"\n").removesuffix(b"\r")
    except OSError as e:
        raise ConfigError(f"FILEX_DL_KEY_FILE cannot be read: {e.strerror}.") from None
    if len(key) < DL_KEY_BYTES:
        raise ConfigError("FILEX_DL_KEY_FILE must hold at least 32 random bytes.")
    return key


def _error(status: int, code: str, message: str, details: dict | None = None, headers=None):
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(body, status_code=status, headers=headers)


class LocalModeSameSite:
    """``FILEX_AUTH=none`` only (ADR-0011): the browser attaches no credential, so any website
    the user visits could post jobs to ``http://127.0.0.1:8080/api`` blind (CSRF). Refuses
    ``/api`` requests that a browser marks as cross-site, or whose ``Origin`` is not one of
    ``FILEX_ALLOWED_HOSTS``. Scripts send neither header and pass."""

    def __init__(self, app, allowed_hosts: tuple[str, ...]) -> None:
        self.app = app
        # The Host check needs "[::1]"; an Origin's hostname is "::1".
        self.hosts = {h.lower().removeprefix("[").removesuffix("]") for h in allowed_hosts}

    def _cross_site(self, headers: Headers) -> bool:
        if headers.get("sec-fetch-site") == "cross-site":
            return True
        origin = headers.get("origin")
        if origin is None:
            return False
        try:
            return urlsplit(origin).hostname not in self.hosts  # "null" has no hostname
        except ValueError:  # e.g. an unclosed "[" in an IPv6 origin
            return True

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"].startswith("/api/")
            and self._cross_site(Headers(scope=scope))
        ):
            msg = "FileX in local mode accepts requests only from its own page."
            await _error(403, "CROSS_SITE_REQUEST", msg)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class SpaFiles(StaticFiles):
    """The built UI, with the app-shell headers. Client-side routes (``/tools/pdf.merge``) get
    ``index.html``; unknown ``/api``, ``/assets`` and ``/fonts`` paths stay ``404``."""

    def __init__(self, *, headers: dict[str, str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.headers = headers

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            parts = PurePath(path).parts
            if exc.status_code != 404 or (parts and parts[0] in ("api", "assets", "fonts")):
                raise
            response = await super().get_response("index.html", scope)
        response.headers.update(self.headers)
        return response


def create_app(
    settings: Settings | None = None,
    redis: aioredis.Redis | None = None,
    tools: dict[str, Tool] | None = None,
    http: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Raises ``ConfigError`` when no sign-in is configured or a setting is unsafe."""
    settings = settings or Settings.from_env()
    auth = Authenticator.from_settings(settings, http)
    dl_key = download_key(settings)
    drop_query_strings_from_access_log()
    if auth.mode == "none":
        log.warning(
            "FILEX_AUTH=none: FileX runs WITHOUT sign-in. Anyone who can reach it can use it."
        )
    elif auth.mode == "keel" and not settings.allowed_user_ids:
        log.info(
            "FILEX_ALLOWED_USER_IDS is empty: only Keel users with app_metadata.filex can sign in."
        )
    own_redis = redis is None
    if redis is None:
        redis = aioredis.Redis.from_url(
            settings.redis_url,
            username=settings.redis_user,
            password=settings.redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
            health_check_interval=30,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        drop_query_strings_from_access_log()  # again, in case logging was configured since
        sweeper = asyncio.create_task(janitor.run(redis, settings.data, app.state.uploading))
        yield
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await auth.aclose()
        if own_redis:
            await redis.aclose()

    # No /docs or /redoc (review M-1): they load Swagger UI / ReDoc script from a CDN onto this
    # origin, where it could read the Keel session. The contract stays at /openapi.json.
    app = FastAPI(
        title="FileX",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )
    app.state.settings = settings
    app.state.dl_key = dl_key
    app.state.redis = redis
    app.state.auth = auth
    app.state.tools = tools if tools is not None else discover()
    app.state.uploading = set()  # job ids whose upload this process is receiving (janitor)
    # Blocks DNS-rebinding requests (review C1 fix 5). No CORS middleware, on purpose (M3).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    if auth.mode == "none":
        app.add_middleware(LocalModeSameSite, allowed_hosts=settings.allowed_hosts)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = ({"Cache-Control": "no-store"} if exc.status >= 400 else {}) | exc.headers
        return _error(exc.status, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return _error(422, "INVALID_REQUEST", "The request is not valid.", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unexpected error on %s %s", request.method, request.url.path)
        return _error(500, "INTERNAL_ERROR", "Something went wrong on the server.")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        # Unauthenticated: status only, never groups or engine versions (review L2, task 60).
        return {"status": "ok"}

    @app.get("/api/v1/config", response_model=schemas.Config)
    def config(response: Response) -> dict:
        """Public runtime config for the UI (ADR-0009, A9): no auth, only public values."""
        keel = auth.mode == "keel"
        response.headers["Cache-Control"] = "no-store"
        return {
            "auth_mode": _AUTH_MODES[auth.mode],
            "keel_url": auth.keel_url if keel else None,
            "keel_anon_key": auth.keel_anon_key if keel else None,
            "limits": {
                "max_upload_mb": settings.max_upload // MiB,
                "max_files": settings.max_files,
                "retention_min": settings.retention_min,
            },
            "version": VERSION,
        }

    @app.delete("/api/v1/session", status_code=204, response_class=Response)
    def end_session(request: Request) -> Response:
        """Forget the cached check of the caller's token; the UI then signs out of Keel.

        No principal needed: it only drops a cache entry, so the next request asks Keel again.
        """
        auth.evict(request.headers.get("authorization"))
        return Response(status_code=204)

    app.include_router(public_router)
    app.include_router(router)

    def openapi() -> dict:
        if app.openapi_schema is None:
            app.openapi_schema = contract.build(app, app.state.tools)
        return app.openapi_schema

    app.openapi = openapi  # one typed job route per tool (ADR-0004)
    if settings.web_dir is not None:
        web = SpaFiles(directory=settings.web_dir, html=True, headers=shell_headers(settings, auth))
        app.mount("/", web, name="web")
    return app


def __getattr__(name: str) -> FastAPI:
    # ``uvicorn filex.api.main:app`` builds the app on first access, so importing this module
    # (tests, scripts/gen_openapi.py) needs no environment.
    if name == "app":
        logs.configure(os.environ.get("FILEX_LOG_LEVEL", "INFO"))
        try:
            application = create_app()
        except ConfigError as e:
            raise SystemExit(f"FileX will not start: {e}") from None  # exit status 1
        globals()["app"] = application
        return application
    raise AttributeError(name)
