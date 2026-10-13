"""Ghostscript runner shared by ``pdf.compress``, ``pdf.repair`` and ``pdf.to-pdfa``.

Settings (review H4, ADR-0007 Appendix A): ``-dSAFER -dBATCH -dNOPAUSE -dQUIET``,
``--permit-file-read=`` for the input (and nothing else the job controls), input and output
inside a private temp dir (``TMPDIR`` is the step's scratch dir in the worker), no shell, stdin
from ``/dev/null``, own process group killed on timeout, only files starting with ``%PDF-``
(:func:`require_pdf`). Minimum version 10.03.1 is the worker's engine probe. Ghostscript's
console output goes to the step log only, never into a message.

Engine imports: none (stdlib only), so tool modules may import this at module level.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from filex.registry import StepError


def base_args(src: Path, gs: str = "gs") -> list[str]:
    """The sandboxed start of every pdfwrite command line; the caller appends its settings,
    ``-sOutputFile=`` and ends with ``-f <src>``. Never a shell string."""
    return [
        gs,
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        "-dQUIET",
        f"--permit-file-read={src}",
        "-sDEVICE=pdfwrite",
    ]


def require_pdf(path: Path, what: str) -> None:
    """INVALID_PDF unless ``path`` starts with ``%PDF-``: Ghostscript would run anything else
    as PostScript, so nothing else may reach it (ADR-0007 Appendix A)."""
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise StepError("INVALID_PDF", f"{what} is not a readable PDF.")


def run_ghostscript(
    args: list[str],
    timeout_s: float,
    *,
    failed: str,
    slow: str,
    unavailable: str = "The PDF engine could not start.",
    failed_code: str = "ENGINE_FAILED",
) -> None:
    """Run Ghostscript in its own process group; kill the group on timeout. A non-zero exit
    raises ``failed_code`` with the ``failed`` message, a timeout ENGINE_FAILED with ``slow``;
    Ghostscript's stderr goes to the step log only.

    A process group, not a session: the runner's sweep kills any descendant outside a running
    step's session (ADR-0007 item 2), so ``start_new_session`` would get Ghostscript killed
    mid-step. Same as ``filex.tools.office._lo``."""
    posix = os.name == "posix"
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            process_group=0 if posix else None,
            creationflags=0 if posix else subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except OSError as e:
        raise StepError("ENGINE_FAILED", unavailable) from e
    try:
        _, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        kill_group(proc)
        raise StepError("ENGINE_FAILED", slow) from e
    finally:
        if proc.poll() is None:
            kill_group(proc)
    if proc.returncode != 0:
        sys.stderr.write(err[-2000:].decode("utf-8", "replace"))
        raise StepError(failed_code, failed)


def kill_group(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()
