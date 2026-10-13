"""Retention, janitor and back-pressure (task 15, ADR-0008, review M7 fix 1)."""

import asyncio
import logging
import os
import time
from datetime import UTC, datetime

import fakeredis
import pytest
import redis.asyncio as aioredis
from conftest import job

from filex.api import janitor
from filex.api.deps import Settings
from filex.jobs import JobPaths, job_key, new_job_id, ownidx_key
from filex.worker.runner import Config


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def r(server):  # overrides conftest: the janitor's async client must see the same data
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture
def sweep(server, data):
    def run(now=None, busy=(), client=None):
        async def go():
            ar = client or fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            return await janitor.sweep(ar, data, busy, now)

        return asyncio.run(go())

    return run


def orphan(data, job_id=None, files=("in/0.pdf",)):
    paths = JobPaths(data, job_id or new_job_id())
    for f in files:
        (paths.dir / f).parent.mkdir(parents=True, exist_ok=True)
        (paths.dir / f).write_bytes(b"%PDF-1.7\n")
    return paths


def test_outputs_and_hash_are_gone_after_a_one_minute_retention(
    r, data, make_runner, make_job, sweep
):
    job_id = make_job(("t.copy", {}))
    make_runner(retention_s=60).run_once()
    paths = JobPaths(data, job_id)
    assert job(r, job_id)["status"] == "SUCCEEDED" and paths.out_dir.is_dir()
    assert 0 < r.ttl(job_key(job_id)) <= 60
    expires = datetime.strptime(job(r, job_id)["expiresAt"], "%Y-%m-%dT%H:%M:%SZ")
    assert expires.replace(tzinfo=UTC).timestamp() <= time.time() + 61

    assert sweep().removed == 0  # still within retention: nothing goes
    assert paths.out_dir.is_dir()

    done = sweep(now=time.time() + 62)
    assert done.expired == 1
    assert not paths.dir.exists() and not r.exists(job_key(job_id))


@pytest.mark.parametrize(("value", "minutes"), [("2000", 1440), ("0", 1)])
def test_retention_out_of_range_is_clamped_with_a_log_line(monkeypatch, caplog, value, minutes):
    monkeypatch.setenv("FILEX_RETENTION_MIN", value)
    with caplog.at_level(logging.WARNING, logger="filex"):
        assert Settings.from_env().retention_min == minutes
        assert Config.from_env("core").retention_s == minutes * 60
    assert f"FILEX_RETENTION_MIN={value} is outside 1..1440; using {minutes}" in caplog.text


def test_redis_lost_mid_job_orphans_are_removed_in_one_pass(r, data, make_job, sweep):
    job_id = make_job(("t.copy", {}))
    paths = JobPaths(data, job_id)
    paths.step_dir(0).mkdir(parents=True)
    paths.scratch(0).mkdir(parents=True)
    (paths.scratch(0) / "0.pdf").write_bytes(b"x")
    r.zadd(ownidx_key("anonymous"), {job_id: 1})
    r.flushall()  # Redis restarted without persistence
    r.zadd(ownidx_key("anonymous"), {job_id: 1, "j_" + "A" * 22: 2})  # stale index entries

    done = sweep()
    assert not paths.dir.exists() and not paths.scratch(0).exists()
    assert done.removed == 2 and done.unindexed == 2
    assert not r.exists(ownidx_key("anonymous"))


def test_delete_now_raced_by_a_step_leaves_nothing_behind(r, data, sweep):
    paths = orphan(data, files=("out/0.pdf",))  # recreated by a step after the hash was deleted
    sweep()
    assert not paths.dir.exists()


def test_finished_jobs_lose_inputs_and_intermediates(r, data, sweep):
    files = ("in/0.pdf", "steps/0/0.pdf", "out/0.pdf", "result.zip")
    done, cancelled = orphan(data, files=files), orphan(data, files=files)
    later = "2999-01-01T00:00:00Z"
    r.hset(job_key(done.job_id), mapping={"status": "SUCCEEDED", "expiresAt": later})
    r.hset(job_key(cancelled.job_id), mapping={"status": "CANCELLED", "expiresAt": later})
    running = orphan(data, files=files)
    r.hset(job_key(running.job_id), mapping={"status": "RUNNING", "expiresAt": later})
    running.scratch(0).mkdir(parents=True)

    sweep()
    assert not done.in_dir.exists() and not done.steps_dir.exists()
    assert done.out_dir.is_dir() and (done.dir / "result.zip").is_file()
    assert sorted(os.listdir(cancelled.dir)) == []
    assert running.in_dir.is_dir() and running.steps_dir.is_dir() and running.scratch(0).is_dir()


def test_uploads_in_progress_are_left_alone(data, sweep):
    writing = orphan(data, files=("in/0.part",))
    starting = JobPaths(data, new_job_id())
    starting.in_dir.mkdir(parents=True)
    mine = orphan(data, files=("in/0.pdf",))  # renamed, hash not written yet
    stalled = orphan(data, files=("in/0.part",))
    old = time.time() - janitor.UPLOAD_GRACE_S - 5
    os.utime(stalled.in_dir / "0.part", (old, old))
    # Another API process: .part files already renamed, hash not written yet (review L-3).
    other = orphan(data, files=("in/0.pdf", f"in/{janitor.UPLOAD_MARKER}"))
    crashed = orphan(data, files=("in/0.pdf", f"in/{janitor.UPLOAD_MARKER}"))
    os.utime(crashed.in_dir / janitor.UPLOAD_MARKER, (old, old))

    sweep(busy={mine.job_id})
    assert writing.dir.exists() and starting.dir.exists() and mine.dir.exists()
    assert other.dir.exists()
    assert not stalled.dir.exists() and not crashed.dir.exists()


def test_only_real_dirs_named_like_job_ids_are_touched(data, tmp_path, sweep):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    jobs, tmp = data / "jobs", data / "tmp"
    jobs.mkdir(parents=True)
    tmp.mkdir()
    (jobs / "not-a-job").mkdir()
    (tmp / "upload-1234").mkdir()  # other temp files share /data/tmp
    try:
        os.symlink(outside, jobs / new_job_id(), target_is_directory=True)
        os.symlink(outside, tmp / f"{new_job_id()}.0", target_is_directory=True)
        inner = orphan(data)
        os.symlink(outside, inner.in_dir / "link", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs privileges on this system")

    sweep()
    assert (outside / "keep.txt").read_text() == "keep"
    assert (jobs / "not-a-job").is_dir() and (tmp / "upload-1234").is_dir()
    assert not inner.dir.exists()


def test_removals_per_pass_are_bounded(data, sweep, monkeypatch):
    monkeypatch.setattr(janitor, "MAX_REMOVALS", 2)
    for _ in range(3):
        orphan(data)
    assert sweep().removed == 2
    assert len(os.listdir(data / "jobs")) == 1
    assert sweep().removed == 1


def test_unreachable_redis_deletes_nothing(data, sweep):
    paths = orphan(data)
    down = aioredis.Redis(port=1, socket_connect_timeout=0.5)
    with pytest.raises(Exception):  # noqa: B017 (any connection error)
        sweep(client=down)
    assert paths.dir.exists()
