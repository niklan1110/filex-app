"""Fixtures for runner tests: a real Redis when FILEX_TEST_REDIS_URL is set, else fakeredis.

fakeredis differs from Redis on details the runner relies on (XAUTOCLAIM's scan cap, for one),
so run this folder against a real, throwaway Redis before merging runner changes. The fixture
FLUSHes the database it is given: never point it at the compose Redis or a Redis with data.

    docker run --rm -d --name filex-test-redis -p 127.0.0.1:6399:6379 valkey/valkey:8.1-alpine
    FILEX_TEST_REDIS_URL=redis://127.0.0.1:6399/15 python -m pytest tests/integration
    docker stop filex-test-redis

CI runs the same (task 11). Tests marked ``real_redis_only`` are skipped on fakeredis.
"""

import json
import os
import sys
from pathlib import Path

import pytest
import redis

from filex.jobs import JobPaths, job_key, new_job_id, queue_key, sec_key
from filex.registry import discover
from filex.worker.runner import Config, Runner

HERE = Path(__file__).parent
LINUX = sys.platform.startswith("linux")
linux_only = pytest.mark.skipif(not LINUX, reason="needs Linux (rlimits, /proc, seccomp)")
real_redis_only = pytest.mark.skipif(
    not os.environ.get("FILEX_TEST_REDIS_URL"), reason="needs a real Redis (FILEX_TEST_REDIS_URL)"
)
PDF = b"%PDF-1.7\n%test\n"


@pytest.fixture
def r():
    url = os.environ.get("FILEX_TEST_REDIS_URL")
    if url:
        client = redis.Redis.from_url(url, decode_responses=True)
        client.flushdb()
    else:
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    if url:
        client.flushdb()


@pytest.fixture(scope="session")
def tools():
    return discover("runner_tools")


@pytest.fixture(autouse=True)
def child_path(monkeypatch):
    """Step children import the dummy tools (and filex, if not installed) via PYTHONPATH."""
    paths = [str(HERE), str(HERE.parents[1]), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in paths if p))


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def make_runner(r, data, tools):
    def make(group="core", probes=None, **cfg):
        return Runner(r, Config(group=group, data=data, **cfg), tools=tools, probes=probes or {})

    return make


@pytest.fixture
def make_job(r, data):
    def make(*steps, inputs=1, aux=None, secret=None, group="core", names=None):
        """steps: (tool_id, options) pairs; aux: {slot: (format, bytes)}."""
        job_id = new_job_id()
        paths = JobPaths(data, job_id)
        paths.in_dir.mkdir(parents=True)
        for i in range(inputs):
            paths.input(i, "pdf").write_bytes(PDF + str(i).encode())
        aux_formats = {}
        for slot, (fmt, content) in (aux or {}).items():
            paths.aux_dir.mkdir(exist_ok=True)
            paths.aux(slot, fmt).write_bytes(content)
            aux_formats[slot] = fmt
        spec = [
            {"tool": t, "options": o, "secret": secret is not None and n == 0}
            for n, (t, o) in enumerate(steps)
        ]
        r.hset(
            job_key(job_id),
            mapping={
                "status": "QUEUED",
                "steps": json.dumps(spec),
                "inputs": json.dumps(
                    [
                        {"format": "pdf", "name": n}
                        for n in (names or [f"{i}.pdf" for i in range(inputs)])
                    ]
                ),
                "aux": json.dumps(aux_formats),
            },
        )
        if secret is not None:
            r.set(sec_key(job_id, 0), json.dumps(secret))
        r.xadd(queue_key(group), {"job": job_id, "step": "0"})
        return job_id

    return make


def job(r, job_id):
    """The job hash with JSON fields decoded."""
    h = r.hgetall(job_key(job_id))
    return {
        k: json.loads(v) if k not in ("status", "finishedAt", "expiresAt") else v
        for k, v in h.items()
    }
