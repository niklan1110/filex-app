"""Step child: ``python -P -s -m filex.worker.step``, one process per step (ADR-0007 item 3).

Before anything else the child makes itself non-dumpable and says ``{"ready": true}``; only then
does the runner send the request (security review M-3). On Linux, stdin and stdout are one
AF_UNIX socket, which another process cannot open through ``/proc/<pid>/fd``. Then it reads one
JSON request (options, secrets, scratch input names and display names, output dirs, limits),
applies rlimits, the Landlock ruleset (its scratch dir and output dirs writable, system paths and
the import path read-only, nothing else) and the seccomp filter to itself, and runs the tool: ``run(ctx, src, opts)``
once per input with ``src`` a path when ``files.each``, else once with ``src`` the list of input
paths (empty for tools without inputs). Secrets arrive only on stdin, never in argv or the
environment (review H5). pydantic and the registry are imported after the handshake, so the
window before the child is non-dumpable stays short.

On success the child sends one ``{"output": [k, file, role, display name]}`` line per output,
then ``{"done": <count>}``. Events go to the runner as JSON lines on a private copy of the original stdout; fd 1 is pointed
at stderr so engine subprocesses cannot write into the event channel (the copy is not inherited).
The runner treats every event as untrusted.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from filex.worker import sandbox


class Context:
    """The ``StepContext`` a tool's ``run()`` gets."""

    def __init__(self, emit, req: dict[str, Any], names: list[str], scale: tuple[int, int]):
        self._emit, self.target, self.input_names = emit, req["target"], names
        self.aux = {slot: Path(name).resolve() for slot, name in req["aux"].items()}
        self.max_pages = int(req["max_pages"])
        self.max_pixels = int(req["max_pixels"])
        self._k, self._n = scale  # invocation k of n (per-file tools)
        self.out_dir = Path()

    def progress(self, fraction: float, message: str = "") -> None:
        f = min(max(float(fraction), 0.0), 1.0)
        self._emit(progress=(self._k + f) / self._n, message=str(message)[:200])

    def warn(self, code: str, message: str) -> None:
        self._emit(warn=[str(code), str(message)])


def main() -> int:
    events = os.fdopen(os.dup(1), "w", encoding="utf-8")  # os.dup: not inheritable
    os.dup2(2, 1)

    def emit(**event: Any) -> None:
        events.write(json.dumps(event) + "\n")
        events.flush()

    if sandbox.LINUX:
        try:
            sandbox.harden_step()
        except OSError:
            traceback.print_exc()
            return 1  # no "ready": the runner fails the step with SANDBOX_FAILED
    emit(ready=True)
    req = json.loads(sys.stdin.buffer.read(1 << 20))
    sys.stdin.close()

    from pydantic import ValidationError

    from filex.registry import MAX_OUTPUTS, StepError

    try:
        if os.name == "posix":
            sandbox.apply_rlimits(**req["limits"])
        if req.get("landlock"):  # the ABI the runner probed; it fails closed from here on
            rules = sandbox.landlock_ruleset(
                int(req["landlock"]), write=[os.getcwd(), *req["out_dirs"]], read=sys.path
            )
            sandbox.install_landlock(rules)
        if sandbox.LINUX:
            sandbox.install_seccomp()
    except (OSError, ValueError, KeyError):
        traceback.print_exc()
        emit(error={"code": "SANDBOX_FAILED", "message": "The step sandbox could not start."})
        return 1

    try:
        done = run(req, emit)
        # One line per output keeps every event far below the runner's 64 KB line cap; past
        # MAX_OUTPUTS the runner fails the step anyway, so one extra line is enough to show it.
        for item in done[: MAX_OUTPUTS + 1]:
            emit(output=item)
        emit(done=len(done))
        return 0
    except StepError as e:
        emit(error={"code": e.code, "message": e.message})
    except ValidationError:
        emit(error={"code": "INVALID_OPTIONS", "message": "The options are not valid."})
    except MemoryError:
        emit(error={"code": "ENGINE_MEMORY", "message": "The tool ran out of memory."})
    except Exception as e:  # noqa: BLE001 - any other tool failure is ENGINE_FAILED
        # Type and stack only: an exception message may quote a secret or a file name.
        print(type(e).__name__, "".join(traceback.format_tb(e.__traceback__)), file=sys.stderr)
        emit(error={"code": "ENGINE_FAILED", "message": "The tool failed on this file."})
    return 1


def run(req: dict[str, Any], emit) -> list[list[Any]]:
    """``[k, file name in out_dirs[k], role, display name]`` per output."""
    from filex.registry import Output, StepError, Tool

    mod = importlib.import_module(req["module"])
    tool = next(o for o in vars(mod).values() if isinstance(o, Tool) and o.id == req["tool"])
    opts = tool.options.model_validate({**req["options"], **req["secrets"]})
    inputs = [Path(name).resolve() for name in req["inputs"]]  # relative to cwd = scratch
    names = [str(n) for n in req["input_names"]]
    out_dirs = [Path(d) for d in req["out_dirs"]]
    calls = (
        [(p, d, [name]) for p, d, name in zip(inputs, out_dirs, names)]
        if tool.files.each
        else [(inputs, out_dirs[0], names)]
    )
    done = []
    for k, (src, out_dir, call_names) in enumerate(calls):
        ctx = Context(emit, req, call_names, (k, len(calls)))
        ctx.out_dir = out_dir
        for item in tool(ctx, src, opts) or []:
            wrapped = isinstance(item, Output)
            path = Path(item.path if wrapped else item)
            if path.parent.resolve() != out_dir.resolve():
                raise StepError("INVALID_OUTPUT", "The tool wrote an output outside its folder.")
            done.append(
                [k, path.name, item.role if wrapped else "RESULT", item.name if wrapped else ""]
            )
    return done


if __name__ == "__main__":
    sys.exit(main())
