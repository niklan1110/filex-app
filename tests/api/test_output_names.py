"""Output display names end to end: a tool names its outputs with ``Output(path, name)``, the
runner cleans the name into the job record, and the API saves downloads and zip entries under
it, falling back to the input's name (``pdf.split`` with the real runner and pikepdf)."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from filex.jobs import job_key
from filex.registry import discover
from filex.worker.runner import ENGINES, Config, Runner

CORPUS = Path(__file__).resolve().parents[1] / "corpus" / "pdf"


@pytest.fixture(scope="module")
def tools():
    pytest.importorskip("pikepdf")
    found = discover("api_tools")
    found["pdf.split"] = discover()["pdf.split"]
    return found


@pytest.fixture
def split_client(make_client, online):
    return make_client(max_upload=200_000, max_file=100_000)


def run_split(client, r, data, tools, name, options):
    pdf = (CORPUS / "text_10p.pdf").read_bytes()
    body = [
        ("files", (name, pdf, "application/pdf")),
        ("options", (None, json.dumps(options), "application/json")),
    ]
    res = client.post("/api/v1/tools/pdf.split/jobs", files=body)
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    probes = {"pikepdf": ENGINES["pikepdf"]}
    assert Runner(r, Config(group="core", data=data), tools=tools, probes=probes).run_once()
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job
    return job_id, job


def test_split_parts_download_under_their_own_names(split_client, r, data, tools):
    job_id, job = run_split(split_client, r, data, tools, "Отчёт 2026.pdf", {"every": 4})
    names = ["Отчёт 2026-part-001.pdf", "Отчёт 2026-part-002.pdf", "Отчёт 2026-part-003.pdf"]
    assert [o["name"] for o in job["outputs"]] == names

    one = split_client.get(f"/api/v1/jobs/{job_id}/outputs/1")
    assert one.status_code == 200
    assert one.headers["content-disposition"] == (
        'attachment; filename="2026-part-002.pdf"; '
        "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82%202026-part-002.pdf"
    )

    res = split_client.get(f"/api/v1/jobs/{job_id}/result")
    assert res.headers["content-type"] == "application/zip"
    assert (
        "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82%202026.zip"
        in res.headers["content-disposition"]
    )
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert zf.namelist() == names


def test_duplicate_tool_names_are_still_made_unique(split_client, r, data, tools):
    job_id, job = run_split(
        split_client, r, data, tools, "a.pdf", {"mode": "ranges", "ranges": "1, 2"}
    )
    assert [o["name"] for o in job["outputs"]] == ["a-part-001.pdf", "a-part-002.pdf"]
    # A worker that wrote the same name twice (or a doctored record) still gets unique names.
    record = json.loads(r.hget(job_key(job_id), "outputs"))
    for o in record:
        o["name"] = "same.pdf"
    r.hset(job_key(job_id), "outputs", json.dumps(record))
    job = split_client.get(f"/api/v1/jobs/{job_id}").json()
    assert [o["name"] for o in job["outputs"]] == ["same.pdf", "same (2).pdf"]


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("../../x\u202e.exe", "x.pdf"),  # Redis is not trusted: cleaned again, format's extension
        ("", "a.pdf"),  # empty: named after the input
        (42, "a.pdf"),  # not a string: named after the input
    ],
)
def test_names_from_redis_are_cleaned_again(split_client, r, data, tools, stored, shown):
    job_id, _ = run_split(split_client, r, data, tools, "a.pdf", {"mode": "ranges", "ranges": "1"})
    record = json.loads(r.hget(job_key(job_id), "outputs"))
    record[0]["name"] = stored
    r.hset(job_key(job_id), "outputs", json.dumps(record))
    assert split_client.get(f"/api/v1/jobs/{job_id}").json()["outputs"][0]["name"] == shown
    res = split_client.get(f"/api/v1/jobs/{job_id}/result")
    assert f'filename="{shown}"' in res.headers["content-disposition"]
