"""Engine processes for the ebook tools: Calibre ``ebook-convert``, Pandoc, ``ddjvu`` and
``djvused`` (ADR-0007 Appendix A).

Every engine runs in its own process group inside the step's session, with stdin from
``/dev/null``; on our timeout or any error the whole group is killed (Calibre starts Qt
WebEngine helpers). A new session would count as an escape for the runner's sweep, so it is
never used. Console output goes to the step log (stderr) only, never to the user: failures map
to our own messages.

Calibre gets fresh config, temp and cache dirs inside the work dir (so no plugin, setting or
cache outlives a step), headless Qt and Chromium flags. The step child's environment is an
allowlist, so everything Calibre needs is set here, not inherited from the image.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from filex.formats import FORMATS
from filex.registry import StepError

LOG_TAIL = 4000  # characters of engine output kept for failure messages and the step log


def run(
    args: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float,
    what: str,
    engine: str,
    env: Mapping[str, str] | None = None,
    stdout_only: bool = False,
) -> tuple[int, str]:
    """Run one engine process; return (exit status, the tail of its stdout + stderr). With
    ``stdout_only``, all of stdout instead, and stderr goes to the step log. A timeout kills
    the process group and is ``ENGINE_FAILED``."""
    full_env = {**os.environ, "HOME": str(cwd), "TMPDIR": str(cwd), **(env or {})}
    try:
        proc = subprocess.Popen(
            list(args),
            cwd=cwd,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None if stdout_only else subprocess.STDOUT,
            process_group=0,
        )
    except OSError as e:
        raise StepError("ENGINE_FAILED", f"{engine} could not start.") from e
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise StepError("ENGINE_FAILED", f"{what} took too long to convert.") from e
    finally:
        kill_group(proc.pid)
        proc.wait()
    text = out.decode("utf-8", "replace")
    if stdout_only:
        return proc.returncode, text
    log = text[-LOG_TAIL:]
    if proc.returncode:
        sys.stderr.write(f"{engine} exited with {proc.returncode}:\n{log}\n")
    return proc.returncode, log


def kill_group(pgid: int) -> None:
    """SIGKILL the group, a few times: a process forked while the first signal was sent can
    miss it. Usually the group is already empty."""
    for _ in range(3):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)


def calibre_env(work: Path) -> dict[str, str]:
    """Calibre's environment, with its config, temp and cache dirs created under ``work``."""
    dirs = {
        "CALIBRE_CONFIG_DIRECTORY": work / "calibre" / "config",
        "CALIBRE_TEMP_DIR": work / "calibre" / "tmp",
        "CALIBRE_CACHE_DIRECTORY": work / "calibre" / "cache",
        "XDG_CACHE_HOME": work / "cache",
        "XDG_CONFIG_HOME": work / "config",
        "XDG_RUNTIME_DIR": work / "runtime",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        **{k: str(v) for k, v in dirs.items()},
        "QT_QPA_PLATFORM": "offscreen",
        "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu --disable-dev-shm-usage",
        "QTWEBENGINE_DISABLE_SANDBOX": "1",
    }


def memory_failure(code: int, log: str) -> bool:
    """True when an engine most likely ran out of memory: killed by SIGKILL (the cgroup's OOM
    killer) or an allocation error in its output."""
    markers = (
        "MemoryError",
        "bad_alloc",
        "Cannot allocate memory",
        "out of memory",
        "Heap exhausted",
    )
    return code == -getattr(signal, "SIGKILL", 9) or any(m in log for m in markers)


_BY_EXT = {f.extensions[0]: f.id for f in FORMATS.values()}


def format_of(src: Path, allowed: Sequence[str]) -> str:
    """The input's format. The runner names scratch inputs ``<n><extension>`` after the sniffed
    format, so the suffix is the format, never the user's file name."""
    fmt = _BY_EXT.get(src.suffix.lower())
    if fmt not in allowed:
        raise StepError("INVALID_INPUT", "This file type cannot be opened by this tool.")
    return fmt


def title(fmt: str) -> str:
    return FORMATS[fmt].title if fmt in FORMATS else fmt
