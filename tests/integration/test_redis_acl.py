"""Redis ACL users ``api`` and ``worker`` (task 54, ADR-0007 item 3, ADR-0008 key table).

The rule tests parse ``docker/redis/users.acl`` and always run. The live tests start the real
Valkey image that compose pins with that file and ``docker/redis/start.sh``, exactly as compose
does, and need Docker:

    FILEX_TEST_REDIS_ACL=docker python -m pytest tests/integration/test_redis_acl.py -q

The container is removed when the tests end.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import redis

from filex.jobs import redis_credentials

ROOT = Path(__file__).resolve().parents[2]
ACL = ROOT / "docker" / "redis" / "users.acl"
# The redis service's image in compose.yaml (Valkey 8, pinned by digest): test what ships.
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")
IMAGE = re.search(r"^\s*image:\s*(valkey/\S+)", COMPOSE, re.MULTILINE).group(1)

# ADR-0008 "Redis keys and who may write them". sec:* is special: the worker may only GETDEL it.
KEY_TABLE = {
    "job": {"api", "worker"},
    "q": {"api", "worker"},
    "own": {"api"},
    "ownidx": {"api"},
    "sec": {"api"},
    "worker": {"worker"},
    "slot": {"worker"},
    "rl": {"api"},
}
WORKER_CATEGORIES = [
    "-@all", "+@read", "+@write", "+@stream", "+@connection", "+@transaction",
    "-@admin", "-@dangerous",
]  # fmt: skip


def parse_acl(text: str) -> dict[str, dict]:
    """``{user: {"rules": [...], "selectors": [[...]]}}`` from the ACL template."""
    users = {}
    for line in text.splitlines():
        if not line.startswith("user "):
            continue
        selectors = [s.split() for s in re.findall(r"\(([^)]*)\)", line)]
        rules = re.sub(r"\([^)]*\)", "", line).split()
        users[rules[1]] = {"rules": rules[2:], "selectors": selectors}
    return users


def key_patterns(rules: list[str]) -> dict[str, str]:
    """``{prefix: "rw" | "r" | "w"}`` from ``~p:*``, ``%R~p:*``, ``%W~p:*``, ``%RW~p:*``."""
    out = {}
    for rule in rules:
        m = re.fullmatch(r"(?:%(R|W|RW)|)~([a-z]+):\*", rule)
        if m:
            out[m.group(2)] = (m.group(1) or "RW").lower()
    return out


@pytest.fixture(scope="module")
def acl() -> dict[str, dict]:
    return parse_acl(ACL.read_text(encoding="utf-8"))


def test_only_three_users_and_default_is_off(acl):
    assert set(acl) == {"default", "api", "worker"}
    default = acl["default"]["rules"]
    assert "off" in default and "on" not in default
    assert "resetpass" in default and "nopass" not in default
    assert not key_patterns(default) and "-@all" in default
    assert not [r for r in default if r.startswith("+")]


def test_worker_rules_are_the_adr_rules(acl):
    worker = acl["worker"]
    assert [r for r in worker["rules"] if r.startswith(("+", "-"))] == WORKER_CATEGORIES
    assert worker["selectors"] == [["~sec:*", "+getdel"]]


@pytest.mark.parametrize("prefix", sorted(KEY_TABLE))
def test_write_access_matches_the_key_table(acl, prefix):
    writers = KEY_TABLE[prefix]
    api, worker = key_patterns(acl["api"]["rules"]), key_patterns(acl["worker"]["rules"])
    assert ("w" in api.get(prefix, "")) == ("api" in writers)
    assert ("w" in worker.get(prefix, "")) == ("worker" in writers)


def test_api_reads_heartbeats_but_cannot_touch_slots(acl):
    api = key_patterns(acl["api"]["rules"])
    assert api["worker"] == "r"
    assert "slot" not in api
    assert "+@all" not in acl["api"]["rules"]
    assert {"-@admin", "-@dangerous"} <= set(acl["api"]["rules"])


def test_every_password_is_a_hash_placeholder(acl):
    for user in ("api", "worker"):
        secrets = [r for r in acl[user]["rules"] if r[0] in "#>" or r == "nopass"]
        assert secrets == [f"#@{user.upper()}_HASH@"]


def test_credentials_come_from_the_secret_file(tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("s3cret-from-file\n", encoding="utf-8")
    env = {"FILEX_REDIS_USER": "api", "FILEX_REDIS_PASSWORD_FILE": str(secret)}
    assert redis_credentials(env | {"FILEX_REDIS_PASSWORD": "env"}) == ("api", "s3cret-from-file")
    assert redis_credentials({"FILEX_REDIS_PASSWORD": "env"}) == (None, "env")
    assert redis_credentials({}) == (None, None)
    secret.write_text("\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        redis_credentials(env)
    with pytest.raises(FileNotFoundError):
        redis_credentials({"FILEX_REDIS_PASSWORD_FILE": str(tmp_path / "missing")})


def _make_secrets():
    spec = importlib.util.spec_from_file_location("make_secrets", ROOT / "scripts/make_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_secrets


def test_make_secrets_keeps_existing_files_unless_forced(tmp_path):
    make_secrets = _make_secrets()
    folder = tmp_path / "secrets"
    first = make_secrets(folder)
    assert sorted(p.name for p in first) == ["redis_api_password", "redis_worker_password"]
    api = (folder / "redis_api_password").read_text(encoding="utf-8")
    assert len(api) >= 32 and "\n" not in api
    assert make_secrets(folder) == []
    assert len(make_secrets(folder, force=True)) == 2
    assert (folder / "redis_api_password").read_text(encoding="utf-8") != api


# --- live: the real image, the real start.sh -------------------------------------------------

live = pytest.mark.skipif(
    os.environ.get("FILEX_TEST_REDIS_ACL") != "docker" or not shutil.which("docker"),
    reason="needs Docker (FILEX_TEST_REDIS_ACL=docker)",
)


def _docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=120
    ).stdout.strip()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("acl") / "secrets"
    _make_secrets()(folder)
    name = f"filex-test-acl-{uuid.uuid4().hex[:8]}"
    mounts = [f"{ROOT / 'docker' / 'redis'}:/etc/filex-redis:ro"]
    mounts += [f"{folder / n}:/run/secrets/{n}:ro" for n in ("redis_api_password", "redis_worker_password")]  # fmt: skip
    _docker(
        "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::6379",
        "--user", "999:1000", "--read-only", "--tmpfs", "/tmp:size=16m,mode=1777",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        *[a for m in mounts for a in ("-v", m)],
        "--entrypoint", "sh", IMAGE, "/etc/filex-redis/start.sh",
    )  # fmt: skip
    try:
        host, port = _docker("port", name, "6379/tcp").splitlines()[0].rsplit(":", 1)
        info = {
            "host": host,
            "port": int(port),
            "api": (folder / "redis_api_password").read_text(encoding="utf-8"),
            "worker": (folder / "redis_worker_password").read_text(encoding="utf-8"),
            "name": name,
        }
        for _ in range(50):
            try:
                _client(info, "api").ping()
                break
            except (redis.ConnectionError, redis.AuthenticationError):
                time.sleep(0.2)
        else:
            pytest.fail("Redis did not start: " + _docker("logs", name))
        yield info
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, timeout=60)


def _client(info, user: str | None, password: str | None = None) -> redis.Redis:
    return redis.Redis(
        host=info["host"],
        port=info["port"],
        username=user,
        password=password if password is not None else info.get(user or "", None),
        decode_responses=True,
        socket_connect_timeout=2,
    )


DENIED = (redis.exceptions.NoPermissionError, redis.ResponseError)


@live
@pytest.mark.parametrize(
    "command",
    [
        ("SET", "own:x", "y"),
        ("ZADD", "ownidx:x", "1", "y"),
        ("INCR", "rl:x"),
        ("GET", "sec:x"),
        ("SET", "sec:x", "y"),
        ("DEL", "sec:x"),
        ("CONFIG", "SET", "dir", "/tmp"),
        ("CONFIG", "GET", "*"),
        ("MODULE", "LOAD", "/tmp/x.so"),
        ("FLUSHALL",),
        ("KEYS", "*"),
        ("EVAL", "return 1", "0"),
        ("ACL", "LIST"),
    ],
)
def test_worker_denied(server, command):
    with pytest.raises(DENIED):
        _client(server, "worker").execute_command(*command)


@live
def test_worker_allowed(server):
    w = _client(server, "worker")
    a = _client(server, "api")
    a.set("sec:j:0", "secret")
    assert w.getdel("sec:j:0") == "secret"
    assert w.xadd("q:core", {"job": "j", "step": "0"})
    w.xgroup_create("q:core", "workers", id="0", mkstream=True)
    assert w.xreadgroup("workers", "w1", {"q:core": ">"}, count=1)
    assert w.hset("job:x", mapping={"status": "RUNNING"}) == 1
    with w.pipeline() as p:  # the runner's WATCH/MULTI/EXEC job writes
        p.watch("job:x")
        p.multi()
        p.hset("job:x", "status", "SUCCEEDED")
        p.expire("job:x", 60)
        p.execute()
    assert w.set("slot:heavy:0", "t", nx=True, px=30000)
    assert w.delete("slot:heavy:0") == 1
    assert w.set("worker:core:w1", "{}", ex=30)


@live
def test_api_allowed_and_denied(server):
    a = _client(server, "api")
    a.set("own:x", "keel:u", ex=60)
    a.zadd("ownidx:keel:u", {"x": 1})
    a.incr("rl:auth:1.2.3.4")
    a.set("sec:x:0", "{}")
    a.hset("job:y", "status", "QUEUED")
    a.xadd("q:office", {"job": "y", "step": "0"})
    _client(server, "worker").set("worker:core:w2", "{}", ex=30)
    assert "worker:core:w2" in list(a.scan_iter(match="worker:*"))
    assert a.get("worker:core:w2") == "{}"
    for command in (
        ("SET", "worker:core:w2", "forged"),
        ("SET", "slot:heavy:0", "t"),
        ("SET", "other:x", "y"),
        ("CONFIG", "SET", "dir", "/tmp"),
        ("FLUSHALL",),
        ("KEYS", "*"),
    ):
        with pytest.raises(DENIED):
            a.execute_command(*command)


@live
def test_no_user_and_default_user_are_refused(server):
    with pytest.raises((redis.AuthenticationError, redis.ResponseError)):
        _client(server, None, "").ping()  # no AUTH at all: NOAUTH
    with pytest.raises((redis.AuthenticationError, redis.ResponseError)):
        _client(server, None, server["api"]).ping()  # AUTH <password> = default user, off
    with pytest.raises((redis.AuthenticationError, redis.ResponseError)):
        _client(server, "default", server["api"]).ping()
    with pytest.raises(redis.AuthenticationError):
        _client(server, "worker", server["api"]).ping()  # each user has its own password


@live
def test_container_holds_no_password_outside_the_secret_files(server):
    inspect = _docker("inspect", server["name"])
    assert server["api"] not in inspect and server["worker"] not in inspect
