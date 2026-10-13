"""Log hygiene (task 65, ADR-0008 "Logs", review M7 fix 2): the API and the runner log JSON lines
with ids, sizes, durations and codes, never file names, option values, passwords or tokens.
Engine stderr only at DEBUG, cut to 2 KB, and never in an API response."""

import hashlib
import json
import logging

import pytest

from filex.logs import JsonFormatter
from filex.worker.runner import Config, Runner

FXT = "fxt_" + "Q" * 43
NAME = "Zebrafish-Diagnosis-Smith.pdf"
LABEL = "ultraviolet-octopus"
PASSWORD = "correct-horse-battery"
PDF = b"%PDF-1.7\n%log test\n"


class Lines(logging.Handler):
    """Every record, formatted as the containers write it."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[tuple[int, str]] = []

    def emit(self, record):
        self.lines.append((record.levelno, self.format(record)))

    def text(self, min_level=logging.INFO) -> str:
        return "\n".join(line for level, line in self.lines if level >= min_level)


@pytest.fixture
def lines():
    handler, root = Lines(), logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old)


@pytest.fixture
def token_client(make_client, online):
    digest = hashlib.sha256(FXT.encode()).hexdigest()
    client = make_client(auth="", api_tokens=f"ci:{digest}")
    client.headers["Authorization"] = f"Bearer {FXT}"
    return client


def run_job(client, r, data, tools, **options):
    body = [
        ("files", (NAME, PDF, "application/pdf")),
        ("options", (None, json.dumps({"label": LABEL, "password": PASSWORD, **options}), "")),
    ]
    res = client.post("/api/v1/tools/api.noisy/jobs", files=body)
    assert res.status_code == 202, res.text
    job_id = res.json()["id"]
    assert Runner(r, Config(group="core", data=data), tools=tools, probes={}).run_once()
    job = client.get(f"/api/v1/jobs/{job_id}")
    assert job.status_code == 200
    return job_id, job


def test_no_names_options_passwords_or_tokens_at_info(token_client, r, data, tools, lines):
    job_id, job = run_job(token_client, r, data, tools)
    assert job.json()["status"] == "SUCCEEDED"
    assert job.json()["outputs"][0]["name"] == f"{LABEL}-result.pdf"
    res = token_client.get(f"/api/v1/jobs/{job_id}/result")
    assert LABEL in res.headers["content-disposition"]

    text = lines.text()
    for leak in (NAME, "Zebrafish", LABEL, PASSWORD, FXT, "fxt_"):
        assert leak not in text, leak
    records = [json.loads(line) for _, line in lines.lines]
    created = next(x for x in records if x["msg"] == "job created")
    assert created | {"job": job_id, "tool": "api.noisy", "principal": "token:ci"} == created
    assert created["files"] == 1 and created["bytes"] == len(PDF)
    done = next(x for x in records if x["msg"] == "step done")
    assert done["job"] == job_id and done["tool"] == "api.noisy" and done["step"] == 0
    assert done["files"] == 1 and done["bytes"] == len(PDF) and done["duration_s"] >= 0


def test_failing_engine_stderr_only_at_debug_and_cut_to_2kb(token_client, r, data, tools, lines):
    _, job = run_job(token_client, r, data, tools, fail=True)
    body = job.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == "ENGINE_FAILED"
    assert LABEL not in job.text and PASSWORD not in job.text and "engine says" not in job.text

    info = lines.text()
    for leak in (NAME, LABEL, PASSWORD, FXT, "engine says", "xxxx"):
        assert leak not in info, leak
    failed = [json.loads(line) for _, line in lines.lines if '"step failed"' in line]
    assert failed and failed[0]["code"] == "ENGINE_FAILED"

    debug = [json.loads(line) for level, line in lines.lines if level == logging.DEBUG]
    stderr = [x["stderr"] for x in debug if "stderr" in x]
    assert len(stderr) == 1
    assert len(stderr[0].encode()) <= 2048
    assert "xxxx" in stderr[0]  # the tail of the engine's output
    assert "engine says:" not in stderr[0]  # its start, before the 5000 x, was cut


def test_exceptions_are_logged_without_their_message(lines):
    log = logging.getLogger("filex.test")
    try:
        raise ValueError(f"cannot open {NAME} with {PASSWORD}")
    except ValueError:
        log.exception("unexpected error", extra={"job": "j_x", "code": "INTERNAL_ERROR"})
    record = json.loads(lines.lines[-1][1])
    assert record["exc"].startswith("ValueError\n") and "test_logs.py" in record["exc"]
    assert record["job"] == "j_x" and record["code"] == "INTERNAL_ERROR"
    assert NAME not in lines.lines[-1][1] and PASSWORD not in lines.lines[-1][1]


def test_access_log_lines_lose_their_query_string():
    # uvicorn's access logger is not driven by TestClient; the filter on it is what runs.
    from filex.api.main import DropQueryString

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/v1/dl/j_x/result?exp=1&sig=SIGNATURE", "1.1", 200),
        None,
    )
    assert DropQueryString().filter(record)
    assert "SIGNATURE" not in JsonFormatter().format(record)
