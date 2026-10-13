"""Converter targets at job creation (ADR-0005): the ``target`` part is defaulted, required or
rejected by the sniffed input formats, stored in the step, and reaches the tool as
``ctx.target`` through the real runner."""

import io
import json

import pikepdf
import pytest
from api_tools.tools import office_file

from filex.api.deps import ApiError
from filex.api.routes import _target
from filex.jobs import job_key
from filex.registry import discover
from filex.worker.runner import ENGINES, Config, Runner

REAL = discover()


@pytest.fixture(scope="module")
def tools():
    return discover("api_tools") | {"pdf.to-image": REAL["pdf.to-image"]}


@pytest.fixture
def run_worker(r, data, tools):
    def run():
        probes = {e: ENGINES[e] for e in REAL["pdf.to-image"].engines}  # pdf.to-image's
        runner = Runner(r, Config(group="core", data=data), tools=tools, probes=probes)
        assert runner.run_once()

    return run


def doc(tmp_path, fmt):
    return office_file(tmp_path / f"in.{fmt}", fmt).read_bytes()


def one_page_pdf():
    pdf, buf = pikepdf.new(), io.BytesIO()
    pdf.add_blank_page(page_size=(72, 72))
    pdf.save(buf)
    return buf.getvalue()


def post(client, tool, files, target=None, options=None):
    body = [("files", f) for f in files]
    if target is not None:
        body.append(("target", (None, target)))
    if options is not None:
        body.append(("options", (None, json.dumps(options), "application/json")))
    return client.post(f"/api/v1/tools/{tool}/jobs", files=body)


def step_target(r, job_id):
    return json.loads(r.hget(job_key(job_id), "steps"))[0]["target"]


def error(res, status):
    assert res.status_code == status, res.text
    return res.json()["error"]


# --- through the API with the real runner


def test_docx_converts_to_the_requested_odt(client, run_worker, r, tmp_path):
    res = post(client, "api.convert", [("Brief.docx", doc(tmp_path, "docx"))], target="odt")
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    assert step_target(r, job_id) == "odt"
    run_worker()
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job["error"]
    assert [(o["name"], o["format"]) for o in job["outputs"]] == [("Brief.odt", "odt")]


def test_the_only_target_is_the_default(client, run_worker, r, tmp_path):
    res = post(client, "api.convert", [("a.odt", doc(tmp_path, "odt"))])
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    assert step_target(r, job_id) == "docx"
    run_worker()
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED" and job["outputs"][0]["format"] == "docx"


def test_pdf_to_image_falls_back_to_its_format_option(client, run_worker, r):
    res = post(client, "pdf.to-image", [("p.pdf", one_page_pdf())], options={"format": "jpg"})
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    assert step_target(r, job_id) == "jpg"
    run_worker()
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job["error"]
    assert [(o["name"], o["format"]) for o in job["outputs"]] == [("p-p001.jpg", "jpg")]


def test_pdf_to_image_target_wins_over_the_option(client, run_worker, r):
    files = [("p.pdf", one_page_pdf())]
    res = post(client, "pdf.to-image", files, target="webp", options={"format": "jpg"})
    assert res.status_code == 202, res.text
    run_worker()
    job = client.get(f"/api/v1/jobs/{res.json()['id']}").json()
    assert job["status"] == "SUCCEEDED", job["error"]
    assert job["outputs"][0]["format"] == "webp"


# --- rejected at job creation


def test_several_targets_need_one_chosen(client, tmp_path):
    e = error(post(client, "api.convert", [("a.docx", doc(tmp_path, "docx"))]), 422)
    assert e["code"] == "TARGET_REQUIRED" and e["details"] == {"targets": ["odt", "rtf"]}


@pytest.mark.parametrize(("fmt", "target"), [("docx", "pdf"), ("odt", "rtf"), ("docx", "x" * 32)])
def test_target_without_an_edge_from_the_input_is_rejected(client, tmp_path, fmt, target):
    e = error(post(client, "api.convert", [(f"a.{fmt}", doc(tmp_path, fmt))], target=target), 422)
    assert e["code"] == "INVALID_TARGET"


def test_inputs_without_a_common_target_are_rejected(client, tmp_path):
    files = [("a.docx", doc(tmp_path, "docx")), ("b.odt", doc(tmp_path, "odt"))]
    e = error(post(client, "api.convert", files), 422)
    assert e["code"] == "INVALID_TARGET" and e["details"] == {"targets": []}


def test_overlong_or_repeated_target_part_is_rejected(client, tmp_path):
    files = [("files", ("a.docx", doc(tmp_path, "docx")))]
    long = client.post(
        "/api/v1/tools/api.convert/jobs", files=[*files, ("target", (None, "o" * 33))]
    )
    assert error(long, 422)["code"] == "INVALID_TARGET"
    twice = [*files, ("target", (None, "odt")), ("target", (None, "rtf"))]
    res = client.post("/api/v1/tools/api.convert/jobs", files=twice)
    assert error(res, 422)["code"] == "INVALID_REQUEST"


def test_a_tool_that_is_not_a_converter_rejects_a_target(client):
    res = post(client, "api.pdf", [("a.pdf", b"%PDF-1.7\n")], target="pdf")
    assert error(res, 422)["code"] == "INVALID_TARGET"


def test_tool_detail_lists_edges_and_the_target_option(client):
    detail = client.get("/api/v1/tools/pdf.to-image").json()
    assert detail["targetOption"] == "format"
    assert {"src": "pdf", "dst": "png", "lossy": True} in detail["converts"]
    plain = client.get("/api/v1/tools/api.pdf").json()
    assert plain["converts"] == [] and plain["targetOption"] is None


# --- the shipped converters resolve (no engine needed)


@pytest.mark.parametrize(
    ("tool", "formats", "target", "expected"),
    [
        ("office.to-pdf", ["docx", "xlsx"], None, "pdf"),
        ("image.to-pdf", ["jpg", "png"], None, "pdf"),
        ("office.convert", ["docx"], "odt", "odt"),
        ("office.convert", ["xlsx", "ods"], "csv", "csv"),
        ("pdf.to-text", ["pdf"], None, "txt"),
        ("pdf.to-image", ["pdf"], None, "png"),  # the format option's default
    ],
)
def test_shipped_converter_targets_resolve(tool, formats, target, expected):
    t = REAL[tool]
    options = t.options().model_dump(mode="json", by_alias=True)
    assert _target(t, formats, target, options) == expected


@pytest.mark.parametrize(
    ("tool", "formats", "target", "code"),
    [
        ("office.convert", ["docx"], None, "TARGET_REQUIRED"),
        ("office.convert", ["docx"], "xlsx", "INVALID_TARGET"),  # another family
        ("office.to-pdf", ["docx"], "odt", "INVALID_TARGET"),
        ("pdf.compress", ["pdf"], "pdf", "INVALID_TARGET"),  # not a converter
    ],
)
def test_shipped_converter_targets_are_checked(tool, formats, target, code):
    t = REAL[tool]
    with pytest.raises(ApiError) as e:
        _target(t, formats, target, t.options().model_dump(mode="json", by_alias=True))
    assert e.value.code == code
