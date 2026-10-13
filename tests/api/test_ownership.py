"""Job ownership (task 62, ADR-0009 "Ownership", review H7): own:<id>, require_job, GET /jobs."""

import hashlib
import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.openapi.utils import _get_api_route_for_openapi
from fastapi.routing import iter_route_contexts

from filex.api.deps import (
    OWNER_TTL_S,
    QUEUED_CAP_S,
    JobIdParam,
    Settings,
    find_job,
    require_job,
)
from filex.api.main import create_app
from filex.jobs import JobPaths, job_key, own_key, ownidx_key

PDF = b"%PDF-1.7\n%api test\n"
JOBS = "/api/v1/tools/api.pdf/jobs"
TOKEN_A, TOKEN_B = "fxt_" + "A" * 43, "fxt_" + "B" * 43
TOKENS = ",".join(
    f"{name}:{hashlib.sha256(t.encode()).hexdigest()}"
    for name, t in [("a", TOKEN_A), ("b", TOKEN_B)]
)
A, B = {"Authorization": f"Bearer {TOKEN_A}"}, {"Authorization": f"Bearer {TOKEN_B}"}


@pytest.fixture
def c(make_client, online):
    return make_client(auth="", api_tokens=TOKENS)


def create(c, headers) -> str:
    res = c.post(JOBS, files=[("files", ("a.pdf", PDF, "application/pdf"))], headers=headers)
    assert res.status_code == 202, res.text
    return res.json()["id"]


def finish(r, data, job_id):
    """Make the job look like the runner finished it with one result."""
    out = JobPaths(data, job_id).output(0, "pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(PDF)
    outputs = [{"index": 0, "format": "pdf", "size": len(PDF), "role": "RESULT"}]
    r.hset(job_key(job_id), mapping={"status": "SUCCEEDED", "outputs": json.dumps(outputs)})


# --- the owner is recorded at creation


def test_owner_and_index_are_written_with_the_job(c, r):
    job_id = create(c, A)
    assert r.get(own_key(job_id)) == "token:a"
    assert QUEUED_CAP_S < r.ttl(own_key(job_id)) <= OWNER_TTL_S  # never expires before the job
    assert r.zscore(ownidx_key("token:a"), job_id) is not None
    assert 0 < r.ttl(ownidx_key("token:a")) <= OWNER_TTL_S
    assert not r.exists(ownidx_key("token:b"))


def test_anonymous_mode_has_one_shared_owner(client, r):
    job_id = create(client, {})
    assert r.get(own_key(job_id)) == "anonymous"
    assert [j["id"] for j in client.get("/api/v1/jobs").json()["items"]] == [job_id]


# --- another principal's job looks exactly like a missing one


def test_other_principal_gets_404_on_every_job_route(c, r, data):
    job_id = create(c, A)
    finish(r, data, job_id)
    missing = "j_" + "Z" * 22
    for path in ("", "/result", "/outputs/0"):
        assert c.get(f"/api/v1/jobs/{job_id}{path}", headers=A).status_code == 200
        theirs = c.get(f"/api/v1/jobs/{job_id}{path}", headers=B)
        gone = c.get(f"/api/v1/jobs/{missing}{path}", headers=B)
        assert theirs.status_code == gone.status_code == 404
        assert theirs.json() == gone.json()  # no existence oracle
    assert c.delete(f"/api/v1/jobs/{job_id}", headers=B).status_code == 204
    assert r.hget(job_key(job_id), "status") == "SUCCEEDED"  # untouched
    assert JobPaths(data, job_id).output(0, "pdf").exists()


def test_owner_field_inside_the_job_hash_is_ignored(c, r):
    job_id = create(c, A)
    r.hset(job_key(job_id), mapping={"owner": "token:b", "own": "token:b"})  # as a worker could
    assert c.get(f"/api/v1/jobs/{job_id}", headers=B).status_code == 404
    assert c.get(f"/api/v1/jobs/{job_id}", headers=A).status_code == 200


def test_job_without_an_owner_key_is_404(c, r):
    job_id = create(c, A)
    r.delete(own_key(job_id))
    assert c.get(f"/api/v1/jobs/{job_id}", headers=A).status_code == 404


def test_deleting_a_finished_job_removes_its_ownership_keys(c, r, data):
    job_id = create(c, A)
    finish(r, data, job_id)
    assert c.delete(f"/api/v1/jobs/{job_id}", headers=A).status_code == 204
    assert not r.exists(job_key(job_id), own_key(job_id))
    assert r.zscore(ownidx_key("token:a"), job_id) is None


# --- GET /jobs


def test_list_shows_only_the_callers_jobs_newest_first(c, r):
    a1, b1, a2 = create(c, A), create(c, B), create(c, A)
    r.zadd(ownidx_key("token:a"), {a1: 1000, a2: 2000})  # deterministic creation order
    res = c.get("/api/v1/jobs", headers=A)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert [j["id"] for j in body["items"]] == [a2, a1] and body["nextBefore"] is None
    assert body["items"][0]["tool"] == "api.pdf" and body["items"][0]["status"] == "QUEUED"
    assert [j["id"] for j in c.get("/api/v1/jobs", headers=B).json()["items"]] == [b1]


def test_list_pages_with_the_before_cursor(c, r):
    ids = [create(c, A) for _ in range(3)]
    r.zadd(ownidx_key("token:a"), {j: 1000 * (n + 1) for n, j in enumerate(ids)})
    first = c.get("/api/v1/jobs?limit=2", headers=A).json()
    assert [j["id"] for j in first["items"]] == [ids[2], ids[1]] and first["nextBefore"] == 2000
    second = c.get(f"/api/v1/jobs?limit=2&before={first['nextBefore']}", headers=A).json()
    assert [j["id"] for j in second["items"]] == [ids[0]] and second["nextBefore"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "before=-1", "before=x"])
def test_list_rejects_bad_paging(c, query):
    assert c.get(f"/api/v1/jobs?{query}", headers=A).status_code == 422


def test_expired_jobs_are_dropped_from_the_index_on_read(c, r):
    old, kept, newest = create(c, A), create(c, A), create(c, A)
    r.zadd(ownidx_key("token:a"), {old: 1000, kept: 2000, newest: 3000})
    r.delete(job_key(newest), job_key(old))  # their hashes expired
    body = c.get("/api/v1/jobs?limit=1", headers=A).json()
    assert [j["id"] for j in body["items"]] == [kept]  # the page is still filled
    assert r.zscore(ownidx_key("token:a"), newest) is None
    assert c.get("/api/v1/jobs", headers=A).json()["items"] == [
        c.get(f"/api/v1/jobs/{kept}", headers=A).json()
    ]
    assert r.zrange(ownidx_key("token:a"), 0, -1) == [kept]


def test_index_entry_of_someone_elses_job_is_never_listed(c, r):
    theirs = create(c, B)
    r.zadd(ownidx_key("token:a"), {theirs: 1})  # a corrupted index still shows nothing
    assert c.get("/api/v1/jobs", headers=A).json()["items"] == []


def test_index_is_capped_to_the_newest_entries(c, r, monkeypatch):
    from filex.api import routes

    monkeypatch.setattr(routes, "OWNIDX_CAP", 2)
    ids = [create(c, A) for _ in range(3)]
    assert r.zcard(ownidx_key("token:a")) == 2 and r.zscore(ownidx_key("token:a"), ids[0]) is None
    assert c.get(f"/api/v1/jobs/{ids[0]}", headers=A).status_code == 200  # still reachable


# --- every {jobId} route is guarded


ALLOWED_WITHOUT_REQUIRE_JOB = {("DELETE", "/api/v1/jobs/{jobId}")}  # 204 when gone: find_job
# No principal at all: the HMAC signature is the capability (tests/api/test_download_links.py).
SIGNED = {("GET", "/api/v1/dl/{jobId}/{target}")}


def _calls(dependant) -> set:
    found = {dependant.call}
    for sub in dependant.dependencies:
        found |= _calls(sub)
    return found


def unguarded(app: FastAPI) -> list[str]:
    """``{jobId}`` routes whose dependency tree lacks ``require_job`` (DELETE: ``find_job``).
    Walks the expanded route table the way ``get_openapi`` does (FastAPI 0.143 keeps included
    routers as ``_IncludedRouter`` objects in ``app.routes``)."""
    bad, seen = [], 0
    for ctx in iter_route_contexts(app.routes):
        route = _get_api_route_for_openapi(ctx)
        if route is None or "{jobId}" not in route.path_format:
            continue
        seen += 1
        calls = _calls(route.dependant)
        for method in route.methods:
            if (method, route.path_format) in SIGNED:
                continue
            if (method, route.path_format) in ALLOWED_WITHOUT_REQUIRE_JOB:
                ok = find_job in calls
            else:
                ok = require_job in calls
            if not ok:
                bad.append(f"{method} {route.path_format}")
    assert seen >= 4, "the walk found no job routes; it is not looking at the right table"
    return bad


def test_every_job_route_requires_the_owner(tools):
    assert unguarded(create_app(Settings(auth="none"), tools=tools)) == []


def test_route_walk_catches_a_job_route_without_require_job(tools):
    app = create_app(Settings(auth="none"), tools=tools)
    extra = APIRouter(prefix="/api/v1")

    @extra.get("/jobs/{jobId}/leak")
    def leak(job_id: JobIdParam) -> dict:
        return {"id": job_id}

    app.include_router(extra)
    assert unguarded(app) == ["GET /api/v1/jobs/{jobId}/leak"]
