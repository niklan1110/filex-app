"""Fixtures for the job API tests: one fakeredis server shared by the async API client and the
sync runner, a temp data volume, and the dummy tools in ``api_tools``."""

import json
import os
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

from filex.api.deps import Settings
from filex.api.main import create_app
from filex.registry import discover

HERE = Path(__file__).parent
PDF = b"%PDF-1.7\n%api test\n"


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def r(server):
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture(scope="session")
def tools():
    return discover("api_tools")


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def settings(data):
    return Settings(
        data=data,
        auth="none",
        allowed_hosts=("testserver",),
        max_upload=10_000,
        max_file=6_000,
        max_files=5,
        min_free_mb=0,  # tests/api/test_backpressure.py turns the free-space check on
    )


@pytest.fixture
def online(r, tools):
    """A live core worker heartbeat, as the runner writes it."""
    core = sorted(t.id for t in tools.values() if t.group == "core")
    r.set("worker:core:test-1", json.dumps({"tools": core, "engines": {}}), ex=30)


@pytest.fixture
def make_client(server, tools, settings):
    def make(**overrides):
        cfg = Settings(**{**settings.__dict__, **overrides})
        ar = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        return TestClient(create_app(cfg, redis=ar, tools=tools))

    return make


@pytest.fixture
def client(make_client, online):
    return make_client()


@pytest.fixture(autouse=True)
def child_path(monkeypatch):
    """Step children of the in-process runner import ``api_tools`` via PYTHONPATH."""
    paths = [str(HERE), str(HERE.parents[1]), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in paths if p))
