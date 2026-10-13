"""Worker runner (task 5): hand-off, limits, redelivery, cancel, scratch dir, probes, secrets,
warnings, output checks and the heavy-step slot. Linux-only checks are in test_sandbox.py."""

import json
import logging
import math
import os
import sys
import threading
import time

import pytest
from conftest import PDF, job, real_redis_only

from filex.jobs import GROUP, JobPaths, job_key, queue_key, sec_key
from filex.worker import runner as runner_module
from filex.worker.runner import (
    ENGINES,
    WAITING,
    Config,
    Probe,
    StepRun,
    _display_name,
    container_memory_mb,
    probe,
    version_tuple,
)


def test_two_step_job_crosses_two_group_streams(r, data, make_runner, make_job):
    job_id = make_job(("t.copy", {}), ("t.copy-office", {}))
    core, office = make_runner("core"), make_runner("office", heavy_slots=0)

    assert core.run_once()
    assert job(r, job_id)["status"] == "RUNNING"
    assert r.xlen(queue_key("core")) == 0  # acked and deleted
    assert r.xlen(queue_key("office")) == 1
    assert office.run_once()

    j = job(r, job_id)
    assert j["status"] == "SUCCEEDED" and j["progress"] == 1
    assert j["outputs"] == [{"index": 0, "format": "pdf", "size": len(PDF) + 1, "role": "RESULT"}]
    assert j["step:0"]["status"] == j["step:1"]["status"] == "SUCCEEDED"
    paths = JobPaths(data, job_id)
    assert paths.output(0, "pdf").read_bytes() == PDF + b"0"
    assert not paths.in_dir.exists() and not paths.steps_dir.exists()
    assert not (data / "tmp" / f"{job_id}.0").exists()  # scratch dir removed
    assert 0 < r.ttl(job_key(job_id)) <= 1440 * 60


def test_per_file_tool_runs_once_per_input(r, data, make_runner, make_job):
    job_id = make_job(("t.each", {}), inputs=3)
    make_runner().run_once()
    j = job(r, job_id)
    assert [o["index"] for o in j["outputs"]] == [0, 1, 2]
    assert JobPaths(data, job_id).output(2, "pdf").read_bytes() == PDF + b"2"


def test_timeout_fails_the_job_and_the_worker_keeps_serving(r, make_runner, make_job):
    slow = make_job(("t.sleep", {"seconds": 30}))
    fine = make_job(("t.copy", {}))
    runner = make_runner()
    started = time.monotonic()
    runner.run_once()
    assert time.monotonic() - started < 10
    assert job(r, slow)["error"] == {
        "code": "ENGINE_TIMEOUT",
        "message": "The tool took longer than its 2 s limit.",
        "step": 0,
    }
    assert job(r, slow)["status"] == "FAILED"
    runner.run_once()
    assert job(r, fine)["status"] == "SUCCEEDED"


def test_tool_error_code_is_kept(r, make_runner, make_job):
    job_id = make_job(("t.inspect", {"fail": True}))
    make_runner().run_once()
    assert job(r, job_id)["error"]["code"] == "INVALID_INPUT"


def test_lost_worker_redelivers_once_then_fails_with_worker_lost(r, make_runner, make_job):
    job_id = make_job(("t.copy", {}))
    r.xgroup_create(queue_key("core"), GROUP, id="0", mkstream=True)
    r.xreadgroup(GROUP, "dead-1", {queue_key("core"): ">"}, count=1)  # delivery 1, then "dies"
    runner = make_runner(claim_idle_ms=0)
    assert runner.run_once()  # reclaims it: delivery 2
    assert job(r, job_id)["status"] == "SUCCEEDED"

    lost = make_job(("t.copy", {}))
    r.xreadgroup(GROUP, "dead-1", {queue_key("core"): ">"}, count=1)  # delivery 1
    r.xautoclaim(queue_key("core"), GROUP, "dead-2", 0, "0-0", count=1)  # delivery 2, "dies"
    assert runner.run_once()  # delivery 3
    assert job(r, lost)["error"]["code"] == "WORKER_LOST"
    assert r.xlen(queue_key("core")) == 0


def test_cancel_kills_the_running_step_quickly(r, data, make_runner, make_job):
    job_id = make_job(("t.sleep", {"seconds": 1}))
    r.hset(job_key(job_id), "steps", json.dumps([{"tool": "t.sleep", "options": {"seconds": 60}}]))
    runner = make_runner()
    t = threading.Thread(target=runner.run_once)
    t.start()
    deadline = time.monotonic() + 10
    while job(r, job_id).get("status") != "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.05)
    r.hset(job_key(job_id), "status", "CANCELLED")
    cancelled_at = time.monotonic()
    t.join(5)
    assert not t.is_alive() and time.monotonic() - cancelled_at < 2
    assert job(r, job_id)["status"] == "CANCELLED"
    assert "outputs" not in job(r, job_id)
    assert r.xlen(queue_key("core")) == 0
    assert not (data / "tmp" / f"{job_id}.0").exists()
    paths = JobPaths(data, job_id)  # the runner owns the cleanup of a cancelled job
    assert not paths.in_dir.exists() and not paths.steps_dir.exists()


def test_deleted_job_is_dropped_without_recreating_its_hash(r, data, make_runner, make_job):
    job_id = make_job(("t.copy", {}))
    r.delete(job_key(job_id))
    assert make_runner().run_once()
    assert not r.exists(job_key(job_id)) and r.xlen(queue_key("core")) == 0
    assert not JobPaths(data, job_id).in_dir.exists()


def test_queued_job_cancelled_before_it_runs_is_cleaned_up(r, data, make_runner, make_job):
    job_id = make_job(("t.copy", {}), ("t.inspect", {}))
    spec = json.loads(r.hget(job_key(job_id), "steps"))
    spec[1]["secret"] = True
    r.hset(job_key(job_id), mapping={"steps": json.dumps(spec), "status": "CANCELLED"})
    r.set(sec_key(job_id, 1), json.dumps({"password": "later-step-secret"}))
    assert make_runner().run_once()
    paths = JobPaths(data, job_id)
    assert not paths.in_dir.exists() and not paths.steps_dir.exists()
    assert not r.exists(sec_key(job_id, 1))
    assert job(r, job_id)["status"] == "CANCELLED" and r.xlen(queue_key("core")) == 0


def test_cancel_while_the_last_step_links_its_outputs_drops_them(r, data, make_runner, make_job):
    job_id = make_job(("t.copy", {}))
    runner = make_runner()
    real = runner._write

    def cancel_first(job_id_, fields, **kw):
        if fields.get("status") == "SUCCEEDED":  # out/ is linked, the final write comes next
            r.hset(job_key(job_id_), "status", "CANCELLED")
        return real(job_id_, fields, **kw)

    runner._write = cancel_first
    runner.run_once()
    paths = JobPaths(data, job_id)
    assert job(r, job_id)["status"] == "CANCELLED" and "outputs" not in job(r, job_id)
    assert not paths.out_dir.exists() and not paths.in_dir.exists()
    assert not paths.steps_dir.exists()


def test_duplicate_delivery_does_not_hand_off_twice(r, make_runner, make_job):
    job_id = make_job(("t.copy", {}), ("t.copy-office", {}))
    core = make_runner("core")
    assert core.run_once()
    r.xadd(queue_key("core"), {"job": job_id, "step": "0"})  # a stalled worker's copy
    assert core.run_once()
    assert r.xlen(queue_key("office")) == 1 and r.xlen(queue_key("core")) == 0
    assert job(r, job_id)["status"] == "RUNNING"


@pytest.mark.parametrize(("value", "minutes"), [("0", 1), ("-5", 1), ("90", 90), ("9999", 1440)])
def test_retention_is_clamped_to_at_least_one_minute(monkeypatch, value, minutes):
    monkeypatch.setenv("FILEX_RETENTION_MIN", value)
    assert Config.from_env("core").retention_s == minutes * 60


def test_scratch_dir_holds_only_the_input_and_env_is_allowlisted(r, data, make_runner, make_job):
    job_id = make_job(("t.inspect", {}))
    make_runner().run_once()
    j = job(r, job_id)
    assert j["status"] == "SUCCEEDED", j
    assert j["outputs"][1]["role"] == "REPORT" and j["outputs"][1]["format"] == "json"
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info["listing"] == ["0.pdf"]
    assert info["home"] == info["cwd"]
    allowed = {"PATH", "LANG", "HOME", "TMPDIR", "OMP_THREAD_LIMIT", "PYTHONDONTWRITEBYTECODE"}
    allowed |= {"PYTHONPATH"}  # tests only: the dummy tools live outside the package
    if os.name == "nt":
        allowed |= {"SYSTEMROOT", "TEMP", "TMP"}
    assert set(info["env"]) - allowed <= {"LC_CTYPE"}  # macOS adds LC_CTYPE itself


def test_probe_below_minimum_marks_the_tool_unavailable(r, make_runner, make_job):
    old = Probe("2.0", cmd=(sys.executable, "-c", "print('oldtool 1.4.2')"))
    runner = make_runner(probes={"oldtool": old})
    assert "t.probed" not in runner.tools and "t.copy" in runner.tools
    runner.heartbeat()
    beat = json.loads(r.get(f"worker:core:{runner.id}"))
    assert "t.probed" not in beat["tools"] and beat["engines"] == {}
    assert 0 < r.ttl(f"worker:core:{runner.id}") <= 30

    job_id = make_job(("t.probed", {}))
    runner.run_once()
    assert job(r, job_id)["error"]["code"] == "TOOL_UNAVAILABLE"

    new = Probe("1.4", cmd=(sys.executable, "-c", "print('oldtool 1.4.2')"))
    assert "t.probed" in make_runner(probes={"oldtool": new}).tools


def test_pikepdf_probe_reports_the_installed_version():
    pikepdf = pytest.importorskip("pikepdf")
    assert probe(ENGINES["pikepdf"]) == pikepdf.__version__


def test_step_gets_display_names_and_the_page_limit(r, data, make_runner, make_job):
    job_id = make_job(("t.inspect", {}), names=["Re\u0301sume\u0301\u202e.pdf\x07"])
    make_runner(max_pages=7, max_pixels=1234).run_once()
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info["input_names"] == ["R\u00e9sum\u00e9.pdf"]  # NFC, no bidi or control chars
    assert info["max_pages"] == 7
    assert info["max_pixels"] == 1234


def test_display_names_are_capped_and_never_missing():
    assert _display_name("x" * 300) == "x" * 200
    assert _display_name(None) == "" and _display_name(42) == ""


def test_max_pages_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("FILEX_MAX_PAGES", "50")
    assert Config.from_env("core").max_pages == 50
    monkeypatch.delenv("FILEX_MAX_PAGES")
    assert Config.from_env("core").max_pages == 2000


def test_max_pixels_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("FILEX_MAX_PIXELS", "50000000")
    assert Config.from_env("core").max_pixels == 50_000_000
    monkeypatch.delenv("FILEX_MAX_PIXELS")
    assert Config.from_env("core").max_pixels == 100_000_000


def test_version_tuple():
    assert version_tuple("GPL Ghostscript 10.03.1 (2024)") == (10, 3, 1, 2024)
    assert version_tuple("10.03.1") > version_tuple("10.2") and version_tuple("9.56") < (10,)


def test_memory_is_clamped_to_the_container_limit(tmp_path):
    f = tmp_path / "memory.max"
    f.write_text("1073741824\n")
    assert container_memory_mb([str(f)]) == 1024
    f.write_text("max\n")
    assert container_memory_mb([str(f)]) is None


def test_secret_reaches_the_step_on_stdin_and_nowhere_else(r, data, make_runner, make_job):
    import hashlib

    secret = "correct horse battery staple"
    job_id = make_job(("t.inspect", {}), secret={"password": secret})
    make_runner().run_once()
    j = job(r, job_id)
    assert j["status"] == "SUCCEEDED", j
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info["secret_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
    assert not any(info["secret_in_proc"].values())  # Linux: not in cmdline or environ
    assert not r.exists(sec_key(job_id, 0))
    for key in r.keys("*"):
        dump = r.dump(key) or b""
        assert secret.encode() not in dump, key


def test_redelivered_step_without_its_secret_fails(r, make_runner, make_job):
    job_id = make_job(("t.inspect", {}), secret={"password": "hunter22hunter22"})
    r.delete(sec_key(job_id, 0))  # consumed by a worker that then died
    make_runner().run_once()
    assert job(r, job_id)["error"]["code"] == "SECRET_UNAVAILABLE"


@pytest.mark.parametrize("fail", [False, True])
def test_sensitive_aux_file_is_deleted_after_the_step(r, data, make_runner, make_job, fail):
    job_id = make_job(("t.inspect", {"fail": fail}), aux={"certificate": ("p12", b"0\x82key")})
    paths = JobPaths(data, job_id)
    runner = make_runner()
    seen = []
    real = runner._spawn

    def spy(*a, **kw):
        seen.append(paths.aux(slot="certificate", fmt="p12").exists())
        return real(*a, **kw)

    runner._spawn = spy
    runner.run_once()
    assert seen == [True]
    assert not paths.aux("certificate", "p12").exists()
    assert job(r, job_id)["status"] == ("FAILED" if fail else "SUCCEEDED")
    if not fail:
        info = json.loads(paths.output(1, "json").read_text())
        assert info["aux"] == {"certificate": "certificate.p12"}
        assert info["listing"] == ["0.pdf", "certificate.p12"]


def test_warnings_are_capped_and_reports_keep_their_role(r, make_runner, make_job, caplog):
    job_id = make_job(("t.warn", {}))
    runner = make_runner()
    seen = []
    real = runner._finish

    def spy(run, outputs, warnings):
        seen.append(len(warnings))
        return real(run, outputs, warnings)

    runner._finish = spy
    with caplog.at_level(logging.WARNING, logger="filex.worker"):
        runner.run_once()
    assert seen == [20]  # capped while streaming, not only at the end
    assert sum("undeclared warning" in m for m in caplog.messages) == 1
    j = job(r, job_id)
    assert len(j["warnings"]) == 20
    assert j["warnings"][0] == {"code": "LONG", "message": "x" * 300, "step": 0}
    assert all(w["code"] != "UNDECLARED" for w in j["warnings"])
    assert [o["role"] for o in j["outputs"]] == ["RESULT", "REPORT"]


@pytest.mark.parametrize("mode", ["SYMLINK", "HARDLINK", "OUTSIDE"])
def test_linked_or_foreign_outputs_fail_the_job(r, data, tmp_path, make_runner, make_job, mode):
    if mode == "SYMLINK":
        try:
            (tmp_path / "probe").symlink_to(tmp_path)
        except OSError:
            pytest.skip("creating symlinks needs a privilege on Windows")
    job_id = make_job(("t.link", {"mode": mode}))
    make_runner().run_once()
    j = job(r, job_id)
    assert j["error"]["code"] == "INVALID_OUTPUT", j
    assert not JobPaths(data, job_id).out_dir.exists()


def test_child_cannot_set_nan_progress_or_a_runner_error_code(r, make_runner, make_job):
    job_id = make_job(("t.liar", {"code": "WORKER_LOST"}))
    make_runner().run_once()
    j = job(r, job_id)
    assert math.isfinite(j["progress"])
    assert j["error"]["code"] == "ENGINE_FAILED"


@pytest.mark.parametrize(
    ("code", "kept"),
    [("WRONG_PASSWORD", "WRONG_PASSWORD"), ("INVALID_INPUT", "INVALID_INPUT")],
)
def test_declared_and_common_error_codes_are_kept(r, make_runner, make_job, code, kept):
    job_id = make_job(("t.liar", {"code": code}))
    make_runner().run_once()
    assert job(r, job_id)["error"]["code"] == kept


def test_undeclared_tool_error_code_becomes_engine_failed(r, make_runner, make_job):
    job_id = make_job(("t.liar", {"code": "INVALID_PDF"}))  # another tool's code
    make_runner().run_once()
    assert job(r, job_id)["error"]["code"] == "ENGINE_FAILED"


def test_total_output_size_is_capped(r, make_runner, make_job):
    job_id = make_job(("t.copy", {}))
    make_runner(max_output_bytes=8).run_once()
    assert job(r, job_id)["error"]["code"] == "OUTPUT_TOO_LARGE"


def test_step_is_stopped_when_the_data_disk_is_nearly_full(r, make_runner, make_job):
    job_id = make_job(("t.sleep", {"seconds": 1}))
    started = time.monotonic()
    make_runner(min_free_mb=1 << 40).run_once()
    assert job(r, job_id)["error"]["code"] == "OUTPUT_TOO_LARGE"
    assert time.monotonic() - started < 5


def test_reader_start_failure_still_kills_the_step(r, make_runner, make_job, monkeypatch):
    make_job(("t.sleep", {"seconds": 30}))
    runner = make_runner()
    procs = []
    real_popen = runner_module.subprocess.Popen

    def popen(*a, **kw):
        procs.append(real_popen(*a, **kw))
        return procs[-1]

    def no_threads(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(runner_module.subprocess, "Popen", popen)
    monkeypatch.setattr(runner_module.threading.Thread, "start", no_threads)
    with pytest.raises(RuntimeError):
        runner.run_once()
    assert procs and procs[0].poll() is not None
    assert runner._running == set()


def test_worker_without_seccomp_advertises_no_tools(r, make_runner, monkeypatch):
    monkeypatch.setattr(runner_module.sandbox, "LINUX", True)
    monkeypatch.setattr(runner_module.sandbox, "seccomp_available", lambda: False)
    runner = make_runner()
    runner.heartbeat()
    assert runner.tools == {}
    assert json.loads(r.get(f"worker:core:{runner.id}"))["tools"] == []


def test_two_worker_threads_run_two_jobs_at_once(r, make_runner, make_job):
    jobs = [make_job(("t.sleep", {"seconds": 1})) for _ in range(2)]
    runner = make_runner(concurrency=2)
    t = threading.Thread(target=runner.serve)
    t.start()
    overlap, states, deadline = False, [], time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            states = [job(r, j).get("status") for j in jobs]
            overlap |= states == ["RUNNING", "RUNNING"]
            if states == ["SUCCEEDED", "SUCCEEDED"]:
                break
            time.sleep(0.05)
    finally:
        runner.stop.set()
        t.join(10)
    assert states == ["SUCCEEDED", "SUCCEEDED"] and overlap


@real_redis_only
def test_autoclaim_reaches_a_dead_entry_behind_many_live_ones(r, make_runner, make_job):
    """Redis 7 scans at most COUNT*10 PEL entries per XAUTOCLAIM, so the runner must keep the
    cursor. fakeredis has no scan cap, so this means something only against a real Redis."""
    ids = [make_job(("t.copy", {})) for _ in range(16)]
    r.xgroup_create(queue_key("core"), GROUP, id="0", mkstream=True)
    entries = r.xreadgroup(GROUP, "dead", {queue_key("core"): ">"}, count=16)[0][1]
    time.sleep(1.1)
    live = [e[0] for e in entries[:15]]
    r.xclaim(queue_key("core"), GROUP, "live", 0, live, justid=True)  # touched just now
    runner = make_runner(claim_idle_ms=1000)
    got = [runner._next_entry(10) for _ in range(3)]
    assert any(g and g[1]["job"] == ids[15] for g in got)


def test_forged_and_malformed_stream_entries(r, data, make_runner, make_job):
    job_id = make_job(("t.copy", {}))
    r.xadd(queue_key("core"), {"job": "../../etc", "step": "0"})
    r.xadd(queue_key("core"), {"job": job_id, "step": "nope"})
    runner = make_runner()
    while runner.run_once():
        pass
    assert job(r, job_id)["status"] == "SUCCEEDED"
    assert r.xlen(queue_key("core")) == 0


def test_stream_and_group_are_recreated_after_a_redis_restart(r, make_runner, make_job):
    runner = make_runner()
    runner.ensure_group()
    r.delete(queue_key("core"))  # what a Redis restart without persistence looks like
    assert not runner.run_once()
    job_id = make_job(("t.copy", {}))
    assert runner.run_once()
    assert job(r, job_id)["status"] == "SUCCEEDED"


def test_heavy_step_waits_for_the_slot_without_a_delivery_bump(r, make_runner, make_job):
    r.set("slot:heavy:0", "someone-else", px=30_000)
    job_id = make_job(("t.copy-office", {}), group="office")
    office = make_runner("office", heavy_slots=1)
    t = threading.Thread(target=office.run_once)
    t.start()
    deadline = time.monotonic() + 5
    while "step:0" not in job(r, job_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    j = job(r, job_id)
    assert j["status"] == "QUEUED" and j["step:0"]["message"] == WAITING
    pending = r.xpending_range(queue_key("office"), GROUP, "-", "+", 10)
    assert pending[0]["times_delivered"] == 1
    r.delete("slot:heavy:0")
    t.join(10)
    assert job(r, job_id)["status"] == "SUCCEEDED"
    assert r.get("slot:heavy:0") is None  # released


def test_cancel_while_waiting_for_the_slot(r, data, make_runner, make_job):
    r.set("slot:heavy:0", "someone-else", px=30_000)
    job_id = make_job(("t.copy-office", {}), group="office")
    office = make_runner("office", heavy_slots=1)
    t = threading.Thread(target=office.run_once)
    t.start()
    deadline = time.monotonic() + 5
    while "step:0" not in job(r, job_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    r.hset(job_key(job_id), "status", "CANCELLED")
    t.join(5)
    assert not t.is_alive()
    assert job(r, job_id)["status"] == "CANCELLED" and r.xlen(queue_key("office")) == 0
    assert not JobPaths(data, job_id).in_dir.exists()
    assert r.get("slot:heavy:0") == "someone-else"


def test_slot_is_renewed_and_released_only_by_its_holder(r, make_runner):
    office = make_runner("office", heavy_slots=1)
    r.set("slot:heavy:0", "other", px=5_000)
    office._renew_slot("slot:heavy:0", "mine")
    office._release_slot("slot:heavy:0", "mine")
    assert r.get("slot:heavy:0") == "other" and r.pttl("slot:heavy:0") <= 5_000


def test_heavy_slot_is_a_lease_and_core_never_takes_it(r, make_runner, make_job):
    office = make_runner("office", heavy_slots=1)
    job_id = "j_" + "a" * 22
    run = StepRun("0-0", job_id, 0, [], JobPaths(office.cfg.data, job_id))
    key, token = office._take_slot(run)
    assert key == "slot:heavy:0" and 0 < r.pttl(key) <= 30_000
    office._release_slot(key, token)
    assert r.get(key) is None

    r.set("slot:heavy:0", "busy", px=30_000)
    job_id = make_job(("t.copy", {}))
    make_runner("core", heavy_slots=1).run_once()
    assert job(r, job_id)["status"] == "SUCCEEDED"

    job_id = make_job(("t.copy-office", {}), group="office")
    make_runner("office", heavy_slots=0).run_once()  # 0 = slot off
    assert job(r, job_id)["status"] == "SUCCEEDED"


# --- output display names and the output cap (Output(), MAX_OUTPUTS)


def test_tool_given_output_names_are_cleaned_into_the_job_record(r, make_runner, make_job):
    names = [
        "Résumé\u202e.docx",  # NFC, bidi override dropped, extension replaced
        "../../etc/passwd",  # path parts dropped
        "a\\b\\c\x07.pdf",  # Windows path parts and control characters dropped
        ".pdf",  # nothing left: no name, the API falls back to the input's
        "",
        "x" * 300,
    ]
    job_id = make_job(("t.many", {"count": len(names), "names": "|".join(names)}))
    make_runner().run_once()
    j = job(r, job_id)
    assert j["status"] == "SUCCEEDED", j
    shown = [o.get("name") for o in j["outputs"]]
    assert shown == ["Résumé.pdf", "passwd.pdf", "c.pdf", None, None, "x" * 196 + ".pdf"]
    assert [o.get("name") for o in j["step:0"]["outputs"]] == shown
    assert [o["index"] for o in j["outputs"]] == list(range(len(names)))


def test_output_names_drop_characters_windows_forbids():
    assert runner_module.output_name('Q1: "plan"*?<v2>|.docx', "pdf") == "Q1 planv2.pdf"
    assert runner_module.output_name(":*?.pdf", "pdf") == ""


def test_bare_paths_and_reports_carry_no_name(r, make_runner, make_job):
    job_id = make_job(("t.inspect", {}))
    make_runner().run_once()
    assert all("name" not in o for o in job(r, job_id)["outputs"])


def test_next_step_sees_the_names_the_previous_step_gave(r, data, make_runner, make_job):
    job_id = make_job(("t.many", {"count": 1, "names": "Named part.pdf"}), ("t.inspect", {}))
    runner = make_runner()
    runner.run_once()
    runner.run_once()
    info = json.loads(JobPaths(data, job_id).output(1, "json").read_text())
    assert info["input_names"] == ["Named part.pdf"]


def test_more_outputs_than_the_cap_fail_the_step_loudly(
    r, data, make_runner, make_job, monkeypatch
):
    monkeypatch.setattr(runner_module, "MAX_OUTPUTS", 3)
    ok, over = make_job(("t.many", {"count": 3})), make_job(("t.many", {"count": 4}))
    runner = make_runner()
    runner.run_once()
    runner.run_once()
    assert len(job(r, ok)["outputs"]) == 3
    j = job(r, over)
    assert j["status"] == "FAILED"
    assert j["error"]["code"] == "OUTPUT_TOO_LARGE"
    assert j["error"]["message"] == "The tool made more than 3 files, which is the limit."
    assert not JobPaths(data, over).out_dir.exists()


def test_path_fields_in_a_stream_entry_are_ignored(r, data, tmp_path, make_runner, make_job):
    """Task 61: every path comes from the checked job id and the step index, never the entry."""
    job_id = make_job(("t.copy", {}))
    r.delete(queue_key("core"))  # replace make_job's entry with a doctored one
    evil = tmp_path / "evil"
    forged = {"in": str(evil / "in"), "out": str(evil / "out"), "path": "../../etc/passwd"}
    r.xadd(queue_key("core"), {"job": job_id, "step": "0", **forged})
    runner = make_runner()
    runner.ensure_group()
    assert runner.run_once()
    assert job(r, job_id)["status"] == "SUCCEEDED"
    assert JobPaths(data, job_id).output(0, "pdf").read_bytes() == PDF + b"0"
    assert not evil.exists()
