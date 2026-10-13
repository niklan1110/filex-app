"""``convert`` over HTTP (ADR-0005, plan task 36): the route preview, reachable targets, and a
two-group job planned at creation and run end to end by the real runner (fakeredis), handing
off from the core worker to the office worker."""

import json

import pytest

from filex.jobs import JobPaths, job_key, queue_key
from filex.registry import discover
from filex.worker.runner import Config, Runner


@pytest.fixture(scope="module")
def tools():
    return discover("convert_tools")


def beat(r, group, tool_ids):
    r.set(f"worker:{group}:test-1", json.dumps({"tools": tool_ids, "engines": {}}), ex=30)


@pytest.fixture
def both_online(r):
    beat(r, "core", ["cv.md-html"])
    beat(r, "office", ["cv.html-pdf"])


def post(client, *names, target="pdf"):
    body = [("files", (n, f"# {n}\n".encode(), "text/markdown")) for n in names]
    if target is not None:
        body.append(("target", (None, target)))
    return client.post("/api/v1/convert/jobs", files=body)


def test_plan_previews_a_two_group_route_with_its_lossy_step(make_client, both_online):
    res = make_client().get("/api/v1/convert/plan", params={"from": "md", "to": "pdf"})
    assert res.status_code == 200, res.text
    plan = res.json()
    assert plan["sources"] == ["md"] and plan["target"] == "pdf" and plan["lossy"] is True
    assert plan["cost"] == 1 + 5
    assert [(s["tool"], s["group"], s["src"], s["dst"], s["lossy"]) for s in plan["steps"]] == [
        ("cv.md-html", "core", ["md"], "html", False),
        ("cv.html-pdf", "office", ["html"], "pdf", True),
    ]
    assert plan["steps"][1]["title"] == "HTML to PDF"
    (warning,) = plan["warnings"]
    assert warning["code"] == "LOSSY_STEP" and warning["step"] == 1


def test_no_route_names_the_profile_to_enable(make_client, r):
    beat(r, "core", ["cv.md-html"])
    res = make_client().get("/api/v1/convert/plan", params={"from": "md", "to": "pdf"})
    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "NO_ROUTE" and error["details"]["profiles"] == ["office"]
    assert "'office' to COMPOSE_PROFILES" in error["message"]


def test_plan_rejects_unknown_formats(make_client, both_online):
    res = make_client().get("/api/v1/convert/plan", params={"from": "md", "to": "nope"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "UNKNOWN_FORMAT"


def test_targets_lists_what_is_reachable_now(make_client, r):
    beat(r, "core", ["cv.md-html"])
    client = make_client()
    assert client.get("/api/v1/convert/targets").json() == [
        {
            "src": "md",
            "targets": [{"format": "html", "steps": 1, "lossy": False, "groups": ["core"]}],
        }
    ]
    beat(r, "office", ["cv.html-pdf"])
    by_src = {s["src"]: s["targets"] for s in client.get("/api/v1/convert/targets").json()}
    assert by_src["md"][1] == {
        "format": "pdf",
        "steps": 2,
        "lossy": True,
        "groups": ["core", "office"],
    }
    assert [t["format"] for t in by_src["html"]] == ["pdf"]


def test_convert_job_runs_across_two_groups(make_client, both_online, r, data, tools):
    client = make_client()
    res = post(client, "notes.md", "Отчёт.md")
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["tool"] == "convert" and job["status"] == "QUEUED"
    assert [(s["tool"], s["group"]) for s in job["steps"]] == [
        ("cv.md-html", "core"),
        ("cv.html-pdf", "office"),
    ]
    assert [w["code"] for w in job["warnings"]] == ["LOSSY_STEP"]
    stored = json.loads(r.hget(job_key(job["id"]), "steps"))
    assert [(s["target"], s["secret"], s["options"]) for s in stored] == [
        ("html", False, {}),
        ("pdf", False, {}),
    ]
    assert r.xlen(queue_key("core")) == 1 and r.xlen(queue_key("office")) == 0

    assert Runner(r, Config(group="core", data=data), tools=tools, probes={}).run_once()
    mid = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert [s["status"] for s in mid["steps"]] == ["SUCCEEDED", "QUEUED"]
    assert r.xlen(queue_key("office")) == 1  # handed off to the other group
    assert Runner(r, Config(group="office", data=data), tools=tools, probes={}).run_once()

    done = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert done["status"] == "SUCCEEDED", done
    assert [o["name"] for o in done["outputs"]] == ["notes.pdf", "Отчёт.pdf"]
    assert [w["code"] for w in done["warnings"]] == ["LOSSY_STEP"]
    first = client.get(f"/api/v1/jobs/{job['id']}/outputs/0").content.decode()
    # every step saw the name the user gave the file, and its own target
    assert first.startswith("%PDF-1.7\npdf ['notes.md'] <html>html ['notes.md']</html>")
    assert not JobPaths(data, job["id"]).steps_dir.exists()


def test_a_target_is_required(make_client, both_online, data):
    res = post(make_client(), "notes.md", target=None)
    assert res.status_code == 422 and res.json()["error"]["code"] == "TARGET_REQUIRED"
    assert not any((data / "jobs").iterdir())  # the upload is removed


def test_no_route_for_a_job_removes_the_upload(make_client, r, data):
    beat(r, "core", ["cv.md-html"])
    res = post(make_client(), "notes.md", target="pdf")
    assert res.status_code == 422 and res.json()["error"]["code"] == "NO_ROUTE"
    assert res.json()["error"]["details"]["profiles"] == ["office"]
    assert not any((data / "jobs").iterdir())


def test_files_no_converter_reads_are_refused(make_client, both_online):
    res = make_client().post(
        "/api/v1/convert/jobs",
        files=[("files", ("a.pdf", b"%PDF-1.7\n", "application/pdf")), ("target", (None, "md"))],
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_INPUT_FORMAT"


def test_convert_takes_no_options(make_client, both_online):
    res = make_client().post(
        "/api/v1/convert/jobs",
        files=[
            ("files", ("a.md", b"# a\n", "text/markdown")),
            ("target", (None, "pdf")),
            ("options", (None, '{"x": 1}', "application/json")),
        ],
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_OPTIONS"


def test_a_full_first_queue_answers_429(make_client, both_online, r):
    r.xadd(queue_key("core"), {"job": "j_x", "step": "0"})
    res = post(make_client(max_queued=1), "notes.md")
    assert res.status_code == 429 and res.json()["error"]["code"] == "QUEUE_FULL"
    assert res.json()["error"]["details"]["group"] == "core"
