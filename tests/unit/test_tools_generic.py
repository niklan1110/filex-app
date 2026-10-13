"""Generic tests over every registered tool in ``filex.tools`` (ADR-0003, architecture 6).

Loading the registry already enforces unique ids, known groups/categories/formats, the
options-schema subset, write-only secrets, converter rules and valid example options: a bad
declaration makes this module fail to import. The tests below add what needs files or a
fresh interpreter. They are skipped while ``filex.tools`` is still empty.
"""

import importlib
import inspect
import re
import typing
from pathlib import Path

import pytest
from tool_checks import (
    example_problems,
    module_level_imports,
    tool_test_path,
    undeclared_errors,
)

from filex.registry import discover

TOOLS = discover("filex.tools")


def test_registry_loads():
    assert all(t.id == tid for tid, t in TOOLS.items())


@pytest.mark.parametrize("t", TOOLS.values(), ids=list(TOOLS))
def test_module_imports_without_engines(t):
    assert module_level_imports(t.module) == set(), "move engine imports inside run()"


@pytest.mark.parametrize("t", TOOLS.values(), ids=list(TOOLS))
def test_tool_has_a_test_file(t):
    assert tool_test_path(t.id).is_file()


@pytest.mark.parametrize("t", TOOLS.values(), ids=list(TOOLS))
def test_examples_use_corpus_files_the_tool_accepts(t):
    assert example_problems(t) == []


@pytest.mark.parametrize("t", TOOLS.values(), ids=list(TOOLS))
def test_every_raised_error_code_is_declared_or_common(t):
    assert undeclared_errors(t) == set(), "add the codes to errors=[...] in @tool"


def test_no_tool_module_starts_a_new_session():
    """The runner's sweep kills descendants outside a running step's session (ADR-0007 item 2),
    so engines run in a process group (``process_group=0``), never a session of their own."""
    pkg = importlib.import_module("filex.tools")
    found = []
    for path in sorted(Path(pkg.__path__[0]).rglob("*.py")):
        code = re.sub(r"(?m)#.*$", "", path.read_text(encoding="utf-8"))
        code = re.sub(r'(?s)""".*?"""', "", code)  # docstrings may explain why
        if re.search(r"start_new_session|setsid", code):
            found.append(path.name)
    assert found == []


@pytest.mark.parametrize("t", TOOLS.values(), ids=list(TOOLS))
def test_run_takes_what_the_step_child_passes(t):
    """The step child calls ``run(ctx, src, opts)`` with one path per call when ``files.each``,
    else with the list of input paths, also for ``Files(min=1, max=1)`` (task 64: pdf.organize
    declared ``src: Path`` without ``each`` and every job failed)."""
    src = list(inspect.signature(t.fn).parameters)[1]
    hint = typing.get_type_hints(t.fn)[src]
    assert hint == (Path if t.files.each else list[Path]), (
        f"src is {hint}: declare files=Files(..., each=True) or take list[Path]"
    )
