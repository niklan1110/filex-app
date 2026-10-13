"""Static multi-step tools (ADR-0005 amendment, plan task 52): ``djvu.to-pdf`` plans its OCR step
when the job is created, from the workers online at that moment."""

import json
from pathlib import Path

import pytest

from filex.jobs import job_key
from filex.registry import FollowUp, Plan, discover

REAL = discover()
DJVU = Path(__file__).resolve().parents[1] / "corpus" / "ebook" / "un_resolution_1837.djvu"


@pytest.fixture(scope="module")
def tools():
    return discover("api_tools") | {i: REAL[i] for i in ("djvu.to-pdf", "ocr.pdf")}


def beat(r, group, tool_ids):
    r.set(f"worker:{group}:test-1", json.dumps({"tools": tool_ids, "engines": {}}), ex=30)


def post(client, options=None):
    body = [("files", ("scan.djvu", DJVU.read_bytes()))]
    if options is not None:
        body.append(("options", (None, json.dumps(options), "application/json")))
    res = client.post("/api/v1/tools/djvu.to-pdf/jobs", files=body)
    assert res.status_code == 202, res.text
    return res.json()


def steps(r, job_id):
    return json.loads(r.hget(job_key(job_id), "steps"))


def test_with_ocr_online_the_job_has_two_steps_from_creation(make_client, r):
    beat(r, "ebook", ["djvu.to-pdf"])
    beat(r, "ocr", ["ocr.pdf"])
    job = post(make_client(max_upload=100_000, max_file=60_000))
    planned = steps(r, job["id"])
    assert [s["tool"] for s in planned] == ["djvu.to-pdf", "ocr.pdf"]
    assert planned[0]["target"] == "pdf"
    assert planned[1]["options"]["mode"] == "skip" and planned[1]["secret"] is False
    assert job["warnings"] == []
    assert [s["tool"] for s in job["steps"]] == ["djvu.to-pdf", "ocr.pdf"]


def test_with_ocr_offline_the_job_has_one_step_and_an_ocr_skipped_warning(make_client, r):
    beat(r, "ebook", ["djvu.to-pdf"])
    job = post(make_client(max_upload=100_000, max_file=60_000))
    assert [s["tool"] for s in steps(r, job["id"])] == ["djvu.to-pdf"]
    (warning,) = job["warnings"]
    assert warning["code"] == "OCR_SKIPPED" and warning["step"] == 0
    assert "COMPOSE_PROFILES" in warning["message"]
    stored = json.loads(r.hget(job_key(job["id"]), "warnings"))
    assert stored == job["warnings"]


def test_ocr_never_plans_one_step_without_a_warning(make_client, r):
    beat(r, "ebook", ["djvu.to-pdf"])
    beat(r, "ocr", ["ocr.pdf"])
    job = post(make_client(max_upload=100_000, max_file=60_000), {"ocr": "never"})
    assert [s["tool"] for s in steps(r, job["id"])] == ["djvu.to-pdf"]
    assert job["warnings"] == []


def test_plan_hook_returns_a_plan():
    plan = REAL["djvu.to-pdf"].plan
    opts = REAL["djvu.to-pdf"].options()
    assert isinstance(plan(opts, frozenset()), Plan)
    assert plan(opts, frozenset({"ocr.pdf"})).steps[0].tool == "ocr.pdf"


def _planned_with(plan_result):
    import dataclasses

    from filex.api.routes import _planned

    djvu = dataclasses.replace(REAL["djvu.to-pdf"], plan=lambda opts, online: plan_result)
    tools = {"djvu.to-pdf": djvu, "ocr.pdf": REAL["ocr.pdf"]}
    first = [{"tool": djvu.id, "options": {"ocr": "auto"}, "target": "pdf", "secret": False}]
    return _planned(tools, djvu, {"ocr": "auto"}, {"djvu.to-pdf", "ocr.pdf"}, first)


def test_follow_up_options_are_validated_and_stored_with_defaults():
    steps, warnings = _planned_with(Plan(steps=(FollowUp("ocr.pdf", {"mode": "skip"}),)))
    assert steps[1]["options"]["mode"] == "skip" and steps[1]["options"]["languages"] == "eng"
    assert warnings == []


@pytest.mark.parametrize(
    "bad",
    [
        Plan(steps=(FollowUp("no.such-tool"),)),
        Plan(steps=(FollowUp("ocr.pdf", {"mode": "sideways"}),)),
        Plan(warnings=(("NOT_DECLARED", "x"),)),
    ],
    ids=["unknown-tool", "bad-options", "undeclared-warning"],
)
def test_a_broken_plan_fails_at_creation_never_later(bad):
    with pytest.raises((KeyError, ValueError)):
        _planned_with(bad)
