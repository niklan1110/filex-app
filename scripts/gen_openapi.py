"""Write the API contract generated from the code (ADR-0004).

    python scripts/gen_openapi.py           # rewrite docs/api/openapi.json
    python scripts/gen_openapi.py --check   # exit 1 if the committed file differs from the code

The contract comes from ``filex.api.main.create_app`` with the real tool registry, so it is the
same document the API serves at ``/openapi.json``. Output is stable: routes in declaration order,
tools sorted by id, components sorted by name, LF line endings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # run from a checkout without installing filex

OPENAPI = ROOT / "docs" / "api" / "openapi.json"


def render() -> str:
    from filex.api.deps import Settings
    from filex.api.main import create_app

    app = create_app(Settings(auth="none"))  # nothing connects to Redis while generating
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def write_or_check(path: Path, text: str, check: bool, script: str = "gen_openapi.py") -> int:
    """Write ``text`` to ``path``, or with ``check`` return 1 if ``path`` is stale."""
    shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    if check:
        try:
            current = path.read_bytes().decode("utf-8").replace("\r\n", "\n")  # git autocrlf
        except FileNotFoundError:
            current = None
        if current != text:
            print(f"{shown} is stale: run python scripts/{script}", file=sys.stderr)
            return 1
        print(f"{shown} is up to date")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # bytes: no CRLF on Windows
    print(f"wrote {shown}")
    return 0


def main(argv: list[str] | None = None, path: Path = OPENAPI) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)
    return write_or_check(path, render(), args.check)


if __name__ == "__main__":
    sys.exit(main())
