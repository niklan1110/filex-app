"""Dummy tools that exercise the worker runner (task 5). Test fixtures, never shipped."""

import hashlib
import json
import os
import shutil
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr

from filex.registry import (
    Aux,
    Files,
    Limits,
    Output,
    Report,
    Secret,
    StepContext,
    StepError,
    ToolOptions,
    tool,
)

_COMMON = {"category": "organize", "inputs": ["pdf"], "outputs": ["pdf"]}


def _copy(ctx: StepContext, src: Path | list[Path]) -> Path:
    src = src[0] if isinstance(src, list) else src  # a list unless files.each
    out = ctx.out_dir / "copy.pdf"
    shutil.copyfile(src, out)
    return out


@tool(id="t.copy", title="Copy", summary="Copies.", group="core", **_COMMON)
def copy(ctx, src, opts):
    ctx.progress(0.5, "halfway")
    return [_copy(ctx, src)]


@tool(id="t.copy-office", title="Copy", summary="Copies.", group="office", **_COMMON)
def copy_office(ctx, src, opts):
    return [_copy(ctx, src)]


@tool(
    id="t.each",
    title="Each",
    summary="Copies each file.",
    group="core",
    files=Files(min=1, max=3, each=True),
    **_COMMON,
)
def each(ctx, src, opts):
    return [_copy(ctx, src)]


class SleepOptions(ToolOptions):
    seconds: int = Field(30, title="Seconds", ge=0, le=60)


@tool(
    id="t.sleep",
    title="Sleep",
    summary="Sleeps.",
    group="core",
    options=SleepOptions,
    limits=Limits(timeout_s=2, memory_mb=64),
    **_COMMON,
)
def sleep(ctx, src, opts):
    time.sleep(opts.seconds)
    return [_copy(ctx, src)]


@tool(
    id="t.hog",
    title="Hog",
    summary="Eats memory.",
    group="core",
    limits=Limits(timeout_s=20, memory_mb=64),
    **_COMMON,
)
def hog(ctx, src, opts):
    blob = bytearray(512 * 1024 * 1024)
    return [_copy(ctx, src)] if blob else []


class InspectOptions(ToolOptions):
    password: SecretStr | None = Secret(title="Password", optional=True)
    fail: bool = Field(False, title="Fail")


@tool(
    id="t.inspect",
    title="Inspect",
    summary="Reports what the step can see.",
    group="core",
    options=InspectOptions,
    files=Files(
        aux={"certificate": Aux(formats=["p12"], title="Cert", sensitive=True, required=False)}
    ),
    **_COMMON,
)
def inspect(ctx, src, opts):
    secret = opts.password.get_secret_value() if opts.password else ""
    proc, linux = {}, {}
    if sys.platform.startswith("linux"):
        import ctypes

        for name in ("cmdline", "environ"):
            try:  # root-owned once the step is not dumpable, so unreadable as well
                seen = Path(f"/proc/self/{name}").read_bytes()
            except PermissionError:
                seen = b""
            proc[name] = bool(secret) and secret.encode() in seen
        linux = {
            "dumpable": ctypes.CDLL(None).prctl(3, 0, 0, 0, 0),  # PR_GET_DUMPABLE
            "oom_score_adj": Path("/proc/self/oom_score_adj").read_text().strip(),
        }
    info = {
        "cwd": os.getcwd(),
        "listing": sorted(os.listdir(os.getcwd())),
        "home": os.environ.get("HOME"),
        "env": sorted(os.environ),
        "aux": {slot: p.name for slot, p in ctx.aux.items()},
        "secret_sha256": hashlib.sha256(secret.encode()).hexdigest() if secret else None,
        "secret_in_proc": proc,
        "input_names": list(ctx.input_names),
        "max_pages": ctx.max_pages,
        "max_pixels": ctx.max_pixels,
        "linux": linux,
    }
    if opts.fail:
        raise StepError("INVALID_INPUT", "Told to fail.")
    report = ctx.out_dir / "report.json"
    report.write_text(json.dumps(info))
    return [_copy(ctx, src), Report(report)]


@tool(
    id="t.warn",
    title="Warn",
    summary="Warns a lot.",
    group="core",
    warnings=["SOMETHING", "LONG"],
    **_COMMON,
)
def warn(ctx, src, opts):
    ctx.warn("LONG", "x" * 400)
    ctx.warn("UNDECLARED", "dropped")
    ctx.warn("UNDECLARED", "dropped again")
    for i in range(25):
        ctx.warn("SOMETHING", f"warning {i}")
    report = ctx.out_dir / "report.json"
    report.write_text("{}")
    return [_copy(ctx, src), Report(report)]


class LinkOptions(ToolOptions):
    mode: Literal["SYMLINK", "HARDLINK", "OUTSIDE"] = Field("SYMLINK", title="Mode")


@tool(
    id="t.link",
    title="Link",
    summary="Leaves a link as output.",
    group="core",
    options=LinkOptions,
    **_COMMON,
)
def link(ctx, src, opts):
    out = ctx.out_dir / "out.pdf"
    if opts.mode == "SYMLINK":
        out.symlink_to(src[0])
    elif opts.mode == "HARDLINK":
        os.link(src[0], out)
    else:
        return [src[0]]  # a path outside its output dir
    return [out]


@tool(id="t.escape", title="Escape", summary="Leaves a process.", group="core", **_COMMON)
def escape(ctx, src, opts):
    r, w = os.pipe()
    if os.fork() == 0:  # child: new session, then fork the sleeper and exit
        os.setsid()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            os.close(w)
            time.sleep(10)
            os._exit(0)
        os.write(w, str(pid).encode())
        os._exit(0)
    os.close(w)
    pid = int(os.read(r, 32))
    report = ctx.out_dir / "report.json"
    report.write_text(json.dumps({"pid": pid}))
    return [_copy(ctx, src), Report(report)]


@tool(id="t.helper", title="Helper", summary="Runs an engine helper.", group="core", **_COMMON)
def helper(ctx, src, opts):
    from filex.tools.optimize.pdf_compress import run_ghostscript

    # Stands in for Ghostscript: fails the step (ENGINE_FAILED) if a sweep kills it.
    run_ghostscript([sys.executable, "-c", "import time; time.sleep(1.5)"], 30, "The file")
    return [_copy(ctx, src)]


class NetOptions(ToolOptions):
    family: Literal["INET", "UNIX", "ALG"] = Field("INET", title="Family")


@tool(
    id="t.net", title="Net", summary="Opens a socket.", group="core", options=NetOptions, **_COMMON
)
def net(ctx, src, opts):
    if opts.family == "INET":
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()  # seccomp: PermissionError
    elif opts.family == "ALG":
        socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET).close()  # not AF_UNIX: denied
    else:
        a, b = socket.socketpair(socket.AF_UNIX)
        a.close()
        b.close()
    return [_copy(ctx, src)]


@tool(
    id="t.probed",
    title="Probed",
    summary="Needs an engine.",
    group="core",
    engines=["oldtool"],
    **_COMMON,
)
def probed(ctx, src, opts):
    return [_copy(ctx, src)]


@tool(id="t.stop", title="Stop", summary="Signals its runner.", group="core", **_COMMON)
def stop(ctx, src, opts):
    """Tries to stop the runner (security review H-1). If that works, it continues the runner
    at once, so a failing test cannot leave pytest frozen."""
    parent, result = os.getppid(), {}
    try:
        os.kill(parent, signal.SIGSTOP)
        os.kill(parent, signal.SIGCONT)
        result["kill"] = "allowed"
    except PermissionError:
        result["kill"] = "denied"
    try:
        fd = os.pidfd_open(parent)
        signal.pidfd_send_signal(fd, signal.SIGCONT)
        result["pidfd"] = "allowed"
    except PermissionError:
        result["pidfd"] = "denied"
    report = ctx.out_dir / "report.json"
    report.write_text(json.dumps(result))
    return [_copy(ctx, src), Report(report)]


class LiarOptions(ToolOptions):
    code: str = Field("WORKER_LOST", title="Code", max_length=40)


@tool(
    id="t.liar",
    title="Liar",
    summary="Sends events the runner must not trust.",
    group="core",
    options=LiarOptions,
    errors=["WRONG_PASSWORD"],
    **_COMMON,
)
def liar(ctx, src, opts):
    ctx._emit(progress=float("nan"), message="nan")
    time.sleep(0.3)  # the runner writes the progress before it sees the error
    raise StepError(opts.code, "A code the runner did not give this tool.")


class ManyOptions(ToolOptions):
    count: int = Field(2, ge=0, le=5000, title="Count")
    names: str = Field("", title="Names, split at |", max_length=1000)


@tool(
    id="t.many",
    title="Many",
    summary="Makes count outputs, named from names when given.",
    group="core",
    options=ManyOptions,
    **_COMMON,
)
def many(ctx, src, opts):
    names = opts.names.split("|") if opts.names else []
    out = []
    for n in range(opts.count):
        path = ctx.out_dir / f"{n}.pdf"
        shutil.copyfile(src[0], path)
        out.append(Output(path, names[n]) if n < len(names) else path)
    return out


class PeekOptions(ToolOptions):
    path: str = Field("", title="Path", max_length=1000)


@tool(
    id="t.peek",
    title="Peek",
    summary="Tries to read, list and write a path outside its job.",
    group="core",
    options=PeekOptions,
    **_COMMON,
)
def peek(ctx, src, opts):
    import errno

    def attempt(action) -> str:
        try:
            action()
        except OSError as e:
            return errno.errorcode.get(e.errno or 0, str(e.errno))
        return "ok"

    target = Path(opts.path)
    info = {
        "read": attempt(target.read_bytes),
        "list": attempt(lambda: os.listdir(target.parent)),
        "write": attempt(lambda: (target.parent / "planted").write_bytes(b"x")),
        "tmp": attempt(lambda: Path("/tmp/filex-peek").write_bytes(b"x")),
        "own_input": attempt(lambda: Path(src[0]).read_bytes()),
    }
    report = ctx.out_dir / "report.json"
    report.write_text(json.dumps(info))
    return [_copy(ctx, src), Report(report)]
