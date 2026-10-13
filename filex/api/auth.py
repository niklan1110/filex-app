"""Who is calling: Keel access tokens and hashed ``fxt_`` tokens (ADR-0009, review C1, M1, M2).

Only ``Authorization: Bearer <value>`` is read. ``fxt_`` values are checked here against the
SHA-256 hashes in ``FILEX_API_TOKENS`` and never leave the box; anything else is asked of Keel
(``GET {KEEL_URL}/auth/v1/user``). Only a Keel ``200`` with a valid user can pass; Keel being
unreachable is ``503``, never a pass. Successes are cached by ``sha256(token)`` for at most 60 s.
Tokens are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from filex.api.deps import ApiError

if TYPE_CHECKING:
    from starlette.requests import Request

    from filex.api.deps import Settings

log = logging.getLogger("filex.api.auth")

CACHE_TTL_S = 60
CACHE_SIZE = 1000
FXT_PREFIX = "fxt_"
FXT_MIN_LEN = 47  # "fxt_" + secrets.token_urlsafe(32), which is 43 characters
ALLOWED_ROLES = ("user", "admin")  # a tuple: compared with ==, so any JSON value is safe
_TOKEN_ENTRY = re.compile(r"([a-z0-9_-]{1,32}):([0-9a-f]{64})")


class ConfigError(Exception):
    """The auth settings are unsafe or incomplete: the API must not start."""


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Sign in to use FileX.")


def unavailable() -> ApiError:
    return ApiError(
        503,
        "AUTH_UNAVAILABLE",
        "The sign-in server cannot be reached. Try again shortly.",
        headers={"Retry-After": "10"},
    )


def not_allowed() -> ApiError:
    return ApiError(403, "USER_NOT_ALLOWED", "This account is not allowed to use FileX.")


def _keel_url(raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or parts.query or parts.fragment:
        raise ConfigError("KEEL_URL must be an https URL like https://keel.example.com/project.")
    return raw.rstrip("/")


def _api_tokens(raw: str) -> dict[str, bytes]:
    tokens: dict[str, bytes] = {}
    for n, entry in enumerate((e.strip() for e in raw.split(",") if e.strip()), 1):
        m = _TOKEN_ENTRY.fullmatch(entry)
        if not m:
            # The entry itself is not echoed: a pasted token instead of its hash must not leak.
            raise ConfigError(
                f"FILEX_API_TOKENS entry {n} is not <name>:<64 lowercase hex sha256>, "
                "with a name of 1-32 characters from a-z, 0-9, _ and -."
            )
        name, digest = m.groups()
        if name in tokens:
            raise ConfigError(f"FILEX_API_TOKENS has the name {name!r} twice.")
        tokens[name] = bytes.fromhex(digest)
    return tokens


def _user_ids(raw: str) -> frozenset[str]:
    ids = set()
    for item in (i.strip() for i in raw.split(",") if i.strip()):
        try:
            ids.add(str(uuid.UUID(item)))
        except ValueError:
            raise ConfigError(
                f"FILEX_ALLOWED_USER_IDS has {item!r}, which is not a UUID."
            ) from None
    return frozenset(ids)


def _networks(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    try:
        return tuple(
            ipaddress.ip_network(p.strip(), strict=False) for p in raw.split(",") if p.strip()
        )
    except ValueError as e:
        raise ConfigError(f"FILEX_TRUSTED_PROXIES: {e}.") from None


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.removeprefix("[").removesuffix("]")).is_loopback
    except ValueError:
        return False


def _exp(token: str) -> int | None:
    """The unverified ``exp`` claim, used only to shorten the cache TTL."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = claims.get("exp")
        return exp if isinstance(exp, int) and not isinstance(exp, bool) else None
    except (IndexError, ValueError, AttributeError):
        return None


def _keel_user_id(body: Any) -> tuple[str, bool, dict[str, Any]]:
    """Validate Keel's user record; anything unexpected is a 401."""
    if not isinstance(body, dict):
        raise unauthenticated()
    raw_id, anon, meta = body.get("id"), body.get("is_anonymous"), body.get("app_metadata")
    if not isinstance(raw_id, str) or not isinstance(anon, bool) or not isinstance(meta, dict):
        raise unauthenticated()
    try:
        user_id = str(uuid.UUID(raw_id))
    except ValueError:
        raise unauthenticated() from None
    return user_id, anon, meta


@dataclass
class Authenticator:
    """Built once per app from ``Settings``; holds the Keel client and the success cache."""

    mode: str  # "none", "keel" (static tokens may also be on) or "token"
    keel_url: str = ""
    keel_anon_key: str = ""
    tokens: dict[str, bytes] = field(default_factory=dict)
    allowed_ids: frozenset[str] = frozenset()
    trusted_proxies: tuple = ()
    http: httpx.AsyncClient | None = None
    _cache: OrderedDict[bytes, tuple[float, str]] = field(default_factory=OrderedDict)

    @classmethod
    def from_settings(cls, s: Settings, http: httpx.AsyncClient | None = None) -> Authenticator:
        """Raise ``ConfigError`` for anything that would leave the API open or half set up."""
        if s.auth not in ("", "none"):
            raise ConfigError("FILEX_AUTH may only be empty or 'none'.")
        keel_url = _keel_url(s.keel_url) if s.keel_url else ""
        if bool(s.keel_url) != bool(s.keel_anon_key):
            missing = "KEEL_ANON_KEY" if s.keel_url else "KEEL_URL"
            raise ConfigError(f"Keel is half configured: set {missing} too.")
        tokens = _api_tokens(s.api_tokens)
        allowed = _user_ids(s.allowed_user_ids)
        proxies = _networks(s.trusted_proxies)
        if s.auth == "none":
            if not _is_loopback(s.bind):
                # ADR-0011: no sign-in is only for this machine; other devices need a token.
                raise ConfigError(
                    f"FILEX_AUTH=none is only allowed when FILEX_BIND is a loopback address "
                    f"(127.0.0.1, ::1 or localhost), not {s.bind!r}. To use FileX from other "
                    "devices, set FILEX_API_TOKENS (see .env.example) and leave FILEX_AUTH empty."
                )
            if any(h.startswith("*") for h in s.allowed_hosts):
                # A wildcard Host lets a DNS-rebinding page use the open API as its own origin.
                raise ConfigError(
                    "FILEX_AUTH=none does not allow wildcards in FILEX_ALLOWED_HOSTS: list the "
                    "exact names, e.g. localhost,127.0.0.1."
                )
            mode = "none"
        elif keel_url:
            mode = "keel"
        elif tokens:
            mode = "token"
        else:
            raise ConfigError(
                "No sign-in is configured. Set KEEL_URL and KEEL_ANON_KEY, or FILEX_API_TOKENS, "
                "or FILEX_AUTH=none to run without auth on purpose."
            )
        if mode == "keel" and http is None:
            # TLS verification is httpx's default and there is no setting to turn it off.
            http = httpx.AsyncClient(timeout=httpx.Timeout(3.0), follow_redirects=False)
        return cls(mode, keel_url, s.keel_anon_key, tokens, allowed, proxies, http)

    def client_ip(self, request: Request) -> str:
        """The peer, or the nearest untrusted ``X-Forwarded-For`` hop when the peer is a proxy."""
        peer = request.client.host if request.client else "unknown"
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
        for hop in [peer, *reversed([h for h in hops if h])]:
            try:
                ip = ipaddress.ip_address(hop)
            except ValueError:
                return peer
            if not any(ip in net for net in self.trusted_proxies):
                return str(ip)
        return peer

    async def principal(self, header: str | None) -> str:
        if self.mode == "none":
            return "anonymous"
        scheme, _, token = (header or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token or " " in token:
            raise unauthenticated()
        if token.startswith(FXT_PREFIX):
            return self._static(token)
        if self.mode != "keel":
            raise unauthenticated()
        key = hashlib.sha256(token.encode()).digest()
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            self._cache.move_to_end(key)
            return hit[1]
        self._cache.pop(key, None)
        principal = await self._keel(token)
        ttl = CACHE_TTL_S
        if (exp := _exp(token)) is not None:
            ttl = min(ttl, exp - int(time.time()))
        if ttl > 0:
            self._cache[key] = (time.monotonic() + ttl, principal)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return principal

    def evict(self, header: str | None) -> None:
        scheme, _, token = (header or "").partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            self._cache.pop(hashlib.sha256(token.strip().encode()).digest(), None)

    def _static(self, token: str) -> str:
        if len(token) < FXT_MIN_LEN or not self.tokens:
            raise unauthenticated()
        digest = hashlib.sha256(token.encode()).digest()
        found = None
        for name, expected in self.tokens.items():
            if hmac.compare_digest(digest, expected):
                found = name
        if found is None:
            raise unauthenticated()
        return f"token:{found}"

    async def _keel(self, token: str) -> str:
        assert self.http is not None
        try:
            res = await self.http.get(
                f"{self.keel_url}/auth/v1/user",
                headers={"apikey": self.keel_anon_key, "Authorization": f"Bearer {token}"},
            )
        except httpx.TransportError as e:  # timeouts, connection and TLS errors
            log.warning("Keel unreachable: %s", type(e).__name__)
            raise unavailable() from None
        if res.status_code == 429 or res.status_code >= 500:
            log.warning("Keel answered %d", res.status_code)
            raise unavailable()
        if res.status_code != 200:  # 401, GoTrue's 403, any other 4xx, 2xx or 3xx
            raise unauthenticated()
        try:
            body = res.json()
        except ValueError:
            raise unauthenticated() from None
        user_id, anonymous, meta = _keel_user_id(body)
        if anonymous:
            raise not_allowed()
        if user_id not in self.allowed_ids and meta.get("filex") not in ALLOWED_ROLES:
            raise not_allowed()
        return f"keel:{user_id}"

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()
