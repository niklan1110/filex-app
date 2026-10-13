"""scripts/make_secrets.sh writes the same files, with the same permissions, as make_secrets.py."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = shutil.which("sh")

pytestmark = pytest.mark.skipif(SH is None, reason="needs a POSIX sh")


def _sh(folder: Path, *args: str) -> str:
    out = subprocess.run(
        [SH, str(ROOT / "scripts" / "make_secrets.sh"), "--dir", folder.as_posix(), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return out.stdout


def _py(folder: Path) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_secrets.py"), "--dir", str(folder)],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _contents(folder: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.iterdir())}


def test_same_names_and_password_shape_as_python(tmp_path):
    sh_dir, py_dir = tmp_path / "sh", tmp_path / "py"
    _sh(sh_dir)
    _py(py_dir)
    sh_files, py_files = _contents(sh_dir), _contents(py_dir)
    assert sh_files.keys() == py_files.keys() == {"redis_api_password", "redis_worker_password"}
    for value in sh_files.values():
        # docker/redis/start.sh needs at least 24 characters; no newline, nothing to strip.
        assert len(value) >= 32 and value.strip() == value and value.isalnum()
    assert len(set(sh_files.values())) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_same_permissions_as_python(tmp_path):
    sh_dir, py_dir = tmp_path / "sh", tmp_path / "py"
    _sh(sh_dir)
    _py(py_dir)
    mode = lambda p: stat.S_IMODE(p.stat().st_mode)
    assert mode(sh_dir) == mode(py_dir) == 0o700
    for name in ("redis_api_password", "redis_worker_password"):
        assert mode(sh_dir / name) == mode(py_dir / name) == 0o644


def test_keeps_existing_files_unless_forced(tmp_path):
    folder = tmp_path / "secrets"
    first = _sh(folder)
    assert first.count("wrote ") == 2
    before = _contents(folder)
    assert "already exist" in _sh(folder)
    assert _contents(folder) == before
    assert _sh(folder, "--force").count("wrote ") == 2
    after = _contents(folder)
    assert all(after[n] != before[n] for n in before)
