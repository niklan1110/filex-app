"""Generic per-tool checks that need files or a subprocess (ADR-0003).

The declaration rules (formats, groups, schema subset, secrets, converters, example options)
are enforced by ``filex.registry.tool`` itself, so a bad declaration fails at import.
"""

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

from filex.formats import sniff
from filex.registry import COMMON_ERRORS, Tool

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "corpus"
TOOL_TESTS = ROOT / "tests" / "tools"

_PROBE = """
import importlib, json, sys
importlib.import_module("filex.registry")
before = {m.split(".")[0] for m in sys.modules}
importlib.import_module(sys.argv[1])
new = {m.split(".")[0] for m in sys.modules} - before
own = sys.argv[1].split(".")[0]
print(json.dumps(sorted(new - set(sys.stdlib_module_names) - {"filex", own})))
"""


def module_level_imports(module: str) -> set[str]:
    """Top-level packages, other than the stdlib, pydantic and filex, that importing
    ``module`` pulls in. Runs in a fresh interpreter so nothing is already cached."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, module],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=60,
    )
    return set(json.loads(out.stdout))


def tool_test_path(tool_id: str) -> Path:
    return TOOL_TESTS / f"test_{tool_id.replace('.', '_').replace('-', '_')}.py"


def example_problems(t: Tool) -> list[str]:
    """Each example's corpus inputs exist and sniff to a format the tool accepts; a converter
    example's target is an edge from that format."""
    problems = []
    for i, ex in enumerate(t.examples):
        for rel in ex.inputs:
            path = CORPUS / rel
            if not path.is_file():
                problems.append(f"example {i}: {rel} is not in tests/corpus")
            elif (fmt := sniff(path, path.name)) not in t.inputs:
                problems.append(f"example {i}: {rel} is {fmt}, tool accepts {list(t.inputs)}")
            elif t.converts and not any(e.src == fmt and e.dst == ex.target for e in t.converts):
                problems.append(f"example {i}: no edge {fmt}->{ex.target} for target {ex.target!r}")
    return problems


def undeclared_errors(t: Tool) -> set[str]:
    """``StepError("CODE", ...)`` codes in the tool's module that are neither common nor in
    its ``errors`` (the runner would turn them into ENGINE_FAILED)."""
    source = Path(importlib.import_module(t.module).__file__).read_text(encoding="utf-8")
    raised = {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "StepError"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    return raised - COMMON_ERRORS - set(t.errors)
