"""Worker runner (ADR-0001, ADR-0002, ADR-0007; key layout in ``filex.jobs``).

One runner per worker container serves one group: it reads ``q:<group>`` with the consumer
group ``workers``, runs each step in a fresh child (``filex.worker.step``) with a timeout and
rlimits, reports progress into ``job:<id>``, then hands off to the next step's stream or
finishes the job, and ``XACK``s + ``XDEL``s the entry in the same ``MULTI``.

- Redelivery: in-flight entries are touched with ``XCLAIM ... JUSTID`` every 10 s (no delivery
  bump), so ``XAUTOCLAIM`` only picks up entries of dead workers. Third delivery: WORKER_LOST.
- Cancel: the job hash going ``CANCELLED`` (or away) kills the step's process group within a
  second; the runner then writes nothing more to the job and removes its ``in/``, ``steps/``,
  ``out/`` and remaining ``sec:*`` keys. The API's cancel only marks the job.
- Heavy groups (all but ``core``) take a ``slot:heavy:<i>`` lease before the child starts.
- After every step and every 5 s, descendants outside a running step's session are killed and
  zombies are reaped. In the image the runner is PID 1 (no ``init``), see ADR-0007.
- Job writes use WATCH/MULTI (the ``worker`` ACL needs ``+@transaction``; no Lua) and never
  recreate a deleted or terminal job hash.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import redis

from filex.formats import FORMATS, sniff
from filex.jobs import (
    GROUP,
    TERMINAL,
    JobPaths,
    ext,
    job_key,
    queue_key,
    retention_minutes,
    sec_key,
    valid_job_id,
)
from filex.registry import COMMON_ERRORS, GROUPS, MAX_OUTPUTS, Tool, discover
from filex.worker import sandbox

log = logging.getLogger("filex.worker")

WAITING = "waiting for a free slot"
MAX_WARNINGS, MAX_WARNING_CHARS = 20, 300
MAX_DELIVERIES = 2
SLOT_PX = 30_000
TOUCH_S = 10  # keep in-flight entries and slot leases alive, check free disk space
CANCEL_CHECK_S = 0.5
READY_S = 30  # the child's interpreter start-up, up to its "ready"
STEP_OVERHEAD_MB = 64  # the child's interpreter, pydantic and filex, on top of memory_mb
STDERR_BYTES = 2048  # the step's stderr tail kept for the DEBUG log, never shown to users
_POSIX = os.name == "posix"
_OPEN_OUTPUT = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_OPEN_OUTPUT |= getattr(os, "O_BINARY", 0)
# Hard links never follow a symlink (Windows' CreateHardLink does not either).
_link = (
    partial(os.link, follow_symlinks=False) if os.link in os.supports_follow_symlinks else os.link
)


class StepFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code, self.message = code, message


class Cancelled(Exception):
    """The job was cancelled or deleted while its step waited or ran."""


@dataclass(frozen=True)
class Config:
    group: str
    data: Path = Path("/data")
    concurrency: int = 1
    heavy_slots: int = 1
    retention_s: int = 1440 * 60
    min_free_mb: int = 0  # a step is stopped below this much free space on the data volume
    max_pages: int = 2000
    max_pixels: int = 100_000_000  # per image or rendered page (review H3 fix 4)
    claim_idle_ms: int = 60_000
    fsize_mb: int = 2048  # RLIMIT_FSIZE per output file
    max_output_bytes: int = 4096 * sandbox.MiB  # all outputs of one step together
    landlock: str = "auto"  # FILEX_LANDLOCK: auto | required | off (ADR-0007 item 11)

    @classmethod
    def from_env(cls, group: str) -> Config:
        e = os.environ
        return cls(
            group=group,
            data=Path(e.get("FILEX_DATA", "/data")),
            concurrency=max(1, int(e.get("FILEX_CONCURRENCY", "1"))),
            heavy_slots=max(0, int(e.get("FILEX_HEAVY_SLOTS", "1"))),
            retention_s=retention_minutes(e.get("FILEX_RETENTION_MIN", "1440")) * 60,
            # The API refuses uploads below FILEX_MIN_FREE_MB; running steps may use half of
            # that reserve, so a job admitted near the threshold still gets to finish.
            min_free_mb=max(0, int(e.get("FILEX_MIN_FREE_MB", "4096"))) // 2,
            max_pages=max(1, int(e.get("FILEX_MAX_PAGES", "2000"))),
            max_pixels=max(1, int(e.get("FILEX_MAX_PIXELS", "100000000"))),
            landlock=e.get("FILEX_LANDLOCK", "auto"),
        )


@dataclass(frozen=True)
class StepRun:
    """One stream entry: step ``n`` of job ``job_id``."""

    entry_id: str
    job_id: str
    n: int
    steps: list[dict]  # the job's step specs; [] when the job record is gone or damaged
    paths: JobPaths

    @property
    def spec(self) -> dict:
        return self.steps[self.n]


# --- engine probes (review H4 fix 3)


@dataclass(frozen=True)
class Probe:
    min_version: str
    cmd: tuple[str, ...]  # prints the version


# Every engine a tool declares needs a probe here; a tool with an unprobed engine is unavailable.
ENGINES: dict[str, Probe] = {
    "ghostscript": Probe("10.03.1", ("gs", "--version")),  # CVE-2024-29510
    # A child process, so a broken libqpdf fails the probe instead of the runner.
    "pikepdf": Probe("9", (sys.executable, "-c", "import pikepdf; print(pikepdf.__version__)")),
    "img2pdf": Probe("0.6", (sys.executable, "-c", "import img2pdf; print(img2pdf.__version__)")),
    "pillow": Probe("10.3", (sys.executable, "-c", "import PIL; print(PIL.__version__)")),
    "pypdfium2": Probe(
        "5", (sys.executable, "-c", "import pypdfium2; print(pypdfium2.PYPDFIUM_INFO)")
    ),
    # pdf.to-docx: layout text, images and find_tables() (1.23+) from untrusted PDFs.
    "pymupdf": Probe("1.24", (sys.executable, "-c", "import pymupdf; print(pymupdf.VersionBind)")),
    # pdf.to-docx writes with it (its own template); it reads no input document.
    "python-docx": Probe("1.1", (sys.executable, "-c", "import docx; print(docx.__version__)")),
    # 4.0+: well past the rl_safe_eval fix (CVE-2023-33733, 3.6.13). Overlays only, no markup.
    "reportlab": Probe("4.0", (sys.executable, "-c", "import reportlab; print(reportlab.Version)")),
    # pdf.sign (task 31): in-process, no timestamp or OCSP fetching. cert.create: key generation.
    "pyhanko": Probe(
        "0.37",
        (sys.executable, "-c", "from pyhanko.version import __version__; print(__version__)"),
    ),
    "cryptography": Probe(
        "44", (sys.executable, "-c", "import cryptography; print(cryptography.__version__)")
    ),
    # 24.8+: the profile's DisableActiveContent (ADR-0007 Appendix A). --version needs no profile.
    "libreoffice": Probe("24.8", ("soffice", "--version")),
    # ocr group (Task 38). OCRmyPDF runs Ghostscript for PDF/A: such tools declare it too.
    "tesseract": Probe("5", ("tesseract", "--version")),
    "ocrmypdf": Probe("17", (sys.executable, "-m", "ocrmypdf", "--version")),
    "unpaper": Probe("7", ("unpaper", "--version")),
    "opencv": Probe("4.10", (sys.executable, "-c", "import cv2; print(cv2.__version__)")),
    # ebook group (Task 49). Calibre's upstream build, as pinned in the image.
    "calibre": Probe("9", ("ebook-convert", "--version")),
    # 3.11, not just --sandbox (2.15+): Debian's 3.1.11 is built without embedded data files and
    # cannot write EPUB or DOCX under --sandbox; the version tuple (3, 1, 11) fails here.
    "pandoc": Probe("3.11", ("pandoc", "--version")),
    "djvulibre": Probe("3.5.28", ("sh", "-c", "ddjvu --help 2>&1")),  # banner goes to stderr
    # CBR (RAR4/RAR5) through libarchive-c and the system libarchive13 (filex/tools/_archive.py).
    # Debian 13's 3.7.4 carries the RAR fixes as patches; 3.7.4 itself fixed CVE-2024-26256.
    "libarchive": Probe(
        "3.7.4",
        (
            sys.executable,
            "-c",
            (
                "import libarchive.ffi as f; n = f.version_number(); "
                "print(f'{n // 1000000}.{n // 1000 % 1000}.{n % 1000}')"
            ),
        ),
    ),
}


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def probe(p: Probe) -> str | None:
    """The engine's version if it is installed and at least ``min_version``, else None."""
    try:
        done = subprocess.run(p.cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\d+(\.\d+)+", done.stdout)
    if not m or version_tuple(m.group(0)) < version_tuple(p.min_version):
        return None
    return m.group(0)


def container_memory_mb(
    paths=("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
) -> int | None:
    for p in paths:
        try:
            v = Path(p).read_text().strip()
        except OSError:
            continue
        if v.isdigit() and int(v) < 1 << 50:
            return int(v) // sandbox.MiB
    return None


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Runner:
    def __init__(
        self,
        r: redis.Redis,
        cfg: Config,
        tools: dict[str, Tool] | None = None,
        probes: dict[str, Probe] = ENGINES,
    ) -> None:
        if cfg.group not in GROUPS:
            raise ValueError(f"unknown group {cfg.group!r}")
        self.r, self.cfg = r, cfg
        self.all_tools = tools if tools is not None else discover()
        self.engines: dict[str, str] = {}
        self.tools: dict[str, Tool] = {}
        found: dict[str, str | None] = {}
        for t in self.all_tools.values():
            if t.group != cfg.group:
                continue
            for e in t.engines:
                if e not in found:
                    found[e] = probe(probes[e]) if e in probes else None
            if all(found[e] for e in t.engines):
                self.tools[t.id] = t
                self.engines.update({e: found[e] for e in t.engines})  # type: ignore[misc]
            else:
                missing = sorted(e for e in t.engines if not found[e])
                log.warning(
                    "tool unavailable: engine probe failed for %s",
                    ", ".join(missing),
                    extra={"tool": t.id},
                )
        if sandbox.LINUX and not sandbox.seccomp_available():
            # Every step would fail with SANDBOX_FAILED: advertise nothing (review L-4).
            log.error("libseccomp is missing: this worker offers no tools")
            self.tools = {}
        abi = sandbox.landlock_abi()
        try:
            # The ABI every step child enforces (0: none) and what the heartbeat reports.
            self.landlock_abi, self.landlock = sandbox.landlock_policy(cfg.landlock, abi)
        except OSError:
            log.error("FILEX_LANDLOCK=required but Landlock is unavailable: no tools offered")
            self.landlock_abi, self.landlock, self.tools = 0, "unavailable", {}
        log.info("step isolation: landlock %s (kernel ABI %d)", self.landlock, abi)
        self.id = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
        self.stream = queue_key(cfg.group)
        self.heavy = cfg.group != "core" and cfg.heavy_slots > 0
        self.memory_cap_mb = container_memory_mb()
        self.stop = threading.Event()
        self._lock = threading.Lock()  # guards _running against the sweep
        self._running: set[int] = set()  # pids (= session ids) of live step children
        self._next_claim = 0.0
        self._claim_cursor = "0-0"

    # --- serving

    def serve(self) -> None:
        self.ensure_group()
        loops = [
            threading.Thread(target=self._loop, daemon=True) for _ in range(self.cfg.concurrency)
        ]
        for t in loops:
            t.start()
        log.info(
            "worker serving: %s",
            ", ".join(sorted(self.tools)),
            extra={"worker": self.id, "group": self.cfg.group},
        )
        next_beat = next_sweep = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            try:
                if now >= next_beat:
                    self.heartbeat()
                    next_beat = now + 10
            except redis.RedisError:
                log.warning("heartbeat failed", exc_info=True)
            if now >= next_sweep:
                self.sweep()
                next_sweep = now + 5
            self.stop.wait(1)
        for t in loops:
            t.join()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.run_once(block_ms=2000)
            except redis.ConnectionError:
                log.warning("redis unavailable, retrying")
                self.stop.wait(1)
            except Exception:
                log.exception("unexpected runner error")
                self.stop.wait(1)

    def run_once(self, block_ms: int = 100) -> bool:
        """Take one entry (a stale one first, else a new one) and handle it."""
        entry = self._next_entry(block_ms)
        if entry is None:
            return False
        self.handle(*entry)
        return True

    def ensure_group(self) -> None:
        try:
            self.r.xgroup_create(self.stream, GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def heartbeat(self) -> None:
        beat = {
            "tools": sorted(self.tools),
            "engines": self.engines,
            "isolation": {"seccomp": sandbox.seccomp_available(), "landlock": self.landlock},
        }
        self.r.set(f"worker:{self.cfg.group}:{self.id}", json.dumps(beat), ex=30)

    def _next_entry(
        self, block_ms: int, retry: bool = True
    ) -> tuple[str, dict[str, str], bool] | None:
        """(entry id, fields, whether it was reclaimed from a dead worker) or None."""
        try:
            if time.monotonic() >= self._next_claim:
                # Redis 7 scans at most COUNT*10 PEL entries per call: keep the cursor, or a
                # dead entry behind 10+ live ones is never reached (review R1).
                cursor, claimed = self.r.xautoclaim(
                    self.stream, GROUP, self.id, self.cfg.claim_idle_ms, self._claim_cursor, count=1
                )[:2]
                self._claim_cursor = cursor
                if claimed:
                    return (*claimed[0], True)
                if cursor == "0-0":  # scanned the whole PEL: look again in 5 s
                    self._next_claim = time.monotonic() + 5
            res = self.r.xreadgroup(GROUP, self.id, {self.stream: ">"}, count=1, block=block_ms)
        except redis.ResponseError:  # NOGROUP: first start, or Redis restarted without data
            if not retry:
                raise
            self.ensure_group()
            self._next_claim, self._claim_cursor = 0.0, "0-0"
            return self._next_entry(block_ms, retry=False)
        return (*res[0][1][0], False) if res and res[0][1] else None

    # --- one entry

    def handle(self, entry_id: str, fields: dict[str, str], claimed: bool = False) -> None:
        job_id, step = fields.get("job"), fields.get("step", "")
        if not valid_job_id(job_id) or not step.isdigit() or int(step) > 64:
            log.warning("dropping malformed stream entry", extra={"entry": entry_id})
            self._ack(entry_id)
            return
        job = self.r.hgetall(job_key(job_id))
        run = StepRun(entry_id, job_id, int(step), _steps(job), JobPaths(self.cfg.data, job_id))
        status = job.get("status")
        if status is None or status == "CANCELLED":
            self._ack(entry_id)
            self._drop_job(run)
            return
        if status in TERMINAL or _step_status(job.get(f"step:{run.n}")) == "SUCCEEDED":
            self._ack(entry_id)  # finished, or a duplicate of a step that already handed off
            return
        if run.n >= len(run.steps):
            self._fail(run, "INTERNAL_ERROR", "The job record is damaged.")
            return
        started = time.monotonic()
        ids = {"job": job_id, "step": run.n, "tool": str(run.spec.get("tool", ""))[:100]}
        try:
            if claimed and self._deliveries(entry_id) > MAX_DELIVERIES:
                raise StepFailed("WORKER_LOST", "The worker stopped twice on this step. Try again.")
            outputs = self._run_step(run, job)
        except Cancelled:
            log.info("step cancelled", extra=ids)
            self._ack(entry_id)
            self._drop_job(run)
        except StepFailed as e:
            log.info("step failed", extra=ids | {"code": e.code, "duration_s": _since(started)})
            self._fail(run, e.code, e.message)
        except OSError:  # preparing dirs or links failed: fail now, not via redelivery
            log.exception("step hit a file system error", extra=ids | {"code": "INTERNAL_ERROR"})
            self._fail(run, "INTERNAL_ERROR", "The worker hit a disk error.")
        else:
            size = sum(o["size"] for o in outputs)
            done = {"files": len(outputs), "bytes": size, "duration_s": _since(started)}
            log.info("step done", extra=ids | done)

    def _run_step(self, run: StepRun, job: dict) -> list[dict]:
        tool = self.tools.get(run.spec.get("tool"))
        if tool is None:
            raise StepFailed("TOOL_UNAVAILABLE", "This tool is not available on this worker.")
        inputs = self._inputs(job, run, tool)
        aux = self._aux(job, run.paths, tool)
        slot = None
        try:
            if self.heavy:
                slot = self._take_slot(run)
            secret: dict = {}
            if run.spec.get("secret"):
                raw = self.r.getdel(sec_key(run.job_id, run.n))
                if raw is None:
                    raise StepFailed("SECRET_UNAVAILABLE", "The password is gone. Submit again.")
                secret = json.loads(raw)
            outputs, warnings = self._spawn(run, tool, inputs, aux, secret, slot)
        finally:
            if slot:
                self._release_slot(*slot)
            for slot_name, path in aux.items():
                if tool.files.aux[slot_name].sensitive:
                    path.unlink(missing_ok=True)
            shutil.rmtree(run.paths.scratch(run.n), ignore_errors=True)
            self.sweep()
        self._finish(run, outputs, warnings)
        return outputs

    def _inputs(self, job: dict, run: StepRun, tool: Tool) -> list[tuple[Path, str, str]]:
        """(path, format, display name) per input. Intermediate files carry the name the
        previous step gave them, else that of the job input at the same index."""
        paths, n = run.paths, run.n
        try:
            given = json.loads(job.get("inputs", "[]"))
            names = [_display_name(i.get("name")) for i in given]
            if n == 0:
                files = [
                    (paths.input(i, str(f["format"])), str(f["format"]))
                    for i, f in enumerate(given)
                ]
            else:
                prev = json.loads(job[f"step:{n - 1}"])["outputs"]
                files, kept = [], []
                for i, o in enumerate(prev):
                    if o["role"] == "RESULT":
                        files.append((paths.step_output(n - 1, i, o["format"]), o["format"]))
                        kept.append(_display_name(o.get("name")))
                names = [k or (names[j] if j < len(names) else "") for j, k in enumerate(kept)]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise StepFailed("INTERNAL_ERROR", "The job record is damaged.") from None
        if not tool.files.min <= len(files) <= tool.files.max or any(
            f not in tool.inputs for _, f in files
        ):
            raise StepFailed("INVALID_INPUT", "These files do not fit this tool.")
        if not all(p.is_file() for p, _ in files):
            raise StepFailed("INPUT_MISSING", "An input file is missing.")
        return [(p, f, names[i] if i < len(names) else "") for i, (p, f) in enumerate(files)]

    def _aux(self, job: dict, paths: JobPaths, tool: Tool) -> dict[str, Path]:
        try:
            given = json.loads(job.get("aux", "{}"))
        except ValueError:
            given = None
        given = given if isinstance(given, dict) else {}
        aux = {}
        for slot, decl in tool.files.aux.items():
            fmt = given.get(slot)
            path = paths.aux(slot, fmt) if fmt in decl.formats else None
            if path is not None and path.is_file():
                aux[slot] = path
            elif decl.required or fmt is not None:
                if decl.sensitive:  # deleted after an earlier delivery of this step
                    raise StepFailed(
                        "SECRET_UNAVAILABLE", "The uploaded key is gone. Submit again."
                    )
                raise StepFailed("INPUT_MISSING", "An auxiliary file is missing.")
        return aux

    # --- the child

    def _spawn(
        self, run: StepRun, tool: Tool, inputs, aux, secret, slot
    ) -> tuple[list[dict], list[dict]]:
        scratch = run.paths.scratch(run.n)
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, mode=0o700)
        names = []
        for i, (src, fmt, _) in enumerate(inputs):  # hard links: scratch holds the inputs only
            _link(src, scratch / f"{i}{ext(fmt)}")
            names.append(f"{i}{ext(fmt)}")
        aux_names = {}
        for slot_name, src in aux.items():
            _link(src, scratch / f"{slot_name}{src.suffix}")
            aux_names[slot_name] = f"{slot_name}{src.suffix}"
        step_dir = run.paths.step_dir(run.n)
        shutil.rmtree(step_dir, ignore_errors=True)  # at-least-once: a rerun starts clean
        out_dirs = [step_dir / f"w{k}" for k in range(len(inputs) if tool.files.each else 1)]
        for d in out_dirs:
            d.mkdir(parents=True)

        memory = tool.limits.memory_mb
        if self.memory_cap_mb:
            memory = min(memory, self.memory_cap_mb)
        request = {
            "module": tool.module,
            "tool": tool.id,
            "options": run.spec.get("options") or {},
            "secrets": secret,
            "inputs": names,
            "input_names": [name for _, _, name in inputs],
            "aux": aux_names,
            "out_dirs": [str(d) for d in out_dirs],
            "target": run.spec.get("target"),
            "landlock": self.landlock_abi,
            "max_pages": self.cfg.max_pages,
            "max_pixels": self.cfg.max_pixels,
            "limits": {
                "memory_mb": memory + STEP_OVERHEAD_MB,
                "cpu_s": tool.limits.timeout_s + 1,
                "fsize_mb": self.cfg.fsize_mb,
                "nofile": 256,
                "data": tool.limits.rlimit_data,
            },
        }
        n_steps = len(run.steps)
        self._write(
            run.job_id,
            {
                "status": "RUNNING",
                f"step:{run.n}": _state("RUNNING", 0),
                "progress": run.n / n_steps,
            },
            once=f"step:{run.n}",
        )
        # POSIX: stdin and stdout are one AF_UNIX socket, which no other process can open
        # through /proc/<pid>/fd (security review M-3). Windows (dev only): pipes.
        mine, theirs = socket.socketpair() if _POSIX else (None, None)
        std = theirs if theirs is not None else subprocess.PIPE
        proc: subprocess.Popen | None = None
        out = None
        readers: list[threading.Thread] = []
        try:
            with self._lock:  # registered before the sweep can see it
                proc = subprocess.Popen(
                    [sys.executable, "-P", "-s", "-m", "filex.worker.step"],
                    stdin=std,
                    stdout=std,
                    stderr=subprocess.PIPE,
                    cwd=scratch,
                    env=_child_env(scratch),
                    start_new_session=_POSIX,
                    creationflags=0 if _POSIX else subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                self._running.add(proc.pid)
            if theirs is not None:
                theirs.close()  # the child holds it now; EOF once the child is gone
            out = mine.makefile("rb") if mine is not None else proc.stdout
            events: queue.Queue = queue.Queue(maxsize=1000)
            stderr_tail = bytearray()
            readers = [
                threading.Thread(target=_read_events, args=(out, events), daemon=True),
                threading.Thread(
                    target=_tail, args=(proc.stderr, stderr_tail, STDERR_BYTES), daemon=True
                ),
            ]
            for t in readers:
                t.start()
            if not _await_ready(proc, events, readers[0]):
                raise StepFailed("SANDBOX_FAILED", "The step sandbox could not start.")
            try:
                _send(proc, mine, json.dumps(request).encode())
            except OSError:
                pass  # the child died early; its exit status says why
            del request, secret
            done, error, warnings = self._supervise(run, tool, proc, events, readers[0], slot)
        finally:
            if proc is not None:
                _kill(proc)
                with self._lock:
                    self._running.discard(proc.pid)
            for t in readers:
                if t.is_alive():
                    t.join(2)
            for f in (out, mine, theirs):
                if f is not None:
                    f.close()
        if error:
            if stderr_tail:  # only at DEBUG, and at most its last 2 KB (ADR-0008 "Logs")
                log.debug(
                    "step stderr",
                    extra={
                        "job": run.job_id,
                        "step": run.n,
                        "stderr": stderr_tail[-STDERR_BYTES:].decode("utf-8", "replace"),
                    },
                )
            raise StepFailed(*error)
        self.sweep()  # nothing of the step may still run while its outputs are checked
        return self._collect(run, tool, done, out_dirs), warnings

    def _supervise(self, run: StepRun, tool: Tool, proc, events, reader, slot):
        """(done, error, warnings) once the child has exited, timed out or must stop."""
        timeout = tool.limits.timeout_s
        deadline = time.monotonic() + timeout
        next_cancel = next_touch = next_progress = 0.0
        warnings: list[dict] = []
        items: list = []  # "output" events, at most MAX_OUTPUTS + 1
        done = error = stop = None
        undeclared_logged = False
        last_progress: tuple[float, str] | None = None
        n_steps = len(run.steps)
        while True:
            try:
                event = events.get(timeout=0.1)
            except queue.Empty:
                event = None
            if isinstance(event, dict):
                p = event.get("progress")
                if isinstance(p, (int, float)) and math.isfinite(p):
                    last_progress = (
                        min(max(float(p), 0.0), 1.0),
                        str(event.get("message", ""))[:200],
                    )
                elif isinstance(event.get("warn"), list) and len(event["warn"]) == 2:
                    code, message = map(str, event["warn"])
                    if code not in tool.warnings:
                        if not undeclared_logged:
                            undeclared_logged = True
                            log.warning(
                                "tool used an undeclared warning code",
                                extra={"job": run.job_id, "step": run.n, "tool": tool.id},
                            )
                    elif len(warnings) < MAX_WARNINGS:  # a flood costs no memory
                        warnings.append(
                            {"code": code, "message": message[:MAX_WARNING_CHARS], "step": run.n}
                        )
                elif "output" in event:
                    if len(items) <= MAX_OUTPUTS:  # one past the cap is enough to fail it
                        items.append(event["output"])
                elif type(event.get("done")) is int:
                    done = (event["done"], items)
                elif isinstance(event.get("error"), dict):
                    code = str(event["error"].get("code", ""))
                    message = str(event["error"].get("message", ""))[:300]
                    # Common codes plus the tool's declared ones; a step can never claim a
                    # runner code such as WORKER_LOST or SECRET_UNAVAILABLE.
                    error = (
                        (code, message)
                        if code in COMMON_ERRORS or code in tool.errors
                        else ("ENGINE_FAILED", "The tool failed.")
                    )
            if event is None and proc.poll() is not None:
                reader.join(1)  # the child is gone: let the reader pass on its last lines
                if events.empty():
                    break
            now = time.monotonic()
            if now > deadline:
                stop = ("ENGINE_TIMEOUT", f"The tool took longer than its {timeout} s limit.")
                break
            if now >= next_cancel:
                next_cancel = now + CANCEL_CHECK_S
                if self.r.hget(job_key(run.job_id), "status") in (None, "CANCELLED"):
                    raise Cancelled
            if now >= next_touch:
                next_touch = now + TOUCH_S
                self._touch(run.entry_id)
                if slot:
                    self._renew_slot(*slot)
                if self._disk_low():  # review M-5: a step must not fill /data for everyone
                    stop = ("OUTPUT_TOO_LARGE", "The data disk is nearly full. Try again later.")
                    break
            if last_progress and now >= next_progress:
                next_progress = now + 0.5
                p, message = last_progress
                last_progress = None
                self._write(
                    run.job_id,
                    {
                        f"step:{run.n}": _state("RUNNING", p, message),
                        "progress": (run.n + p) / n_steps,
                    },
                    once=f"step:{run.n}",
                )
        if stop:  # the caller kills the child
            return None, stop, warnings
        rc = proc.wait()
        if rc == _killed_by("SIGXCPU"):
            error = ("ENGINE_TIMEOUT", f"The tool took longer than its {timeout} s limit.")
        elif rc == _killed_by("SIGXFSZ"):
            error = ("OUTPUT_TOO_LARGE", "The output grew past the size limit.")
        elif error is None and (rc != 0 or done is None):
            # SIGKILL from outside the runner is almost always the kernel's OOM killer.
            if rc == _killed_by("SIGKILL"):
                error = ("ENGINE_MEMORY", "The tool ran out of memory.")
            else:
                error = ("ENGINE_FAILED", "The tool failed on this file.")
        return done, error, warnings

    def _disk_low(self) -> bool:
        free = shutil.disk_usage(self.cfg.data).free
        return self.cfg.min_free_mb > 0 and free < self.cfg.min_free_mb * sandbox.MiB

    def _collect(
        self, run: StepRun, tool: Tool, done: tuple[int, list], out_dirs: list[Path]
    ) -> list[dict]:
        """The runner, not the child, checks every output (ADR-0007 item 8) and moves it to
        ``steps/<n>/<i>.<ext>``. Names come from the child, so they are checked too. Checks run
        on an fd opened without following links and without blocking (a FIFO cannot hang us);
        after the move, the moved file must be the one checked. ``done`` is the child's output
        count and the "output" events it sent; more than MAX_OUTPUTS fails the step."""
        bad = StepFailed("INVALID_OUTPUT", "The tool produced an output that is not a plain file.")
        count, items = done
        if count > MAX_OUTPUTS or len(items) > MAX_OUTPUTS:
            raise StepFailed(
                "OUTPUT_TOO_LARGE",
                f"The tool made more than {MAX_OUTPUTS} files, which is the limit.",
            )
        if count != len(items):
            raise bad
        target = run.spec.get("target")
        results = {target} if tool.converts else set(tool.outputs)
        outputs: list[dict] = []
        total = 0
        for item in items:
            if not (isinstance(item, list) and len(item) == 4):
                raise bad
            k, name, role, shown = item
            if (
                not isinstance(k, int)
                or not 0 <= k < len(out_dirs)
                or role not in ("RESULT", "REPORT")
                or not isinstance(name, str)
                or name in ("", ".", "..")
                or any(c in name for c in "/\\\0")
                or len(name) > 255
                or not isinstance(shown, str)
            ):
                raise bad
            path = out_dirs[k] / name
            try:
                fh = os.fdopen(os.open(path, _OPEN_OUTPUT), "rb")
            except OSError:
                raise bad from None
            with fh:
                st = os.fstat(fh.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise bad
                total += st.st_size
                if total > self.cfg.max_output_bytes:
                    raise StepFailed("OUTPUT_TOO_LARGE", "The outputs grew past the size limit.")
                fmt = sniff(fh, name)
            if fmt is None or fmt not in (results if role == "RESULT" else FORMATS):
                raise StepFailed(
                    "INVALID_OUTPUT", "The tool produced a file of an unexpected type."
                )
            dest = run.paths.step_output(run.n, len(outputs), fmt)
            os.replace(path, dest)
            moved = os.lstat(dest)
            if (moved.st_dev, moved.st_ino) != (st.st_dev, st.st_ino):  # swapped meanwhile
                dest.unlink()
                raise bad
            out = {"format": fmt, "role": role, "size": st.st_size}
            if shown := output_name(shown, fmt):
                out["name"] = shown
            outputs.append(out)
        if not any(o["role"] == "RESULT" for o in outputs):
            raise StepFailed("INVALID_OUTPUT", "The tool produced no result.")
        for d in out_dirs:
            shutil.rmtree(d, ignore_errors=True)
        return outputs

    # --- job updates

    def _finish(self, run: StepRun, outputs: list[dict], warnings: list[dict]) -> None:
        job_id, n, paths = run.job_id, run.n, run.paths
        job_warnings = json.loads(self.r.hget(job_key(job_id), "warnings") or "[]")
        job_warnings = (job_warnings + warnings)[:MAX_WARNINGS]
        state = {**_state("SUCCEEDED", 1), "outputs": outputs}
        fields: dict[str, Any] = {f"step:{n}": state, "warnings": job_warnings}
        if n + 1 < len(run.steps):
            next_tool = self.all_tools.get(run.steps[n + 1].get("tool"))
            if next_tool is None:
                raise StepFailed("TOOL_UNAVAILABLE", "The next tool of this job is unknown.")
            fields["progress"] = (n + 1) / len(run.steps)
            next_entry = (queue_key(next_tool.group), {"job": job_id, "step": str(n + 1)})
            status = self._write(
                job_id, fields, ack=run.entry_id, xadd=next_entry, once=f"step:{n}"
            )
            if status is None or status == "CANCELLED":
                self._drop_job(run)
            return
        shutil.rmtree(paths.out_dir, ignore_errors=True)
        paths.out_dir.mkdir(parents=True)
        final = []
        for i, o in enumerate(outputs):
            _link(paths.step_output(n, i, o["format"]), paths.output(i, o["format"]))
            final.append({"index": i, **o})  # format, role, size and maybe name
        now = _now()
        fields |= {
            "status": "SUCCEEDED",
            "progress": "1",
            "outputs": final,
            "finishedAt": _iso(now),
            "expiresAt": _iso(now + timedelta(seconds=self.cfg.retention_s)),
        }
        status = self._write(job_id, fields, ack=run.entry_id, expire_s=self.cfg.retention_s)
        if status is None or status == "CANCELLED":  # cancelled while we linked: drop out/ too
            self._drop_job(run)
        elif status not in TERMINAL:  # we finished it
            self._drop_job(run, keep_out=True)

    def _fail(self, run: StepRun, code: str, message: str) -> None:
        now = _now()
        fields = {
            "status": "FAILED",
            f"step:{run.n}": _state("FAILED", 0),
            "error": {"code": code, "message": message, "step": run.n},
            "finishedAt": _iso(now),
            "expiresAt": _iso(now + timedelta(seconds=self.cfg.retention_s)),
        }
        status = self._write(run.job_id, fields, ack=run.entry_id, expire_s=self.cfg.retention_s)
        if status not in ("SUCCEEDED", "FAILED"):  # a duplicate never wipes a finished job
            self._drop_job(run)

    def _drop_job(self, run: StepRun, keep_out: bool = False) -> None:
        """Remove what a terminal, cancelled or deleted job no longer needs: secrets of this and
        later steps, inputs, intermediates and (unless kept) outputs (architecture section 3).
        The runner owns this because only it knows the step's child is gone."""
        for k, spec in enumerate(run.steps):
            if k >= run.n and spec.get("secret"):
                self.r.getdel(sec_key(run.job_id, k))
        p = run.paths
        dirs = [p.in_dir, p.steps_dir, p.scratch(run.n)] + ([] if keep_out else [p.out_dir])
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _write(self, job_id, fields, *, ack=None, xadd=None, expire_s=None, once=None):
        """Update a live job; never recreate a deleted job or touch a terminal one. With
        ``once`` (a ``step:<n>`` field), skip the update and hand-off when that step already
        SUCCEEDED, so a duplicate delivery (review C1) cannot reset it or run the rest of the
        job twice. The stream
        entry is acked and deleted in the same transaction either way. Returns the job's
        status as seen inside the transaction (None: deleted)."""
        key = job_key(job_id)
        fields = {k: v if isinstance(v, str) else json.dumps(v) for k, v in fields.items()}
        with self.r.pipeline() as p:
            while True:
                try:
                    p.watch(key)
                    status = p.hget(key, "status")
                    update = status is not None and status not in TERMINAL
                    if update and once:
                        update = _step_status(p.hget(key, once)) != "SUCCEEDED"
                    p.multi()
                    if update:
                        p.hset(key, mapping=fields)
                        if xadd:
                            p.xadd(*xadd)
                        if expire_s:
                            p.expire(key, expire_s)
                    if ack:
                        p.xack(self.stream, GROUP, ack)
                        p.xdel(self.stream, ack)
                    p.execute()
                    return status
                except redis.WatchError:
                    continue

    def _ack(self, entry_id: str) -> None:
        with self.r.pipeline() as p:
            p.xack(self.stream, GROUP, entry_id)
            p.xdel(self.stream, entry_id)
            p.execute()

    def _touch(self, entry_id: str) -> None:
        self.r.xclaim(self.stream, GROUP, self.id, 0, [entry_id], justid=True)

    def _deliveries(self, entry_id: str) -> int:
        pending = self.r.xpending_range(self.stream, GROUP, entry_id, entry_id, 1)
        return pending[0]["times_delivered"] if pending else 0

    # --- heavy-step slot (ADR-0002 amendment)

    def _take_slot(self, run: StepRun) -> tuple[str, str]:
        token = secrets.token_hex(8)
        shown = False
        next_touch = time.monotonic() + TOUCH_S
        while True:
            for i in range(self.cfg.heavy_slots):
                key = f"slot:heavy:{i}"
                if self.r.set(key, token, nx=True, px=SLOT_PX):
                    return key, token
            if not shown:
                shown = True
                fields = {f"step:{run.n}": _state("QUEUED", 0, WAITING)}
                self._write(run.job_id, fields, once=f"step:{run.n}")
            if self.r.hget(job_key(run.job_id), "status") in (None, "CANCELLED"):
                raise Cancelled
            if time.monotonic() >= next_touch:
                next_touch = time.monotonic() + TOUCH_S
                self._touch(run.entry_id)
            time.sleep(0.5)

    def _renew_slot(self, key: str, token: str) -> None:
        self._if_holder(key, token, lambda p: p.pexpire(key, SLOT_PX))

    def _release_slot(self, key: str, token: str) -> None:
        self._if_holder(key, token, lambda p: p.delete(key))

    def _if_holder(self, key: str, token: str, action) -> None:
        with self.r.pipeline() as p:
            try:
                p.watch(key)
                if p.get(key) != token:
                    return
                p.multi()
                action(p)
                p.execute()
            except redis.WatchError:
                pass  # changed hands meanwhile: not ours any more

    # --- escaped-process sweep (ADR-0007 item 2)

    def sweep(self) -> None:
        """Kill descendants whose session is not a running step's (setsid, double fork), and
        reap zombie children. Orphans re-parent to the runner (subreaper, or PID 1 in the
        image), so they stay descendants."""
        if not sandbox.LINUX:
            return
        me = os.getpid()
        with self._lock:
            procs = {pid: (ppid, sid, state) for pid, ppid, sid, state in sandbox.processes()}
            for pid, (ppid, sid, state) in procs.items():
                if pid == me or sid in self._running or not _descends(pid, me, procs):
                    continue
                if state != "Z":
                    log.warning("killing escaped process", extra={"pid": pid, "sid": sid})
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                if ppid == me:
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass


# --- helpers


def _state(status: str, progress: float, message: str = "") -> dict[str, Any]:
    return {"status": status, "progress": progress, "message": message}


def _steps(job: dict) -> list[dict]:
    try:
        steps = json.loads(job.get("steps", "[]"))
    except ValueError:
        return []
    ok = isinstance(steps, list) and all(isinstance(s, dict) for s in steps)
    return steps if ok else []


def _step_status(raw: str | None) -> str | None:
    try:
        return json.loads(raw).get("status") if raw else None
    except (ValueError, AttributeError):
        return None


def _display_name(name: object) -> str:
    """The API already cleans names; the runner does it again, as Redis data is not trusted."""
    s = unicodedata.normalize("NFC", name if isinstance(name, str) else "")
    return "".join(c for c in s if unicodedata.category(c)[0] != "C")[:200]


def output_name(raw: str, fmt: str) -> str:
    """A tool's display name for an output: cleaned like an input name, without path parts, and
    with the sniffed format's extension in place of whatever it had, and without the characters
    Windows forbids in names (review L-6); "" when nothing is left."""
    name = re.split(r"[/\\]", _display_name(raw))[-1]
    name = re.sub(r'[":*?<>|]', "", name).strip()
    stem, dot, _ = name.rpartition(".")
    stem = (stem if dot else name).strip()
    if stem in ("", ".", ".."):
        return ""
    suffix = ext(fmt)
    return stem[: 200 - len(suffix)] + suffix


def _since(started: float) -> float:
    return round(time.monotonic() - started, 3)


def _killed_by(name: str) -> int:
    return -getattr(signal, name, 1000)  # never matches where the signal does not exist


def _child_env(scratch: Path) -> dict[str, str]:
    """An allowlist (ADR-0007 item 7). PYTHONPATH passes through for tests and dev runs."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "OMP_THREAD_LIMIT": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    if os.name == "nt":  # dev machines only: Python cannot start without SYSTEMROOT
        env |= {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": str(scratch),
            "TMP": str(scratch),
        }
    return env


def _await_ready(proc: subprocess.Popen, events: queue.Queue, reader: threading.Thread) -> bool:
    """True once the child said ``{"ready": true}`` (it is non-dumpable now), False if it said
    anything else first, exited, or took longer than READY_S."""
    deadline = time.monotonic() + READY_S
    while time.monotonic() < deadline:
        try:
            return events.get(timeout=0.1) == {"ready": True}
        except queue.Empty:
            if proc.poll() is not None and not reader.is_alive() and events.empty():
                return False
    return False


def _send(proc: subprocess.Popen, sock: socket.socket | None, data: bytes) -> None:
    if sock is not None:
        sock.sendall(data)
        sock.shutdown(socket.SHUT_WR)
    else:
        proc.stdin.write(data)
        proc.stdin.close()


def _read_events(stream, events: queue.Queue) -> None:
    """JSON lines from the child, each at most 64 KB; longer lines are dropped whole."""
    skipping = False
    while line := stream.readline(65536):
        if not line.endswith(b"\n"):
            skipping = True
            continue
        if skipping:
            skipping = False
            continue
        try:
            events.put(json.loads(line))
        except ValueError:
            continue


def _tail(stream, buf: bytearray, keep: int = 2048) -> None:
    while chunk := stream.read(4096):
        buf.extend(chunk)
        del buf[:-keep]


def _kill(proc: subprocess.Popen) -> None:
    """Kill the step's whole process group (the child is its session and group leader)."""
    if _POSIX:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif proc.poll() is None:
        proc.kill()
    proc.wait()


def _descends(pid: int, ancestor: int, procs: dict[int, tuple[int, int, str]]) -> bool:
    for _ in range(64):
        parent = procs.get(pid, (0,))[0]
        if parent == ancestor:
            return True
        if parent <= 1:
            return False
        pid = parent
    return False
