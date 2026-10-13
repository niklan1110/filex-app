"""Check that a worker image (or the API image) has every third-party module its code imports.

    docker run --rm --network none --read-only -v "$PWD/scripts/ci:/ci:ro" \
        filex-worker-office:local python /ci/check_imports.py office

Run it against the BARE production image, before any test layer: the test layer pip-installs
the dev extras, which hid a runtime dependency missing from worker-office (defusedxml).

What it checks: the group's tools (``filex.registry.discover``) and the worker modules are
AST-scanned, including imports inside functions (engine imports live in ``run()``), following
``filex`` imports into the shared helpers. A helper is scanned only as far as the code actually
uses it: ``from filex.tools._pdf import label`` follows ``label`` and what it calls, not every
function in ``_pdf``. Each third-party top-level module found is then really imported; anything
that fails (not installed, or a missing system library) is listed with who needs it.

The group ``api`` checks the API image the same way: ``filex.api.main`` and what it reaches, the
real import of every tool module (the API imports them all through ``discover``), and the modules
that only the image's CMD names (uvicorn). Engine imports inside ``run()`` never run in the API.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from collections.abc import Iterable
from pathlib import Path

WORKER_MODULES = ("filex.worker.__main__", "filex.worker.runner", "filex.worker.step")
# Imported by name outside the code, so the AST scan cannot see them.
EXTRA = {"api": {"uvicorn": "CMD in docker/Dockerfile"}}
WHOLE = "*"  # symbol: every function in the module
TOP = None  # symbol: only the module's top-level statements (what importing it runs)
# Reachable in the code but not in that group's jobs. Each entry says why; keep it short.
UNREACHABLE = {
    # _lo.convert calls _archive.check, whose RAR branch needs libarchive, only for inputs
    # sniffed as zip packages (OOXML, ODF), so a RAR header never gets there.
    ("office", "libarchive"),
}


def _source(module: str) -> Path | None:
    spec = importlib.util.find_spec(module)
    return Path(spec.origin) if spec and spec.origin and spec.origin.endswith(".py") else None


def _is_module(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ImportError:  # a parent is not a package
        return False


def _absolute(module: str, node: ast.ImportFrom, is_pkg: bool) -> str:
    if not node.level:
        return node.module or ""
    parts = module.split(".")
    base = parts[: len(parts) - node.level + (1 if is_pkg else 0)]
    return ".".join(base + ([node.module] if node.module else []))


class _Module:
    """One parsed filex module: its top-level definitions and module aliases."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        self.is_pkg = path.name == "__init__.py"
        self.defs = {
            n.name: n
            for n in self.tree.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        self.top = [n for n in self.tree.body if n not in self.defs.values()]
        self.aliases: dict[str, str] = {}  # local name -> filex module it is bound to
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                src = _absolute(name, node, self.is_pkg)
                for a in node.names:
                    if src.split(".")[0] == "filex" and _is_module(f"{src}.{a.name}"):
                        self.aliases[a.asname or a.name] = f"{src}.{a.name}"


def requirements(entries: Iterable[tuple[str, str | None]]) -> dict[str, set[str]]:
    """Third-party top-level module -> the ``module:symbol`` places that import it."""
    found: dict[str, set[str]] = {}
    parsed: dict[str, _Module | None] = {}
    seen: set[tuple[str, str | None]] = set()
    todo = list(entries)
    while todo:
        module, symbol = todo.pop()
        if (module, symbol) in seen:
            continue
        seen.add((module, symbol))
        if module not in parsed:
            path = _source(module)
            parsed[module] = _Module(module, path) if path else None
        m = parsed[module]
        if m is None:
            continue
        if symbol == WHOLE:
            nodes, where = m.tree.body, module
        elif symbol in m.defs:
            nodes, where = [m.defs[symbol]], f"{module}:{symbol}"
            todo.append((module, TOP))
        else:  # TOP, or a constant or alias the top level defines
            nodes, where = m.top, module

        def need(top: str, where: str = where) -> None:
            if top and top not in sys.stdlib_module_names and top != "filex":
                found.setdefault(top, set()).add(where)

        for node in (n for root in nodes for n in ast.walk(root)):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] == "filex":
                        todo.append((a.name, WHOLE))
                    need(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                src = _absolute(module, node, m.is_pkg)
                if src.split(".")[0] != "filex":
                    need(src.split(".")[0])
                    continue
                todo.append((src, TOP))  # importing anything runs the module's top level
                for a in node.names:
                    sub = f"{src}.{a.name}"
                    todo.append((sub, TOP) if _is_module(sub) else (src, a.name))
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in m.aliases:
                    todo.append((m.aliases[node.value.id], node.attr))
            elif isinstance(node, ast.Name) and node.id in m.defs:
                todo.append((module, node.id))
    return found


def missing(reqs: dict[str, set[str]]) -> dict[str, str]:
    """Import each module for real; return the ones that fail with the error."""
    bad: dict[str, str] = {}
    for top in sorted(reqs):
        try:
            importlib.import_module(top)
        except Exception as e:  # noqa: BLE001 - a broken native lib raises OSError and others
            bad[top] = f"{type(e).__name__}: {e}"
    return bad


def group_entries(group: str) -> list[tuple[str, str | None]]:
    from filex.registry import discover  # imports every tool module's top level for real

    if group == "api":
        discover()  # what the API does at startup; a tool module that fails to import fails here
        return [("filex.api.main", WHOLE)]
    tools = [t for t in discover().values() if t.group == group]
    if not tools:
        raise SystemExit(f"no tools in group {group!r}")
    return [(t.module, WHOLE) for t in tools] + [(w, WHOLE) for w in WORKER_MODULES]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_imports.py <group>", file=sys.stderr)
        return 2
    reqs = requirements(group_entries(argv[0]))
    for top, where in EXTRA.get(argv[0], {}).items():
        reqs.setdefault(top, set()).add(where)
    for g, top in UNREACHABLE:
        if g == argv[0]:
            reqs.pop(top, None)
    bad = missing(reqs)
    if bad:
        print(f"{argv[0]}: {len(bad)} third-party module(s) missing from the image:")
        for top, err in bad.items():
            print(f"  {top}  ({err})")
            for where in sorted(reqs[top]):
                print(f"      needed by {where}")
        return 1
    print(f"{argv[0]}: all {len(reqs)} third-party modules import: {', '.join(sorted(reqs))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
