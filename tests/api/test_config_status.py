"""GET /config, GET /status and the minimal /health (task 60, ADR-0009 A9, review L2)."""

import hashlib
import json

from fastapi.testclient import TestClient

from filex.api.deps import MiB, Settings
from filex.api.main import create_app

TOKEN = "fxt_" + "T" * 43
TOKENS = f"ci:{hashlib.sha256(TOKEN.encode()).hexdigest()}"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEEL = "https://keel.example.com/filex"


def test_health_is_only_ok_without_a_token(make_client):
    res = make_client(auth="", api_tokens=TOKENS).get("/api/v1/health")
    assert res.status_code == 200
    assert res.content == b'{"status":"ok"}'


def test_status_needs_a_token(make_client):
    assert make_client(auth="", api_tokens=TOKENS).get("/api/v1/status").status_code == 401


def test_status_lists_groups_and_engine_versions(make_client, r):
    beat = {
        "tools": ["api.pdf"],
        "engines": {"qpdf": "11.9.0", "gs": "10.03"},
        "isolation": {"seccomp": True, "landlock": "abi7"},
    }
    r.set("worker:core:w1", json.dumps(beat), ex=30)
    w2 = {
        "tools": [],
        "engines": {"qpdf": "11.9.0"},
        "isolation": {"seccomp": True, "landlock": "abi4"},
    }
    r.set("worker:core:w2", json.dumps(w2), ex=30)
    res = make_client(auth="", api_tokens=TOKENS).get("/api/v1/status", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["isolation"] == "container" and body["version"]
    groups = {g["name"]: g for g in body["groups"]}
    assert groups["core"] == {
        "name": "core",
        "online": True,
        "workers": 2,
        "engines": {"qpdf": "11.9.0", "gs": "10.03"},
        "isolation": {"seccomp": True, "landlock": "abi4"},  # the weakest live worker
    }
    offline = [g for name, g in groups.items() if name != "core"]
    assert offline and all(not g["online"] and g["workers"] == 0 for g in offline)
    assert all(g["isolation"] is None for g in offline)


def test_status_treats_a_garbled_isolation_as_unconfined(make_client, r):
    beat = {"tools": [], "engines": {}, "isolation": {"seccomp": "yes", "landlock": "<b>abi9"}}
    r.set("worker:ocr:w1", json.dumps(beat), ex=30)
    r.set("worker:core:w1", json.dumps({"tools": [], "engines": {}}), ex=30)  # older worker
    res = make_client(auth="", api_tokens=TOKENS).get("/api/v1/status", headers=AUTH)
    groups = {g["name"]: g for g in res.json()["groups"]}
    assert groups["ocr"]["isolation"] == {"seccomp": False, "landlock": "unavailable"}
    assert groups["core"]["isolation"] == {"seccomp": False, "landlock": "unavailable"}


def test_config_needs_no_token_and_shows_only_public_values(make_client):
    res = make_client(auth="", keel_url=KEEL, keel_anon_key="anon-key", api_tokens=TOKENS).get(
        "/api/v1/config"
    )
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    assert res.json() == {
        "authMode": "KEEL",
        "keelUrl": KEEL,
        "keelAnonKey": "anon-key",
        "limits": {"maxUploadMb": 0, "maxFiles": 5, "retentionMin": 1440},
        "version": res.json()["version"],
    }
    assert TOKENS.split(":")[1] not in res.text  # token hashes never leave the box


def test_config_auth_modes(make_client):
    none = make_client().get("/api/v1/config").json()
    assert none["authMode"] == "NONE" and none["keelUrl"] is None and none["keelAnonKey"] is None
    token = make_client(auth="", api_tokens=TOKENS).get("/api/v1/config").json()
    assert token["authMode"] == "TOKEN" and token["keelUrl"] is None


def test_config_reflects_env_after_a_restart(monkeypatch, tools):
    def boot() -> dict:
        app = create_app(Settings.from_env(), redis=object(), tools=tools)
        return TestClient(app, base_url="http://localhost").get("/api/v1/config").json()

    for name in ("KEEL_URL", "KEEL_ANON_KEY", "FILEX_API_TOKENS", "FILEX_MAX_FILES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FILEX_AUTH", "none")
    monkeypatch.setenv("FILEX_MAX_UPLOAD_MB", "200")
    monkeypatch.setenv("FILEX_RETENTION_MIN", "60")
    first = boot()
    assert first["authMode"] == "NONE"
    assert first["limits"] == {"maxUploadMb": 200, "maxFiles": 50, "retentionMin": 60}

    monkeypatch.setenv("FILEX_AUTH", "")
    monkeypatch.setenv("KEEL_URL", KEEL)
    monkeypatch.setenv("KEEL_ANON_KEY", "k2")
    monkeypatch.setenv("FILEX_RETENTION_MIN", "99999")  # capped at 1440
    second = boot()
    assert (second["authMode"], second["keelUrl"], second["keelAnonKey"]) == ("KEEL", KEEL, "k2")
    assert second["limits"]["retentionMin"] == 1440


def test_limits_come_from_settings(make_client):
    limits = make_client(max_upload=300 * MiB, max_files=7).get("/api/v1/config").json()["limits"]
    assert limits["maxUploadMb"] == 300 and limits["maxFiles"] == 7
