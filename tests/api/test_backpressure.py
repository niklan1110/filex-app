"""Back-pressure on job creation and the janitor in the API lifespan (task 15)."""

import time

from filex.jobs import JobPaths, new_job_id, queue_key

PDF = b"%PDF-1.7\n%api test\n"
JOBS = "/api/v1/tools/api.pdf/jobs"
BODY = [("files", ("a.pdf", PDF, "application/pdf"))]


def no_job_dirs(data):
    return not (data / "jobs").exists() or not any((data / "jobs").iterdir())


def test_full_queue_answers_429_queue_full_before_reading_the_upload(make_client, online, r, data):
    for _ in range(3):
        r.xadd(queue_key("core"), {"job": new_job_id(), "step": "0"})
    res = make_client(max_queued=3).post(JOBS, files=BODY)
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "30"
    assert res.json()["error"]["code"] == "QUEUE_FULL"
    assert res.json()["error"]["details"] == {"group": "core", "limit": 3}
    assert no_job_dirs(data) and not r.keys("job:*")


def test_queue_below_the_cap_accepts_the_job(make_client, online, r):
    r.xadd(queue_key("core"), {"job": new_job_id(), "step": "0"})
    assert make_client(max_queued=2).post(JOBS, files=BODY).status_code == 202


def test_low_disk_answers_507_with_the_error_envelope(make_client, online, r, data):
    res = make_client(min_free_mb=1 << 40).post(JOBS, files=BODY)  # a petabyte free: never
    assert res.status_code == 507
    assert res.json()["error"]["code"] == "INSUFFICIENT_STORAGE"
    assert res.headers["Cache-Control"] == "no-store"
    assert no_job_dirs(data) and not r.keys("job:*")


def test_disk_filling_up_during_the_upload_answers_507(make_client, online, r, data, monkeypatch):
    """Review L-2: free space is checked again while bytes arrive (chunked uploads declare no
    length, and parallel uploads all pass the check before any of them writes)."""
    from collections import namedtuple

    from filex.api import upload

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(upload, "DISK_CHECK_BYTES", 8)
    monkeypatch.setattr(upload, "disk_usage", lambda _: usage(1, 1, 0))
    res = make_client(min_free_mb=1).post(JOBS, files=BODY)
    assert res.status_code == 507 and res.json()["error"]["code"] == "INSUFFICIENT_STORAGE"
    assert no_job_dirs(data) and not r.keys("job:*")


def test_enough_disk_accepts_the_job(make_client, online):
    assert make_client(min_free_mb=1).post(JOBS, files=BODY).status_code == 202


def test_contract_documents_429_and_507_on_job_creation(make_client):
    op = make_client().get("/openapi.json").json()["paths"]["/api/v1/tools/api.pdf/jobs"]["post"]
    assert "QUEUE_FULL" in op["responses"]["429"]["description"]
    assert "INSUFFICIENT_STORAGE" in op["responses"]["507"]["description"]


def test_janitor_runs_at_startup(make_client, data):
    orphan = JobPaths(data, new_job_id())
    orphan.in_dir.mkdir(parents=True)
    (orphan.in_dir / "0.pdf").write_bytes(PDF)
    with make_client():
        deadline = time.monotonic() + 5
        while orphan.dir.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    assert not orphan.dir.exists()


def test_the_upload_marker_is_gone_once_the_job_hash_exists(make_client, online, r, data):
    """Review L-3: other API processes' janitors keep in/ while ``.uploading`` is there."""
    job_id = make_client().post(JOBS, files=BODY).json()["id"]
    assert r.exists(f"job:{job_id}")
    assert sorted(p.name for p in JobPaths(data, job_id).in_dir.iterdir()) == ["0.pdf"]
