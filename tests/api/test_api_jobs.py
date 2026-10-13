"""Job API (task 6): catalog, job creation limits and checks, status, cancel."""

import io
import json
import zipfile

import pytest

from filex import formats
from filex.api.deps import Settings
from filex.api.main import create_app
from filex.jobs import JobPaths, job_key, queue_key, sec_key

PDF = b"%PDF-1.7\n%api test\n"
JOBS = "/api/v1/tools/api.pdf/jobs"


def files(*items):
    """multipart ``files`` parts: (name, bytes) pairs."""
    return [("files", (name, content, "application/octet-stream")) for name, content in items]


def opts(value) -> tuple:
    return ("options", (None, json.dumps(value), "application/json"))


def assert_no_job_left(r, data):
    assert not list((data / "jobs").glob("*")) if (data / "jobs").exists() else True
    assert not r.keys("job:*") and not r.keys("sec:*")
    assert not r.exists(queue_key("core")) or r.xlen(queue_key("core")) == 0


# --- health, auth hook, catalog


TOKENS = "ci:" + "0" * 64  # static tokens on, no token presented


def test_health_says_only_ok_without_auth(make_client):
    res = make_client(auth="", api_tokens=TOKENS).get("/api/v1/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}


def test_without_a_token_every_other_route_is_401(make_client, online):
    c = make_client(auth="", api_tokens=TOKENS)
    for method, path in [("GET", "/api/v1/tools"), ("POST", JOBS), ("GET", "/api/v1/formats")]:
        res = c.request(method, path)
        assert res.status_code == 401, path
        assert res.json()["error"]["code"] == "UNAUTHENTICATED"


def test_unknown_host_is_refused(client):
    assert client.get("/api/v1/health", headers={"host": "evil.example"}).status_code == 400


def test_tool_catalog_shows_availability_and_profile(client):
    res = client.get("/api/v1/tools")
    assert res.status_code == 200
    by_id = {t["id"]: t for t in res.json()}
    assert by_id["api.pdf"]["available"] is True
    assert by_id["api.office"] == by_id["api.office"] | {"available": False, "profile": "office"}
    assert by_id["api.pdf"]["keywords"] == ["copy", "duplicate"]
    assert by_id["api.office"]["keywords"] == []


def test_tool_detail_has_camel_case_options_schema(client):
    t = client.get("/api/v1/tools/api.pdf").json()
    props = t["optionsSchema"]["properties"]
    assert "pageSize" in props and props["password"]["writeOnly"] is True
    assert t["files"]["max"] == 3 and t["files"]["aux"]["certificate"]["formats"] == ["p12"]
    assert t["errors"] == ["WRONG_PASSWORD"] and t["warnings"] == []
    assert t["keywords"] == ["copy", "duplicate"]
    res = client.get("/api/v1/tools/nope.nope")
    assert res.status_code == 404 and res.json()["error"]["code"] == "TOOL_NOT_FOUND"


def test_formats_list(client):
    pdf = next(f for f in client.get("/api/v1/formats").json() if f["id"] == "pdf")
    assert pdf == {"id": "pdf", "title": "PDF", "extensions": [".pdf"], "mime": "application/pdf"}


# --- creating a job


def test_create_job_writes_the_runner_contract(client, r, data):
    res = client.post(JOBS, files=[*files(("a.pdf", PDF)), opts({"pageSize": "LETTER"})])
    assert res.status_code == 202, res.text
    job = res.json()
    assert res.headers["location"] == f"/api/v1/jobs/{job['id']}"
    assert job["status"] == "QUEUED" and job["tool"] == "api.pdf" and job["warnings"] == []
    assert job["inputs"] == [{"name": "a.pdf", "format": "pdf", "size": len(PDF)}]
    assert job["steps"][0] | {"progress": 0} == {
        "index": 0,
        "tool": "api.pdf",
        "group": "core",
        "status": "QUEUED",
        "progress": 0,
        "message": "",
    }

    h = r.hgetall(job_key(job["id"]))
    assert h["status"] == "QUEUED"
    assert json.loads(h["steps"]) == [
        {"tool": "api.pdf", "options": {"pageSize": "LETTER"}, "target": None, "secret": False}
    ]
    assert json.loads(h["inputs"]) == [{"format": "pdf", "name": "a.pdf", "size": len(PDF)}]
    assert json.loads(h["aux"]) == {}
    assert 0 < r.ttl(job_key(job["id"])) <= 24 * 3600
    [(_, entry)] = r.xrange(queue_key("core"))
    assert entry == {"job": job["id"], "step": "0"}
    assert JobPaths(data, job["id"]).input(0, "pdf").read_bytes() == PDF


def test_secret_options_go_only_to_the_sec_key(client, r):
    secret = "s3cret-value-xyz"
    body = [*files(("a.pdf", PDF)), opts({"password": secret})]
    res = client.post(JOBS, files=body)
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    assert json.loads(r.get(sec_key(job_id, 0))) == {"password": secret}
    assert 0 < r.ttl(sec_key(job_id, 0)) <= 24 * 3600
    assert json.loads(r.hget(job_key(job_id), "steps"))[0]["secret"] is True
    dump = json.dumps([r.hgetall(job_key(job_id)), r.xrange(queue_key("core"))])
    assert secret not in dump and secret not in res.text
    assert secret not in client.get(f"/api/v1/jobs/{job_id}").text


def test_secret_is_never_echoed_in_validation_errors(client):
    res = client.post(JOBS, files=[*files(("a.pdf", PDF)), opts({"password": "x" * 1001})])
    assert res.status_code == 422 and "xxxx" not in res.text


@pytest.mark.parametrize(
    "options",
    [{"page_size": "LETTER"}, {"pageSize": "A3"}, {"unknown": 1}, [1, 2]],
    ids=["by-name", "bad-value", "unknown-key", "not-object"],
)
def test_invalid_options_are_422(client, r, data, options):
    res = client.post(JOBS, files=[*files(("a.pdf", PDF)), opts(options)])
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_OPTIONS"
    assert_no_job_left(r, data)


def test_options_that_are_not_json_are_422(client):
    body = [*files(("a.pdf", PDF)), ("options", (None, "{nope", "application/json"))]
    assert client.post(JOBS, files=body).json()["error"]["code"] == "INVALID_OPTIONS"


def test_wrong_file_type_is_422_and_leaves_nothing(client, r, data):
    res = client.post(JOBS, files=files(("a.pdf", b"\x89PNG\r\n\x1a\n" + b"\0" * 40)))
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "INVALID_INPUT_FORMAT" and err["details"]["format"] == "png"
    assert_no_job_left(r, data)


def test_zip_with_too_many_entries_says_why(client, monkeypatch):
    monkeypatch.setattr(formats, "MAX_ARCHIVE_ENTRIES", 3)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(4):
            zf.writestr(f"{i}.txt", "x")
    res = client.post("/api/v1/tools/api.zip/jobs", files=files(("a.zip", buf.getvalue())))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "TOO_MANY_ARCHIVE_ENTRIES"


def test_offline_group_is_409_naming_the_profile(client, data):
    res = client.post("/api/v1/tools/api.office/jobs", files=files(("a.pdf", PDF)))
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "TOOL_UNAVAILABLE" and err["details"] == {"profile": "office"}
    assert "office" in err["message"]


def test_unknown_tool_is_404(client):
    res = client.post("/api/v1/tools/nope.nope/jobs", files=files(("a.pdf", PDF)))
    assert res.status_code == 404 and res.json()["error"]["code"] == "TOOL_NOT_FOUND"


def test_traversal_name_is_stored_by_index_and_shown_sanitised(client, data):
    res = client.post(JOBS, files=files(("../../etc/x.pdf", PDF)))
    assert res.status_code == 202
    job = res.json()
    assert job["inputs"][0]["name"] == "x.pdf"
    stored = list(JobPaths(data, job["id"]).in_dir.iterdir())
    assert [p.name for p in stored] == ["0.pdf"]


def test_file_over_the_per_file_cap_is_413_without_partial_file(client, r, data):
    res = client.post(JOBS, files=files(("a.pdf", PDF + b"\0" * 6_000)))
    assert res.status_code == 413 and res.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert_no_job_left(r, data)


def test_job_over_the_upload_cap_is_413_without_partial_files(client, r, data):
    big = PDF + b"\0" * 4_000
    res = client.post(JOBS, files=files(("a.pdf", big), ("b.pdf", big), ("c.pdf", big)))
    assert res.status_code == 413 and res.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert_no_job_left(r, data)


def test_upload_exactly_at_the_cap_is_accepted(make_client, online):
    c = make_client(max_upload=len(PDF) + 100, max_file=len(PDF) + 100)
    assert c.post(JOBS, files=files(("a.pdf", PDF + b"\0" * 100))).status_code == 202
    over = c.post(JOBS, files=files(("a.pdf", PDF + b"\0" * 101)))
    assert over.status_code == 413


def test_options_part_over_64_kb_is_413(client, r, data):
    big = ("options", (None, json.dumps({"pageSize": "A4", "x": "y" * 65_536}), "text/plain"))
    res = client.post(JOBS, files=[*files(("a.pdf", PDF)), big])
    assert res.status_code == 413 and res.json()["error"]["code"] == "OPTIONS_TOO_LARGE"
    assert_no_job_left(r, data)


def test_eleventh_form_field_is_refused(client, r, data):
    fields = [("options", (None, "{}", "text/plain"))] * 11
    res = client.post(JOBS, files=[*files(("a.pdf", PDF)), *fields])
    assert res.status_code == 422  # the second options part already is (only one is allowed)
    assert_no_job_left(r, data)


def test_more_files_than_the_global_cap_is_refused(make_client, online, r, data):
    c = make_client(max_files=2)
    res = c.post(JOBS, files=files(*[(f"{i}.pdf", PDF) for i in range(3)]))
    assert res.status_code == 422 and res.json()["error"]["code"] == "TOO_MANY_FILES"
    assert_no_job_left(r, data)


@pytest.mark.parametrize("count", [0, 4])
def test_file_count_outside_the_tool_range_is_422(client, r, data, count):
    res = client.post(
        JOBS,
        files=[*files(*[(f"{i}.pdf", PDF) for i in range(count)]), opts({})],
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] in ("INVALID_FILE_COUNT", "TOO_MANY_FILES")
    assert_no_job_left(r, data)


def test_inputs_part_is_not_supported_yet(client):
    body = [*files(("a.pdf", PDF)), ("inputs", (None, "[]", "application/json"))]
    res = client.post(JOBS, files=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "NOT_SUPPORTED"


def test_unknown_part_is_422(client):
    body = [*files(("a.pdf", PDF)), ("photo", ("p.pdf", PDF, "application/pdf"))]
    res = client.post(JOBS, files=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "UNKNOWN_PART"


def test_aux_slot_is_sniffed_and_stored_by_slot_name(client, r, data):
    p12 = bytes.fromhex("3082010a020103") + b"\0" * 32
    body = [*files(("a.pdf", PDF)), ("certificate", ("../me.pfx", p12, "x/y"))]
    res = client.post(JOBS, files=body)
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    assert json.loads(r.hget(job_key(job_id), "aux")) == {"certificate": "p12"}
    assert JobPaths(data, job_id).aux("certificate", "p12").read_bytes() == p12


def test_aux_slot_of_the_wrong_type_is_422(client):
    body = [*files(("a.pdf", PDF)), ("certificate", ("me.p12", PDF, "x/y"))]
    res = client.post(JOBS, files=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_INPUT_FORMAT"


def test_body_that_is_not_multipart_is_422(client):
    res = client.post(JOBS, json={"files": []})
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_REQUEST"


def test_truncated_multipart_body_is_422_and_leaves_nothing(client, r, data):
    body = b'--b\r\nContent-Disposition: form-data; name="files"; filename="a.pdf"\r\n\r\n' + PDF
    res = client.post(
        JOBS, content=body, headers={"content-type": "multipart/form-data; boundary=b"}
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_REQUEST"
    assert_no_job_left(r, data)


def test_streamed_upload_is_not_held_in_memory(server, tools, settings, online):
    # httpx's ASGI transport streams the body; the TestClient would read it all first.
    import tracemalloc

    import anyio
    import fakeredis
    import httpx

    size = 8 * 1024 * 1024
    cfg = Settings(**{**settings.__dict__, "max_upload": size + 1024, "max_file": size + 1024})
    head = b'--b\r\nContent-Disposition: form-data; name="files"; filename="a.pdf"\r\n\r\n'

    async def body():
        yield head + PDF
        chunk = b"\0" * 65536
        for _ in range(size // 65536 - 1):
            yield chunk
        yield b"\r\n--b--\r\n"

    async def post():
        app = create_app(cfg, redis=fakeredis.FakeAsyncRedis(server=server), tools=tools)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            headers = {"content-type": "multipart/form-data; boundary=b"}
            return await c.post(JOBS, content=body(), headers=headers)

    tracemalloc.start()
    res = anyio.run(post)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert res.status_code == 202, res.text
    assert peak < size // 4


# --- status and cancel


def test_job_status_has_etag_and_304(client):
    job_id = client.post(JOBS, files=files(("a.pdf", PDF))).json()["id"]
    res = client.get(f"/api/v1/jobs/{job_id}")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    etag = res.headers["etag"]
    again = client.get(f"/api/v1/jobs/{job_id}", headers={"if-none-match": etag})
    assert again.status_code == 304 and again.headers["etag"] == etag and not again.content


def test_job_status_etag_changes_when_the_job_changes(client, r):
    job_id = client.post(JOBS, files=files(("a.pdf", PDF))).json()["id"]
    etag = client.get(f"/api/v1/jobs/{job_id}").headers["etag"]
    r.hset(job_key(job_id), mapping={"status": "RUNNING", "progress": "0.5"})
    res = client.get(f"/api/v1/jobs/{job_id}", headers={"if-none-match": etag})
    assert res.status_code == 200 and res.json()["progress"] == 0.5


@pytest.mark.parametrize("job_id", ["j_" + "A" * 22, "nope", "j_" + "A" * 21 + "!"])
def test_unknown_or_malformed_job_is_404(client, job_id):
    for path in ("", "/result", "/outputs/0"):
        res = client.get(f"/api/v1/jobs/{job_id}{path}")
        assert res.status_code == 404 and res.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_delete_cancels_a_queued_job_then_deletes_it(client, r, data):
    body = [*files(("a.pdf", PDF)), opts({"password": "correct horse"})]
    job_id = client.post(JOBS, files=body).json()["id"]
    assert r.exists(sec_key(job_id, 0))

    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "CANCELLED" and job["finishedAt"]
    assert not r.exists(sec_key(job_id, 0))  # the runner cleans files; secrets go now

    # A finished job (CANCELLED included) is deleted at once: "Delete now".
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert not r.exists(job_key(job_id)) and not JobPaths(data, job_id).dir.exists()
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404


def test_delete_of_a_finished_job_removes_everything(client, r, data):
    job_id = client.post(JOBS, files=files(("a.pdf", PDF))).json()["id"]
    r.hset(job_key(job_id), "status", "SUCCEEDED")
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert not r.exists(job_key(job_id)) and not JobPaths(data, job_id).dir.exists()


def test_result_before_the_job_finished_is_409(client):
    job_id = client.post(JOBS, files=files(("a.pdf", PDF))).json()["id"]
    res = client.get(f"/api/v1/jobs/{job_id}/result")
    assert res.status_code == 409 and res.json()["error"]["code"] == "JOB_NOT_READY"


def test_unknown_route_uses_the_error_envelope(client):
    res = client.get("/api/v1/nope")
    assert res.status_code == 404 and res.json()["error"]["code"] == "NOT_FOUND"
