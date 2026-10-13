"""Outputs are opened without following links and served from that descriptor (Task 61,
review H-1), and worker-written job fields cannot make a view answer 500 (review L-6)."""

import io
import json
import os
import zipfile

import pytest

from filex.api import safe_files
from filex.jobs import JobPaths, job_key

PDF = b"%PDF-1.7\n%output files\n"
SECRET = b"REDIS_PASSWORD=hunter2\n"


def finished(client, r, data, n=1) -> str:
    """A SUCCEEDED job with ``n`` RESULT outputs ``out/<i>.pdf``."""
    res = client.post("/api/v1/tools/api.pdf/jobs", files=[("files", ("a.pdf", PDF, "x/y"))])
    job_id = res.json()["id"]
    paths = JobPaths(data, job_id)
    paths.out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        paths.output(i, "pdf").write_bytes(PDF)
    outputs = [{"index": i, "format": "pdf", "size": len(PDF), "role": "RESULT"} for i in range(n)]
    r.hset(job_key(job_id), mapping={"status": "SUCCEEDED", "outputs": json.dumps(outputs)})
    return job_id


@pytest.fixture
def secret(tmp_path):
    path = tmp_path / "api-container-secret"
    path.write_bytes(SECRET)
    return path


def symlink_or_skip(target, link, is_dir=False):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as e:  # Windows without the symlink privilege
        pytest.skip(f"cannot create symlinks here: {e}")


def all_routes(client, job_id):
    link = client.post(f"/api/v1/jobs/{job_id}/outputs/0/link").json()["url"]
    return {
        "result": client.get(f"/api/v1/jobs/{job_id}/result"),
        "output": client.get(f"/api/v1/jobs/{job_id}/outputs/0"),
        "dl": client.get(link),
    }


def assert_all_404(client, job_id):
    for route, res in all_routes(client, job_id).items():
        assert res.status_code == 404, route
        assert SECRET not in res.content, route


def test_a_symlinked_output_is_404_on_every_route(client, r, data, secret):
    job_id = finished(client, r, data)
    out = JobPaths(data, job_id).output(0, "pdf")
    out.unlink()
    symlink_or_skip(secret, out)
    assert_all_404(client, job_id)


def test_a_symlinked_out_dir_is_404(client, r, data, secret, tmp_path):
    job_id = finished(client, r, data)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "0.pdf").write_bytes(SECRET)
    paths = JobPaths(data, job_id)
    for f in paths.out_dir.iterdir():
        f.unlink()
    paths.out_dir.rmdir()
    symlink_or_skip(elsewhere, paths.out_dir, is_dir=True)
    assert_all_404(client, job_id)


def test_a_symlinked_result_zip_is_never_followed(client, r, data, secret):
    job_id = finished(client, r, data, n=2)
    symlink_or_skip(secret, JobPaths(data, job_id).dir / "result.zip")
    res = client.get(f"/api/v1/jobs/{job_id}/result")
    assert SECRET not in res.content
    assert res.status_code == 200  # the link is replaced by a freshly built zip
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert [zf.read(n) for n in zf.namelist()] == [PDF, PDF]


def test_a_swap_between_check_and_open_is_404(client, r, data, secret, monkeypatch):
    """Runs on every platform: the open lands on another file than the one checked, as if a
    symlink had been swapped in after the lstat (the kernel's O_NOFOLLOW is not needed)."""
    job_id = finished(client, r, data)
    real_open = safe_files._open

    def swapped(path, flags, *args, **kwargs):
        if str(path).endswith("0.pdf") and not flags & os.O_CREAT:
            return real_open(secret, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(safe_files, "_open", swapped)
    assert_all_404(client, job_id)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_output_is_404_without_blocking(client, r, data):
    job_id = finished(client, r, data)
    out = JobPaths(data, job_id).output(0, "pdf")
    out.unlink()
    os.mkfifo(out)
    assert_all_404(client, job_id)


def test_an_output_with_a_third_hard_link_is_404(client, r, data, tmp_path):
    job_id = finished(client, r, data)
    out = JobPaths(data, job_id).output(0, "pdf")
    os.link(out, tmp_path / "second")
    assert client.get(f"/api/v1/jobs/{job_id}/outputs/0").status_code == 200  # steps/ + out/
    os.link(out, tmp_path / "third")
    assert_all_404(client, job_id)


def test_bytes_come_from_the_checked_descriptor_and_ranges_work(client, r, data):
    job_id = finished(client, r, data)
    res = client.get(f"/api/v1/jobs/{job_id}/outputs/0", headers={"range": "bytes=2-5"})
    assert res.status_code == 206 and res.content == PDF[2:6]
    assert res.headers["content-range"] == f"bytes 2-5/{len(PDF)}"
    assert res.headers["content-length"] == "4"
    several = client.get(f"/api/v1/jobs/{job_id}/outputs/0", headers={"range": "bytes=0-1,4-5"})
    assert several.status_code == 200 and several.content == PDF  # allowed to ignore Range


# --- review L-6: worker-written fields


@pytest.mark.parametrize(
    "fields",
    [
        {"outputs": json.dumps([{"index": "0", "format": "pdf", "size": 1, "role": "RESULT"}])},
        {"outputs": json.dumps([{"index": 0, "format": "exe", "size": 1, "role": "RESULT"}])},
        {"outputs": json.dumps([{"index": 1, "format": "pdf", "size": 1, "role": "RESULT"}])},
        {"outputs": json.dumps(["x"])},
        {"outputs": json.dumps({"index": 0})},
        {"status": "PWNED", "progress": json.dumps("x")},
        {"step:0": json.dumps({"status": 7, "progress": [1], "message": {"a": 1}})},
        {"step:0": json.dumps("x"), "warnings": json.dumps([{"code": 1}, "x"])},
        {"error": json.dumps({"code": ["x"]}), "steps": json.dumps(["x", {"tool": 5}])},
        {"inputs": json.dumps([{"name": "a"}, 3])},
    ],
)
def test_malformed_worker_fields_never_make_a_view_500(client, r, data, fields):
    job_id = finished(client, r, data)
    r.hset(job_key(job_id), mapping=fields)
    job = client.get(f"/api/v1/jobs/{job_id}")
    assert job.status_code == 200, job.text
    assert client.get("/api/v1/jobs").status_code == 200
    for path in ("/result", "/outputs/0"):
        assert client.get(f"/api/v1/jobs/{job_id}{path}").status_code in (200, 404, 409)
    if "outputs" in fields:
        assert job.json()["outputs"] == []
