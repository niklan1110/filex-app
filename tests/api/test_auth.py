"""Keel sign-in and static tokens, API side (task 13, ADR-0009, review C1, M1, M2).

Keel is never called for real: every test answers ``GET /auth/v1/user`` with an
``httpx.MockTransport``.
"""

import asyncio
import base64
import hashlib
import json
import logging
import time
import types

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from filex.api import auth as auth_mod
from filex.api import main as main_mod
from filex.api.auth import Authenticator, ConfigError
from filex.api.deps import Settings
from filex.api.main import create_app

KEEL = "https://keel.test/filex"
ANON = "anon-key-public"
UID = "8d2b6f2e-0c1a-4f9e-9a63-5c0f1e2d3a4b"
TOOLS = "/api/v1/tools"
FXT = "fxt_" + "A" * 43  # 47 characters, the shortest real token
FXT_HASH = hashlib.sha256(FXT.encode()).hexdigest()


def jwt(exp: int | None = None, tag: str = "a") -> str:
    """A token-shaped string. Only ``exp`` is ever read locally, and never trusted."""
    claims = {"sub": UID, "tag": tag} | ({"exp": exp} if exp is not None else {})
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.c2ln"


def user(**over) -> dict:
    return {
        "id": UID,
        "aud": "authenticated",
        "email": "ada@example.com",
        "is_anonymous": False,
        "app_metadata": {"provider": "email", "filex": "user"},
        "user_metadata": {},
    } | over


class FakeKeel:
    """Answers ``GET /auth/v1/user`` with ``status`` and ``body``, or raises ``exc``."""

    def __init__(self):
        self.status, self.body, self.exc, self.calls = 200, user(), None, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        if isinstance(self.body, (bytes, str)):
            return httpx.Response(self.status, content=self.body)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def keel():
    return FakeKeel()


@pytest.fixture
def make(server, tools, data, online, keel):
    def make(peer: str = "testclient", **over) -> TestClient:
        cfg = Settings(
            **{
                "data": data,
                "allowed_hosts": ("testserver",),
                "keel_url": KEEL,
                "keel_anon_key": ANON,
                **over,
            }
        )
        ar = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        http = httpx.AsyncClient(transport=httpx.MockTransport(keel))
        return TestClient(create_app(cfg, redis=ar, tools=tools, http=http), client=(peer, 5000))

    return make


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Keel branches


def test_allowed_keel_user_passes_and_keel_gets_the_anon_key(make, keel):
    token = jwt()
    res = make().get(TOOLS, headers=bearer(token))
    assert res.status_code == 200
    (req,) = keel.calls
    assert str(req.url) == f"{KEEL}/auth/v1/user"
    assert req.headers["apikey"] == ANON and req.headers["authorization"] == f"Bearer {token}"


@pytest.mark.parametrize("status", [401, 403, 404, 400, 418, 201, 204, 302])
def test_any_other_keel_status_is_401(make, keel, status):
    keel.status = status
    res = make().get(TOOLS, headers=bearer(jwt()))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_keel_overloaded_or_failing_is_503_and_not_cached(make, keel, status):
    keel.status = status
    c = make()
    for _ in range(2):
        res = c.get(TOOLS, headers=bearer(jwt()))
        assert res.status_code == 503 and res.headers["retry-after"] == "10"
        assert res.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert len(keel.calls) == 2  # never cached


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("t"),
        httpx.ReadTimeout("t"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("tls or framing"),
    ],
)
def test_keel_timeout_or_transport_error_is_503(make, keel, exc):
    keel.exc = exc
    res = make().get(TOOLS, headers=bearer(jwt()))
    assert res.status_code == 503 and res.headers["retry-after"] == "10"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        [],
        user(id="not-a-uuid"),
        user(id=123),
        {k: v for k, v in user().items() if k != "is_anonymous"},
        user(is_anonymous="false"),
        user(app_metadata="user"),
    ],
)
def test_malformed_keel_body_is_401(make, keel, body):
    keel.body = body
    assert make().get(TOOLS, headers=bearer(jwt())).status_code == 401


# --- allowlist


@pytest.mark.parametrize(
    "body",
    [
        user(app_metadata={"provider": "email"}),
        user(app_metadata={}, user_metadata={"filex": "admin"}),
        user(is_anonymous=True),
        user(app_metadata={"filex": "guest"}),
        user(app_metadata={"filex": ["user"]}),
    ],
)
def test_user_not_on_the_allowlist_is_403(make, keel, body):
    keel.body = body
    res = make().get(TOOLS, headers=bearer(jwt()))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "USER_NOT_ALLOWED"


@pytest.mark.parametrize("role", ["user", "admin"])
def test_app_metadata_role_grants_access(make, keel, role):
    keel.body = user(app_metadata={"filex": role})
    assert make().get(TOOLS, headers=bearer(jwt())).status_code == 200


def test_allowed_user_ids_grant_access_without_app_metadata(make, keel):
    keel.body = user(app_metadata={})
    c = make(allowed_user_ids=f"00000000-0000-0000-0000-000000000001, {UID.upper()}")
    assert c.get(TOOLS, headers=bearer(jwt())).status_code == 200


def test_anonymous_keel_user_is_403_even_when_allowlisted(make, keel):
    keel.body = user(is_anonymous=True)
    assert make(allowed_user_ids=UID).get(TOOLS, headers=bearer(jwt())).status_code == 403


# --- only the Bearer header counts


def test_token_outside_the_authorization_header_is_ignored(make, keel):
    c = make()
    assert c.get(TOOLS, params={"access_token": jwt()}).status_code == 401
    assert c.get(TOOLS, headers={"Authorization": f"Basic {jwt()}"}).status_code == 401
    c.cookies.set("sb-access-token", jwt())
    assert c.get(TOOLS).status_code == 401
    assert keel.calls == []


# --- static fxt_ tokens


def test_matching_fxt_token_passes_while_keel_is_down(make, keel):
    keel.exc = httpx.ConnectError("down")
    c = make(api_tokens=f"other:{'1' * 64},ci:{FXT_HASH}")
    assert c.get(TOOLS, headers=bearer(FXT)).status_code == 200
    assert keel.calls == []


def test_wrong_fxt_token_is_401_and_never_sent_to_keel(make, keel):
    c = make(api_tokens=f"ci:{FXT_HASH}")
    assert c.get(TOOLS, headers=bearer("fxt_" + "B" * 43)).status_code == 401
    assert keel.calls == []


def test_short_fxt_value_is_rejected_before_hashing(make, monkeypatch):
    c = make(api_tokens=f"ci:{FXT_HASH}")

    def boom(*_):
        raise AssertionError("hashed a short token")

    monkeypatch.setattr(auth_mod, "hashlib", types.SimpleNamespace(sha256=boom))
    assert c.get(TOOLS, headers=bearer(FXT[:46])).status_code == 401


def test_token_only_mode_refuses_other_bearer_values(make, keel):
    c = make(keel_url="", keel_anon_key="", api_tokens=f"ci:{FXT_HASH}")
    assert c.get(TOOLS, headers=bearer(jwt())).status_code == 401
    assert c.get(TOOLS, headers=bearer(FXT)).status_code == 200


def test_principals_carry_the_keel_id_or_the_token_name(keel):
    async def run():
        s = Settings(keel_url=KEEL, keel_anon_key=ANON, api_tokens=f"ci:{FXT_HASH}")
        a = Authenticator.from_settings(s, httpx.AsyncClient(transport=httpx.MockTransport(keel)))
        return await a.principal(f"Bearer {jwt()}"), await a.principal(f"bearer {FXT}")

    assert asyncio.run(run()) == (f"keel:{UID}", "token:ci")


def test_tokens_never_appear_in_logs(make, keel, caplog):
    caplog.set_level(logging.DEBUG)
    good, bad = jwt(tag="good"), jwt(tag="bad")
    c = make(api_tokens=f"ci:{FXT_HASH}")
    c.get(TOOLS, headers=bearer(good))
    c.get(TOOLS, headers=bearer(FXT))
    c.get(TOOLS, headers=bearer("fxt_" + "C" * 43))
    keel.status = 500
    c.get(TOOLS, headers=bearer(bad))
    keel.exc = httpx.ConnectError("down")
    c.get(TOOLS, headers=bearer(bad))
    for secret in (good, bad, FXT, "C" * 43):
        assert secret not in caplog.text


# --- startup


def settings(**over) -> Settings:
    return Settings(**over)


def test_no_auth_at_all_refuses_to_start_naming_the_variables():
    with pytest.raises(ConfigError) as e:
        create_app(settings(), redis=fakeredis.FakeAsyncRedis(), tools={})
    for name in ("KEEL_URL", "KEEL_ANON_KEY", "FILEX_API_TOKENS", "FILEX_AUTH"):
        assert name in str(e.value)


def test_uvicorn_entry_point_exits_non_zero_without_auth(monkeypatch):
    for name in ("KEEL_URL", "KEEL_ANON_KEY", "FILEX_API_TOKENS", "FILEX_AUTH"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as e:
        main_mod.__getattr__("app")
    assert isinstance(e.value.code, str) and "KEEL_URL" in e.value.code  # exit status 1


@pytest.mark.parametrize(
    "entry",
    [
        "ci:" + FXT_HASH.upper(),
        "ci:abc123",
        "CI:" + FXT_HASH,
        "bad name:" + FXT_HASH,
        "x" * 33 + ":" + FXT_HASH,
        FXT,  # the token pasted instead of its hash
        "ci" + FXT_HASH,
    ],
)
def test_malformed_api_token_entry_refuses_to_start(entry):
    with pytest.raises(ConfigError, match="FILEX_API_TOKENS") as e:
        Authenticator.from_settings(settings(api_tokens=entry))
    assert FXT not in str(e.value)


def test_duplicate_api_token_name_refuses_to_start():
    with pytest.raises(ConfigError, match="twice"):
        Authenticator.from_settings(settings(api_tokens=f"ci:{FXT_HASH},ci:{'2' * 64}"))


@pytest.mark.parametrize(
    "url", ["http://keel.test/filex", "keel.test/filex", "https://", "https://k.test/x?a=1"]
)
def test_non_https_keel_url_refuses_to_start(url):
    with pytest.raises(ConfigError, match="KEEL_URL"):
        Authenticator.from_settings(settings(keel_url=url, keel_anon_key=ANON))


@pytest.mark.parametrize(
    "over",
    [
        {"keel_url": KEEL},
        {"keel_anon_key": ANON},
        {"auth": "off"},
        {"auth": "none", "allowed_user_ids": "nope"},
        {"auth": "none", "trusted_proxies": "not-an-ip"},
    ],
)
def test_incomplete_or_unknown_auth_settings_refuse_to_start(over):
    with pytest.raises(ConfigError):
        Authenticator.from_settings(settings(**over))


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.8.9.10", "::1", "[::1]", "localhost"])
def test_auth_none_starts_on_a_loopback_bind(bind):
    assert Authenticator.from_settings(settings(auth="none", bind=bind)).mode == "none"


@pytest.mark.parametrize(
    "bind", ["0.0.0.0", "::", "192.168.1.20", "100.64.0.7", "fd00::1", "files.local", "not an ip"]
)
def test_auth_none_on_a_non_loopback_bind_refuses_to_start(bind):
    with pytest.raises(ConfigError) as e:
        Authenticator.from_settings(settings(auth="none", bind=bind))
    assert "FILEX_BIND" in str(e.value) and "FILEX_API_TOKENS" in str(e.value)


def test_non_loopback_bind_is_fine_with_sign_in():
    lan = {"bind": "192.168.1.20"}
    assert Authenticator.from_settings(settings(api_tokens=f"ci:{FXT_HASH}", **lan)).mode == "token"
    assert (
        Authenticator.from_settings(settings(keel_url=KEEL, keel_anon_key=ANON, **lan)).mode
        == "keel"
    )


def test_filex_bind_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("FILEX_BIND", " 192.168.1.20 ")
    assert Settings.from_env().bind == "192.168.1.20"
    monkeypatch.setenv("FILEX_BIND", "")
    assert Settings.from_env().bind == "127.0.0.1"  # as compose's ${FILEX_BIND:-127.0.0.1}


def test_auth_none_starts_with_a_warning_and_lets_everyone_in(make, keel, caplog):
    caplog.set_level(logging.WARNING)
    c = make(auth="none", keel_url="", keel_anon_key="")
    assert "WITHOUT sign-in" in caplog.text
    assert c.get(TOOLS).status_code == 200


# --- local mode against other websites (public release review M-1, M-2)


@pytest.mark.parametrize("hosts", [("*",), ("localhost", "*.example.com"), ("*lan",)])
def test_auth_none_refuses_wildcard_allowed_hosts(hosts):
    # A wildcard lets a DNS-rebinding page reach an open FileX on 127.0.0.1 as its own origin.
    with pytest.raises(ConfigError) as e:
        Authenticator.from_settings(settings(auth="none", allowed_hosts=hosts))
    assert "FILEX_ALLOWED_HOSTS" in str(e.value)


def test_wildcard_allowed_hosts_is_still_fine_with_sign_in():
    s = settings(api_tokens=f"ci:{FXT_HASH}", allowed_hosts=("*",))
    assert Authenticator.from_settings(s).mode == "token"


@pytest.fixture
def local(make):
    return make(auth="none", keel_url="", keel_anon_key="")


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://127.0.0.1.evil.example:8080"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_local_mode_refuses_requests_from_other_websites(local, headers):
    for res in (
        local.delete("/api/v1/session", headers=headers),
        local.get(TOOLS, headers=headers),
    ):
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "CROSS_SITE_REQUEST"


@pytest.mark.parametrize(
    "headers",
    [
        {},  # scripts on this computer send no Origin
        {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        {"Origin": "http://TESTSERVER:5173", "Sec-Fetch-Site": "same-site"},  # the Vite dev proxy
        {"Sec-Fetch-Site": "none"},  # typed into the address bar
    ],
)
def test_local_mode_accepts_its_own_page_and_local_scripts(local, headers):
    assert local.delete("/api/v1/session", headers=headers).status_code == 204
    assert local.get(TOOLS, headers=headers).status_code == 200


def test_local_mode_accepts_its_own_page_on_ipv6_loopback(make):
    c = make(auth="none", keel_url="", keel_anon_key="", allowed_hosts=("testserver", "[::1]"))
    same = {"Origin": "http://[::1]:8080", "Sec-Fetch-Site": "same-origin"}
    assert c.delete("/api/v1/session", headers=same).status_code == 204
    broken = {"Origin": "http://[::1"}
    assert c.delete("/api/v1/session", headers=broken).status_code == 403


def test_cross_site_check_is_only_for_local_mode(make):
    # With sign-in, a browser never attaches the Bearer header on its own: nothing to forge.
    c = make(api_tokens=f"ci:{FXT_HASH}", keel_url="", keel_anon_key="")
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert c.delete("/api/v1/session", headers=evil).status_code == 204


# --- cache


def test_keel_is_asked_once_per_token_until_delete_session(make, keel):
    c, token = make(), jwt()
    for _ in range(3):
        assert c.get(TOOLS, headers=bearer(token)).status_code == 200
    assert len(keel.calls) == 1
    assert c.delete("/api/v1/session", headers=bearer(token)).status_code == 204
    assert c.get(TOOLS, headers=bearer(token)).status_code == 200
    assert len(keel.calls) == 2


def test_cache_entry_lives_at_most_60_s_and_never_past_exp(keel, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(
        auth_mod, "time", types.SimpleNamespace(monotonic=lambda: clock[0], time=time.time)
    )

    async def run():
        s = Settings(keel_url=KEEL, keel_anon_key=ANON)
        a = Authenticator.from_settings(s, httpx.AsyncClient(transport=httpx.MockTransport(keel)))
        long_lived, short, expired = jwt(), jwt(int(time.time()) + 10), jwt(int(time.time()) - 1)
        for t in (long_lived, short, expired):
            await a.principal(f"Bearer {t}")
        assert len(keel.calls) == 3
        await a.principal(f"Bearer {expired}")  # never cached
        assert len(keel.calls) == 4
        clock[0] += 11
        await a.principal(f"Bearer {long_lived}")  # still cached
        await a.principal(f"Bearer {short}")  # past its exp
        assert len(keel.calls) == 5
        clock[0] += 50
        await a.principal(f"Bearer {long_lived}")  # 61 s later
        assert len(keel.calls) == 6

    asyncio.run(run())


def test_cache_is_bounded(keel, monkeypatch):
    monkeypatch.setattr(auth_mod, "CACHE_SIZE", 2)

    async def run():
        s = Settings(keel_url=KEEL, keel_anon_key=ANON)
        a = Authenticator.from_settings(s, httpx.AsyncClient(transport=httpx.MockTransport(keel)))
        for tag in "abca":
            await a.principal(f"Bearer {jwt(tag=tag)}")
        return len(keel.calls), len(a._cache)

    assert asyncio.run(run()) == (4, 2)  # "a" was evicted by "c"


def test_failures_are_not_cached(make, keel):
    c, token = make(), jwt()
    keel.status = 401
    c.get(TOOLS, headers=bearer(token))
    keel.status = 200
    assert c.get(TOOLS, headers=bearer(token)).status_code == 200
    assert len(keel.calls) == 2


# --- abuse limit


def test_21st_401_from_one_ip_gets_429(make, keel, r):
    keel.status = 401
    c = make(peer="203.0.113.5")
    for n in range(20):
        assert c.get(TOOLS, headers=bearer(jwt(tag=str(n)))).status_code == 401
    keel.status = 200
    res = c.get(TOOLS, headers=bearer(jwt()))
    assert res.status_code == 429 and res.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert 1 <= int(res.headers["retry-after"]) <= 60
    assert len(keel.calls) == 20  # the limited request never reached Keel
    assert 0 < r.ttl("rl:auth:203.0.113.5") <= 60


def test_403_not_allowed_counts_toward_the_limit_too(make, keel, r):
    """Review L-1: a Keel user off the allowlist cannot make FileX call Keel without limit."""
    keel.body = user(app_metadata={})
    c = make(peer="203.0.113.7")
    for n in range(20):
        assert c.get(TOOLS, headers=bearer(jwt(tag=str(n)))).status_code == 403
    assert c.get(TOOLS, headers=bearer(jwt())).status_code == 429
    assert len(keel.calls) == 20


def test_forwarded_for_is_ignored_from_an_untrusted_peer(make, r):
    c = make(peer="203.0.113.5")
    for n in range(21):
        c.get(TOOLS, headers={"X-Forwarded-For": f"198.51.100.{n}"})
    assert r.get("rl:auth:203.0.113.5") == "20"
    assert not r.keys("rl:auth:198.51.100.*")
    assert c.get(TOOLS, headers={"X-Forwarded-For": "198.51.100.99"}).status_code == 429


def test_forwarded_for_is_used_from_a_trusted_proxy(make, r):
    c = make(peer="127.0.0.1")
    for _ in range(21):
        c.get(TOOLS, headers={"X-Forwarded-For": "198.51.100.1"})
    assert c.get(TOOLS, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429
    # A client can prepend fake hops; only the one the proxy appended counts.
    res = c.get(TOOLS, headers={"X-Forwarded-For": "198.51.100.1, 198.51.100.2"})
    assert res.status_code == 401
    assert not r.exists("rl:auth:127.0.0.1")


# --- host and CORS


def test_unknown_host_is_400_and_preflight_gets_no_cors_headers(make):
    c = make()
    assert c.get("/api/v1/health", headers={"host": "evil.example"}).status_code == 400
    res = c.options(
        TOOLS,
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert not any(h.lower().startswith("access-control-") for h in res.headers)
