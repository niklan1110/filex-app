"""Job API end to end (task 6): create a job over HTTP, run it with the real runner in-process,
then poll and download. Plus the display and download name rules (review M4, M5)."""

import io
import json
import zipfile

import pytest

from filex.api.names import content_disposition, display_name, download_name
from filex.jobs import JobPaths, sec_key
from filex.worker.runner import Config, Runner

PDF = b"%PDF-1.7\n%api test\n"
DOWNLOAD_HEADERS = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox; default-src 'none'",
    "cache-control": "no-store",
}


@pytest.fixture
def run_worker(r, data, tools):
    def run():
        runner = Runner(r, Config(group="core", data=data), tools=tools, probes={})
        assert runner.run_once()

    return run


def submit(client, tool, *names, options=None):
    body = [("files", (n, PDF + n.encode(), "application/pdf")) for n in names]
    if options is not None:
        body.append(("options", (None, json.dumps(options), "application/json")))
    res = client.post(f"/api/v1/tools/{tool}/jobs", files=body)
    assert res.status_code == 202, res.text
    return res.json()["id"]


def test_job_runs_and_its_result_downloads_as_an_attachment(client, run_worker, data):
    job_id = submit(client, "api.pdf", "Отчёт.pdf", "b.pdf")
    run_worker()

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED" and job["progress"] == 1 and job["error"] is None
    assert job["finishedAt"] and job["expiresAt"]
    assert job["steps"][0]["status"] == "SUCCEEDED"
    assert job["outputs"] == [
        {
            "index": 0,
            "name": "Отчёт.pdf",
            "format": "pdf",
            "size": len(PDF + "Отчёт.pdf".encode()),
            "role": "RESULT",
        }
    ]
    assert not JobPaths(data, job_id).in_dir.exists()

    res = client.get(f"/api/v1/jobs/{job_id}/result")
    assert res.status_code == 200 and res.content == PDF + "Отчёт.pdf".encode()
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers.items() >= DOWNLOAD_HEADERS.items()
    short = job_id[2:10]
    assert res.headers["content-disposition"] == (
        f'attachment; filename="filex-{short}.pdf"; '
        "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82.pdf"
    )

    part = client.get(f"/api/v1/jobs/{job_id}/outputs/0", headers={"range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == b"%PDF"
    assert part.headers.items() >= DOWNLOAD_HEADERS.items()


def test_several_results_download_as_one_zip_with_unique_names(client, run_worker):
    job_id = submit(client, "api.each", "same.pdf", "same.pdf", "other.pdf")
    run_worker()
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert [o["name"] for o in job["outputs"]] == ["same.pdf", "same (2).pdf", "other.pdf"]

    res = client.get(f"/api/v1/jobs/{job_id}/result")
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    assert res.headers.items() >= DOWNLOAD_HEADERS.items()
    assert 'filename="same.zip"' in res.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert zf.namelist() == ["same.pdf", "same (2).pdf", "other.pdf"]
        assert zf.read("other.pdf") == PDF + b"other.pdf"
    again = client.get(f"/api/v1/jobs/{job_id}/result", headers={"range": "bytes=0-1"})
    assert again.status_code == 206 and again.content == b"PK"

    assert client.get(f"/api/v1/jobs/{job_id}/outputs/2").content == PDF + b"other.pdf"
    for bad in ("3", "-1", "x"):
        res = client.get(f"/api/v1/jobs/{job_id}/outputs/{bad}")
        assert res.status_code in (404, 422), bad
    assert client.get(f"/api/v1/jobs/{job_id}/outputs/3").json()["error"]["code"] == (
        "OUTPUT_NOT_FOUND"
    )


def test_secret_reaches_the_step_and_is_consumed(client, run_worker, r):
    job_id = submit(client, "api.pdf", "a.pdf", options={"password": "correct horse"})
    run_worker()
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "SUCCEEDED"
    assert not r.exists(sec_key(job_id, 0))


def test_failed_job_reports_the_tool_error(client, run_worker):
    job_id = submit(client, "api.pdf", "a.pdf", options={"password": "wrong"})
    run_worker()
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "FAILED"
    assert job["error"] == {
        "code": "WRONG_PASSWORD",
        "message": "The password is wrong.",
        "step": 0,
    }
    res = client.get(f"/api/v1/jobs/{job_id}/result")
    assert res.status_code == 409 and res.json()["error"]["code"] == "JOB_NOT_READY"


def test_deleting_a_queued_job_cancels_it(client, run_worker, r):
    job_id = submit(client, "api.pdf", "a.pdf")
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    run_worker()  # the runner sees CANCELLED and drops the entry
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "CANCELLED"
    assert r.xlen("q:core") == 0


# --- names


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("../../etc/x.pdf", "x.pdf"),
        ("C:\\Users\\me\\a.pdf", "a.pdf"),
        ("e\u0301.pdf", "\u00e9.pdf"),  # NFC
        ("a\x00b\x1b\u202e.pdf", "ab.pdf"),  # control and bidi characters
        ("..", ""),
        (None, ""),
        ("x" * 300 + ".pdf", "x" * 200),
    ],
)
def test_display_name(raw, shown):
    assert display_name(raw) == shown


def test_download_name_uses_the_output_extension_and_falls_back():
    job_id = "j_" + "a1B2" * 5 + "zz"
    assert download_name("report.docx", "pdf", job_id) == "report.pdf"
    assert download_name('a"b\\c.pdf', "pdf", job_id) == "abc.pdf"
    assert download_name("", "pdf", job_id) == "filex-a1B2a1B2.pdf"
    assert len(download_name("y" * 300, "pdf", job_id)) == 150


def test_download_name_drops_characters_windows_forbids():
    job_id = "j_" + "a1B2" * 5 + "zz"
    assert download_name("Q1: plan*v2?<final>|x.docx", "pdf", job_id) == "Q1 planv2finalx.pdf"
    assert download_name("a:stream.pdf", "pdf", job_id) == "astream.pdf"  # no alternate stream
    assert download_name(":*?<>|.pdf", "pdf", job_id) == "filex-a1B2a1B2.pdf"


def test_content_disposition_is_one_safe_header_line():
    job_id = "j_" + "a" * 22
    value = content_disposition(download_name('evil"\r\nX: y.pdf', "pdf", job_id), job_id)
    assert "\r" not in value and "\n" not in value
    assert value.startswith('attachment; filename="')
    assert value.count('"') == 2


def test_content_disposition_keeps_accented_latin_in_the_ascii_fallback():
    value = content_disposition("Résumé.pdf", "j_" + "a" * 22)
    assert value == "attachment; filename=\"Resume.pdf\"; filename*=UTF-8''R%C3%A9sum%C3%A9.pdf"
