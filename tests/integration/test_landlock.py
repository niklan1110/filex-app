"""Per-step Landlock confinement (ADR-0007 item 11). The policy tests run anywhere; the kernel
tests need Linux with Landlock (the worker images: scripts/ci/image-tests.sh runs them in core)."""

import json

import pytest
from conftest import job, linux_only

from filex.jobs import JobPaths, new_job_id
from filex.worker import sandbox


def test_required_without_landlock_offers_no_tools(make_runner, monkeypatch):
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    runner = make_runner(landlock="required")
    assert runner.tools == {} and runner.landlock == "unavailable"


def test_auto_without_landlock_still_serves_and_says_so(r, make_runner, monkeypatch):
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 0)
    runner = make_runner()
    assert runner.tools and (runner.landlock_abi, runner.landlock) == (0, "unavailable")
    runner.heartbeat()
    beat = json.loads(r.get(next(iter(r.scan_iter("worker:core:*")))))
    assert beat["isolation"]["landlock"] == "unavailable"


def test_off_is_reported(make_runner, monkeypatch):
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: 7)
    assert make_runner(landlock="off").landlock == "off"


@pytest.fixture
def landlock():
    if sandbox.landlock_abi() < sandbox.LANDLOCK_MIN_ABI:
        pytest.skip("kernel without Landlock")


@linux_only
def test_step_cannot_touch_another_jobs_files(r, data, make_runner, make_job, landlock):
    other = JobPaths(data, new_job_id())
    other.in_dir.mkdir(parents=True)
    secret = other.input(0, "pdf")
    secret.write_bytes(b"%PDF-1.7 other user's file")
    job_id = make_job(("t.peek", {"path": str(secret)}))
    runner = make_runner()
    assert runner.landlock.startswith("abi")
    runner.run_once()
    assert job(r, job_id)["status"] == "SUCCEEDED", job(r, job_id).get("error")
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info == {
        "read": "EACCES",
        "list": "EACCES",
        "write": "EACCES",
        "tmp": "EACCES",
        "own_input": "ok",
    }
    assert not (other.in_dir / "planted").exists()


@linux_only
def test_off_lets_the_step_read_it_again(r, data, make_runner, make_job, landlock):
    """The control for the test above: without Landlock the same read succeeds."""
    other = JobPaths(data, new_job_id())
    other.in_dir.mkdir(parents=True)
    secret = other.input(0, "pdf")
    secret.write_bytes(b"%PDF-1.7")
    job_id = make_job(("t.peek", {"path": str(secret)}))
    make_runner(landlock="off").run_once()
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info["read"] == "ok"
