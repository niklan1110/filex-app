"""The OpenAPI contract (ADR-0004): FastAPI's schema plus one ``POST /tools/<id>/jobs`` per tool.

``create_job`` reads its multipart body itself (``filex.api.upload``), so FastAPI cannot describe
it. Here the generic operation is replaced by one operation per tool with its typed options
schema (component ``<Tool>Options``), file counts, auxiliary slots, the ``target`` part of
converters (ADR-0005), and the job error and warning
codes it declares (``x-filex-errors``, ``x-filex-warnings``). Codes every job may carry are in
``info.x-filex-common-errors``. ``create_app`` serves this at
``/openapi.json``; ``scripts/gen_openapi.py`` commits it as ``docs/api/openapi.json``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from filex.registry import COMMON_ERRORS, RUNNER_ERRORS, Tool

JOBS_PATH = "/api/v1/tools/{toolId}/jobs"
CONVERT_JOBS_PATH = "/api/v1/convert/jobs"
_BINARY = {"type": "string", "contentMediaType": "application/octet-stream"}


def pascal(tool_id: str) -> str:
    """``pdf.to-pdfa`` -> ``PdfToPdfa``: the prefix of the tool's component names."""
    return "".join(w.capitalize() for w in re.split(r"[.-]", tool_id))


def build(app: FastAPI, tools: Mapping[str, Tool]) -> dict[str, Any]:
    # Source: https://fastapi.tiangolo.com/how-to/extending-openapi/. In FastAPI 0.143
    # app.routes holds an _IncludedRouter for /api/v1, which get_openapi expands (a test checks).
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schema["info"]["x-filex-common-errors"] = sorted(COMMON_ERRORS | RUNNER_ERRORS)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    paths: dict[str, Any] = {}
    for path, item in schema["paths"].items():
        if path == CONVERT_JOBS_PATH:
            paths[path] = {"post": _convert_operation(item["post"], tools)}
            continue
        if path != JOBS_PATH:
            paths[path] = item
            continue
        for t in tools.values():
            paths[f"/api/v1/tools/{t.id}/jobs"] = {"post": _job_operation(item["post"], t)}
            components |= _options_components(t)
    schema["paths"] = paths
    schema["components"]["schemas"] = dict(sorted(components.items()))
    return schema


def _convert_operation(generic: dict[str, Any], tools: Mapping[str, Tool]) -> dict[str, Any]:
    """``POST /convert/jobs`` (ADR-0005): files and a target; codes of every converter."""
    from filex.api.routes import CONVERT_MAX_FILES, convert_tool

    t = convert_tool(dict(tools))
    body = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": _BINARY,
                "minItems": 1,
                "maxItems": CONVERT_MAX_FILES,
                "description": "Input files. Formats: " + ", ".join(t.inputs) + ". Mixed "
                "formats go one route, so its first step must read all of them.",
            },
            "target": {
                "type": "string",
                "enum": list(t.outputs),
                "description": "Output format id. Required (422 TARGET_REQUIRED). No route "
                "among the workers online: 422 NO_ROUTE (see GET /convert/plan).",
            },
        },
        "required": ["files", "target"],
    }
    return generic | {
        "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": body}}},
        "x-filex-errors": list(t.errors),
        "x-filex-warnings": list(t.warnings),
    }


def _options_components(t: Tool) -> dict[str, Any]:
    name = f"{pascal(t.id)}Options"
    opts = t.options.model_json_schema(
        by_alias=True, ref_template=f"#/components/schemas/{name}{{model}}"
    )
    nested = {name + k: v for k, v in opts.pop("$defs", {}).items()}
    return nested | {name: opts | {"title": f"{t.title} options"}}


def _job_operation(generic: dict[str, Any], t: Tool) -> dict[str, Any]:
    op = copy.deepcopy(generic)
    op.pop("parameters", None)  # the tool id is part of the path now
    props: dict[str, Any] = {}
    required: list[str] = []
    if t.files.max:
        props["files"] = {
            "type": "array",
            "items": _BINARY,
            "minItems": t.files.min,
            "maxItems": t.files.max,
            "description": "Input files, in order. Formats: " + ", ".join(t.inputs) + ".",
        }
        if t.files.min:
            required.append("files")
    for slot, aux in t.files.aux.items():
        props[slot] = _BINARY | {
            "title": aux.title,
            "description": "Formats: " + ", ".join(aux.formats) + ".",
        }
        if aux.required:
            required.append(slot)
    props["options"] = {"$ref": f"#/components/schemas/{pascal(t.id)}Options"}
    if t.converts:
        fallback = (
            f"the `{t.options.model_fields[t.target_option].alias or t.target_option}` option"
            if t.target_option
            else "the only format every input converts to; 422 TARGET_REQUIRED if there are several"
        )
        props["target"] = {
            "type": "string",
            "enum": list(t.outputs),
            "description": "Output format id. Every input's format needs an edge to it (see "
            f"`converts` on GET /tools/{t.id}), else 422 INVALID_TARGET. Omitted: {fallback}.",
        }
    body: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        body["required"] = required
    op |= {
        "summary": t.title,
        "description": t.summary,
        "operationId": f"createJob{pascal(t.id)}",
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": body,
                    "encoding": {"options": {"contentType": "application/json"}},
                }
            },
        },
        "x-filex-errors": sorted(t.errors),
        "x-filex-warnings": sorted(t.warnings),
    }
    return op
