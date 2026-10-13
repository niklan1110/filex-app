"""Signed download links (task 59, ADR-0009 "Downloads", review 5.1)."""

import json
import logging
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import fakeredis
import httpx
import pytest
import uvicorn

from filex.api.auth import ConfigError
from filex.api.deps import Settings, uvicorn_workers
from filex.api.main import create_app
from filex.api.routes import dl_signature
from filex.jobs import JobPaths, job_key

PDF = b"%PDF-1.7\n%link test, long enough to cut into ranges\n"
JOBS = "/api/v1/tools/api.pdf/jobs"
RL = "rl:dl:testclient"  # its own bucket, not rl:auth (review M-2)


def create(c) -> str:
    res = c.post(JOBS, files=[("files", ("a.pdf", PDF, "application/pdf"))])
    assert res.status_code == 202, res.text
    return res.json()["id"]


def finish(r, data, job_id):
    out = JobPaths(data, job_id).output(0, "pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(PDF)
    outputs = [{"index": 0, "format": "pdf", "size": len(PDF), "role": "RESULT"}]
    r.hset(job_key(job_id), mapping={"status": "SUCCEEDED", "outputs": json.dumps(outputs)})


@pytest.fixture
def job(client, r, data) -> str:
    job_id = create(client)
    finish(r, data, job_id)
    return job_id


def mint(client, job_id, target="result") -> str:
    path = "result" if target == "result" else f"outputs/{target}"
    res = client.post(f"/api/v1/jobs/{job_id}/{path}/link")
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    return res.json()["url"]


def test_a_minted_link_downloads_without_a_header_and_resumes(client, job):
    res = client.post(f"/api/v1/jobs/{job}/result/link")
    body = res.json()
    url = body["url"]
    assert url.startswith(f"/api/v1/dl/{job}/result?exp=")
    exp = int(parse_qs(urlsplit(url).query)["exp"][0])
    assert time.time() < exp <= time.time() + 300
    assert body["expiresAt"] == time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp))

    full = client.get(url)
    assert full.status_code == 200 and full.content == PDF
    part = client.get(url, headers={"Range": "bytes=5-14"})
    assert part.status_code == 206 and part.content == PDF[5:15]
    out = client.get(mint(client, job, 0))
    assert out.status_code == 200 and out.content == PDF


def test_link_routes_check_the_job_first(client, r, data):
    job_id = create(client)
    assert client.post(f"/api/v1/jobs/{job_id}/result/link").status_code == 409
    finish(r, data, job_id)
    assert client.post(f"/api/v1/jobs/{job_id}/outputs/1/link").status_code == 404
    missing = "j_" + "Z" * 22
    assert client.post(f"/api/v1/jobs/{missing}/result/link").status_code == 404


def _with(url: str, **changes) -> str:
    path, _, query = url.partition("?")
    params = {k: v[0] for k, v in parse_qs(query).items()} | changes
    return path + "?" + "&".join(f"{k}={v}" for k, v in params.items())


def test_every_failure_is_the_same_404_and_counts(client, r, data, job):
    other = create(client)
    finish(r, data, other)
    key = client.app.state.dl_key
    url = mint(client, job)
    now = int(time.time())
    past = now - 1
    ahead = now + 301
    bad = [
        _with(url, exp=past, sig=dl_signature(key, job, "result", past)),  # expired
        url.replace(job, other),  # another job
        url.replace("/result?", "/0?"),  # another target
        _with(url, sig="A" * 43),  # changed signature
        _with(url, exp=ahead, sig=dl_signature(key, job, "result", ahead)),  # too far ahead
        url.split("?")[0],  # no exp, no sig
        _with(url, exp="1e9"),  # exp not an integer
        url.replace(job, "j_short"),  # malformed id
        url.replace("/result?", "/07?"),  # target not canonical
    ]
    bodies = []
    for n, u in enumerate(bad, 1):
        res = client.get(u)
        assert res.status_code == 404, u
        bodies.append(res.content)
        assert int(r.get(RL)) == n, u
    assert len(set(bodies)) == 1
    assert json.loads(bodies[0])["error"]["code"] == "JOB_NOT_FOUND"

    assert client.delete(f"/api/v1/jobs/{job}").status_code == 204
    gone = client.get(url)
    assert gone.status_code == 404 and gone.content == bodies[0]
    assert int(r.get(RL)) == len(bad) + 1


def test_a_link_to_an_output_that_vanished_is_the_same_404(client, data, job):
    url = mint(client, job)
    JobPaths(data, job).output(0, "pdf").unlink()
    res = client.get(url)
    assert res.status_code == 404 and res.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_too_many_failures_are_limited(client, r, job):
    url = mint(client, job)
    r.set(RL, 20, ex=60)
    assert client.get(url).status_code == 429  # even a good link, like the Bearer routes


def test_failed_links_do_not_lock_out_sign_in(make_client, online, r, data):
    """Review M-2: resumes of expired links fill rl:dl, never the rl:auth sign-in bucket."""
    import hashlib

    token = "fxt_" + "Q" * 43
    c = make_client(auth="", api_tokens=f"q:{hashlib.sha256(token.encode()).hexdigest()}")
    h = {"Authorization": f"Bearer {token}"}
    job_id = c.post(JOBS, files=[("files", ("a.pdf", PDF, "application/pdf"))], headers=h)
    job_id = job_id.json()["id"]
    finish(r, data, job_id)
    url = c.post(f"/api/v1/jobs/{job_id}/result/link", headers=h).json()["url"]
    for _ in range(25):
        c.get(_with(url, sig="A" * 43))
    assert c.get(url).status_code == 429
    assert not r.exists("rl:auth:testclient")
    assert c.get("/api/v1/tools", headers=h).status_code == 200
    r.delete(RL)
    r.set("rl:auth:testclient", 20, ex=60)
    assert c.get(url).status_code == 200  # and a full sign-in bucket leaves links working


def test_links_need_no_principal_but_minting_does(make_client, online, r, data):
    import hashlib

    token = "fxt_" + "Q" * 43
    c = make_client(auth="", api_tokens=f"q:{hashlib.sha256(token.encode()).hexdigest()}")
    h = {"Authorization": f"Bearer {token}"}
    res = c.post(JOBS, files=[("files", ("a.pdf", PDF, "application/pdf"))], headers=h)
    job_id = res.json()["id"]
    finish(r, data, job_id)
    assert c.post(f"/api/v1/jobs/{job_id}/result/link").status_code == 401
    url = c.post(f"/api/v1/jobs/{job_id}/result/link", headers=h).json()["url"]
    assert c.get(url).content == PDF


# --- one process, or a key from a secret file


def test_more_than_one_worker_without_a_key_file_is_refused(tools, tmp_path):
    with pytest.raises(ConfigError, match="FILEX_DL_KEY_FILE"):
        create_app(Settings(auth="none", api_workers=2), redis=object(), tools=tools)
    short = tmp_path / "short"
    short.write_bytes(b"x" * 31)
    with pytest.raises(ConfigError, match="32"):
        create_app(Settings(auth="none", dl_key_file=short), redis=object(), tools=tools)
    good = tmp_path / "key"
    good.write_bytes(bytes(range(32)))
    app = create_app(
        Settings(auth="none", api_workers=4, dl_key_file=good), redis=object(), tools=tools
    )
    assert app.state.dl_key == bytes(range(32))


def test_a_binary_key_keeps_its_whitespace_bytes(tools, tmp_path):
    """Review L-7: only a trailing newline is stripped, never key bytes like 0x20 or 0x0b."""
    raw = b" \x0b" + bytes(range(40, 70)) + b"\t "
    for written in (raw, raw + b"\n", raw + b"\r\n"):
        path = tmp_path / "key"
        path.write_bytes(written)
        app = create_app(Settings(auth="none", dl_key_file=path), redis=object(), tools=tools)
        assert app.state.dl_key == raw


def test_worker_count_is_read_like_uvicorn_reads_it():
    assert uvicorn_workers(["uvicorn", "m:app"], {}) == 1
    assert uvicorn_workers(["uvicorn", "m:app", "--workers", "3"], {}) == 3
    assert uvicorn_workers(["uvicorn", "--workers=2"], {"WEB_CONCURRENCY": "5"}) == 2
    assert uvicorn_workers(["uvicorn"], {"WEB_CONCURRENCY": "5"}) == 5


# --- sig never reaches the access log


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_sig_never_appears_in_the_access_log(server, settings, tools, client, job):
    """A real uvicorn server with its default logging config, as in the container."""
    app = create_app(
        settings, redis=fakeredis.FakeAsyncRedis(server=server, decode_responses=True), tools=tools
    )
    config = uvicorn.Config(app, lifespan="on", access_log=True)
    access = logging.getLogger("uvicorn.access")
    capture = Capture()
    access.addHandler(capture)
    srv = uvicorn.Server(config)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not srv.started and time.monotonic() < deadline:
            time.sleep(0.02)
        base, host = f"http://127.0.0.1:{port}", {"host": "testserver"}
        url = httpx.post(f"{base}/api/v1/jobs/{job}/result/link", headers=host).json()["url"]
        res = httpx.get(base + url, headers=host)
        assert res.status_code == 200 and res.content == PDF
        httpx.get(base + url.replace("sig=", "sig=x"), headers=host)
    finally:
        srv.should_exit = True
        thread.join(10)
        access.removeHandler(capture)
    dl_lines = [line for line in capture.lines if "/api/v1/dl/" in line]
    assert len(dl_lines) == 2
    assert not any("sig=" in line or "exp=" in line for line in capture.lines)
