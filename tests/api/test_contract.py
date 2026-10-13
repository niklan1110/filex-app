"""The generated contract and tool docs (plan task 8, ADR-0004). CI runs the drift checks."""

import sys
from pathlib import Path

import pytest

from filex.api.deps import Settings
from filex.api.main import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gen_openapi
import gen_tool_docs


@pytest.mark.parametrize("script", [gen_openapi, gen_tool_docs], ids=["openapi", "tools-md"])
def test_committed_file_matches_the_code(script):
    assert script.main(["--check"]) == 0, f"run python scripts/{script.__name__}.py"


@pytest.mark.parametrize("script", [gen_openapi, gen_tool_docs], ids=["openapi", "tools-md"])
def test_check_fails_on_a_stale_or_missing_file(script, tmp_path):
    stale = tmp_path / "stale"
    stale.write_text(script.render() + "x", encoding="utf-8")
    assert script.main(["--check"], path=stale) == 1
    assert script.main(["--check"], path=tmp_path / "missing") == 1
    assert script.main([], path=stale) == 0
    assert script.main(["--check"], path=stale) == 0


def test_two_runs_are_byte_identical():
    assert gen_openapi.render() == gen_openapi.render()
    assert gen_tool_docs.render() == gen_tool_docs.render()


@pytest.fixture
def spec(tools):
    return create_app(Settings(auth="none"), tools=tools).openapi()


def test_every_router_route_is_in_the_contract(spec):
    # FastAPI 0.143 lists the /api/v1 router as an _IncludedRouter in app.routes.
    assert {
        "/api/v1/health",
        "/api/v1/tools",
        "/api/v1/tools/{toolId}",
        "/api/v1/formats",
        "/api/v1/jobs/{jobId}",
        "/api/v1/jobs/{jobId}/result",
        "/api/v1/jobs/{jobId}/outputs/{index}",
    } <= set(spec["paths"])
    params = spec["paths"]["/api/v1/jobs/{jobId}"]["get"]["parameters"]
    assert [p["name"] for p in params if p["in"] == "path"] == ["jobId"]


def test_one_typed_job_route_per_tool(spec, tools):
    assert "/api/v1/tools/{toolId}/jobs" not in spec["paths"]
    for tool_id in tools:
        assert f"/api/v1/tools/{tool_id}/jobs" in spec["paths"]
    op = spec["paths"]["/api/v1/tools/api.pdf/jobs"]["post"]
    assert op["operationId"] == "createJobApiPdf" and "parameters" not in op
    body = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["properties"]["files"]["minItems"] == 1
    assert body["properties"]["files"]["maxItems"] == 3
    assert body["properties"]["certificate"]["title"] == "Certificate"
    assert body["required"] == ["files"]  # the certificate slot is optional
    assert body["properties"]["options"] == {"$ref": "#/components/schemas/ApiPdfOptions"}
    options = spec["components"]["schemas"]["ApiPdfOptions"]
    assert options["properties"]["pageSize"]["enum"] == ["A4", "LETTER"]
    assert options["properties"]["password"]["writeOnly"] is True
    assert op["x-filex-errors"] == ["WRONG_PASSWORD"]
    assert "WORKER_LOST" in spec["info"]["x-filex-common-errors"]


def test_pdf_merge_has_its_own_options_schema():
    spec = create_app(Settings(auth="none")).openapi()
    op = spec["paths"]["/api/v1/tools/pdf.merge/jobs"]["post"]
    assert op["x-filex-errors"] == ["ENCRYPTED_PDF", "INVALID_PDF", "TOO_MANY_PAGES"]
    assert spec["components"]["schemas"]["PdfMergeOptions"]["properties"]["bookmarks"] == {
        "default": False,
        "title": "Add a bookmark for each file",
        "type": "boolean",
    }


def test_served_contract_is_the_generated_one(client):
    assert client.get("/openapi.json").json()["paths"].keys() >= {"/api/v1/tools/api.pdf/jobs"}


def test_no_swagger_ui_or_redoc_but_the_contract_is_served(client):
    """Review M-1: /docs and /redoc would load CDN script onto the FileX origin."""
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404, path
    assert client.get("/openapi.json").json()["info"]["title"] == "FileX"
