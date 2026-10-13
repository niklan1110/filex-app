"""Every registered tool's ``examples`` run through the real runner and step child, so under the
per-step seccomp filter and rlimits (plan task 53), in the tool's own worker image.

    FILEX_TEST_GROUP=core python -m pytest tests/integration/test_examples.py

Skipped unless FILEX_TEST_GROUP names a group: CI sets it inside each worker image
(scripts/ci/image-tests.sh). There a tool whose engine probe fails is an error, not a skip.
"""

import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import job, linux_only

from filex.formats import sniff
from filex.jobs import JobPaths, job_key, new_job_id, queue_key
from filex.registry import discover
from filex.worker import sandbox
from filex.worker.runner import Config, Runner

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

GROUP = os.environ.get("FILEX_TEST_GROUP", "")
TOOLS = {i: t for i, t in discover().items() if t.group == GROUP} if GROUP else {}
CASES = [(t.id, n) for t in TOOLS.values() for n in range(len(t.examples))]

pytestmark = [
    linux_only,
    pytest.mark.skipif(not GROUP, reason="set FILEX_TEST_GROUP (runs in the worker images)"),
]


@pytest.fixture(scope="module")
def runner_tools():
    assert sandbox.seccomp_available(), "libseccomp missing: examples would run unfiltered"
    return TOOLS


@pytest.mark.parametrize(("tool_id", "n"), CASES or [pytest.param("", 0, marks=pytest.mark.skip)])
def test_example_succeeds_under_the_filter(r, data, runner_tools, tool_id, n):
    tool = runner_tools[tool_id]
    ex = tool.examples[n]
    runner = Runner(r, Config(group=GROUP, data=data, heavy_slots=0), tools=runner_tools)
    assert tool_id in runner.tools, f"{tool_id}: an engine probe failed in the {GROUP} image"

    job_id = new_job_id()
    paths = JobPaths(data, job_id)
    paths.in_dir.mkdir(parents=True)
    inputs = []
    for i, rel in enumerate(ex.inputs):
        src = CORPUS / rel
        fmt = sniff(src, src.name)
        shutil.copyfile(src, paths.input(i, fmt))
        inputs.append({"format": fmt, "name": src.name})
    step = {"tool": tool_id, "options": dict(ex.options), "secret": False, "target": ex.target}
    r.hset(
        job_key(job_id),
        mapping={"status": "QUEUED", "steps": json.dumps([step]), "inputs": json.dumps(inputs)},
    )
    r.xadd(queue_key(GROUP), {"job": job_id, "step": "0"})

    assert runner.run_once()
    result = job(r, job_id)
    assert result["status"] == "SUCCEEDED", result.get("error")
