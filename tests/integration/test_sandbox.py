"""Linux-only step sandbox checks (ADR-0007 items 1-3): rlimits, seccomp, escaped-process sweep.

Run inside the worker image (or any Linux with libseccomp) for full coverage; skipped elsewhere.
"""

import ctypes
import json
import os
import signal
import threading
import time

import pytest
from conftest import job, linux_only

from filex.jobs import JobPaths
from filex.worker import sandbox

pytestmark = linux_only


def test_memory_limit_fails_the_job_and_the_worker_keeps_serving(r, make_runner, make_job):
    hog = make_job(("t.hog", {}))
    fine = make_job(("t.copy", {}))
    runner = make_runner()
    runner.run_once()
    assert job(r, hog)["error"]["code"] == "ENGINE_MEMORY"
    runner.run_once()
    assert job(r, fine)["status"] == "SUCCEEDED"


@pytest.fixture
def seccomp():
    if not sandbox.seccomp_available():
        pytest.skip("libseccomp not installed")


def test_network_socket_is_denied(r, make_runner, make_job, seccomp):
    job_id = make_job(("t.net", {"family": "INET"}))
    make_runner().run_once()
    assert job(r, job_id)["error"]["code"] == "ENGINE_FAILED"


def test_unix_socket_still_works(r, make_runner, make_job, seccomp):
    job_id = make_job(("t.net", {"family": "UNIX"}))
    make_runner().run_once()
    assert job(r, job_id)["status"] == "SUCCEEDED"


def test_unix_is_the_only_socket_family(r, make_runner, make_job, seccomp):
    job_id = make_job(("t.net", {"family": "ALG"}))
    make_runner().run_once()
    assert job(r, job_id)["error"]["code"] == "ENGINE_FAILED"


def test_step_cannot_stop_its_runner(r, data, make_runner, make_job, seccomp):
    """Security review H-1. In the image the runner is PID 1, which the kernel protects from
    SIGSTOP/SIGKILL sent inside its namespace; the filter is the second layer, tested here."""
    stop, fine = make_job(("t.stop", {})), make_job(("t.copy", {}))
    runner = make_runner()
    runner.run_once()
    assert job(r, stop)["status"] == "SUCCEEDED"
    info = json.loads(JobPaths(data, stop).output(1, "json").read_text())
    assert info == {"kill": "denied", "pidfd": "denied"}
    runner.run_once()
    assert job(r, fine)["status"] == "SUCCEEDED"


def test_step_is_not_dumpable_and_is_the_oom_killers_first_pick(r, data, make_runner, make_job):
    job_id = make_job(("t.inspect", {}))
    make_runner().run_once()
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info["linux"] == {"dumpable": 0, "oom_score_adj": "1000"}


@pytest.fixture
def subreaper():
    """Make this test's pytest process the runner's subreaper, and undo it afterwards; escaped
    pids the test records are killed in teardown even if the test fails."""
    escaped: list[int] = []
    sandbox.harden_runner()
    yield escaped
    libc = ctypes.CDLL(None)
    libc.prctl(36, 0, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER off
    libc.prctl(4, 1, 0, 0, 0)  # PR_SET_DUMPABLE back on
    for pid in escaped:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _alive(pid)  # reaps it


def test_escaped_process_is_killed_after_the_step(r, data, make_runner, make_job, subreaper):
    job_id = make_job(("t.escape", {}))
    make_runner().run_once()
    assert job(r, job_id)["status"] == "SUCCEEDED"
    pid = json.loads(JobPaths(data, job_id).output(1, "json").read_text())["pid"]
    subreaper.append(pid)
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid)


def test_engine_helper_survives_sweeps_while_the_step_runs(r, make_runner, make_job):
    """Regression: pdf.compress started Ghostscript in a new session, so the periodic sweep
    killed it mid-step. ``t.helper`` runs the same wrapper while this test sweeps nonstop."""
    job_id = make_job(("t.helper", {}))
    runner = make_runner()
    stop = threading.Event()

    def sweep_nonstop():
        while not stop.is_set():
            runner.sweep()
            time.sleep(0.05)

    sweeper = threading.Thread(target=sweep_nonstop)
    sweeper.start()
    try:
        runner.run_once()
    finally:
        stop.set()
        sweeper.join()
    assert job(r, job_id)["status"] == "SUCCEEDED", job(r, job_id).get("error")


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    finally:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
