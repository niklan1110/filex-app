"""Job ids, Redis keys and ``/data`` paths, shared by the API and the worker runner.

Every path is built from a job id that matched ``JOB_ID`` plus an integer index, never from a
path string read from Redis or a stream entry (ADR-0007 items 4-5, ADR-0008).

``job:<id>`` hash (strings; JSON where marked). The API creates it; workers update it:

=================  =====  ===================================================================
field              by     content
=================  =====  ===================================================================
status             both   QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
steps              api    JSON ``[{"tool", "options", "target", "secret": bool}]``; options
                          without secrets; ``secret`` true when ``sec:<id>:<n>`` was written
inputs             api    JSON ``[{"format", ...}]``: file n is ``in/<n>.<ext>``
aux                api    JSON ``{"<slot>": "<format>"}``: file ``in/aux/<slot>.<ext>``
progress           both   JSON number 0..1 over all steps
step:<n>           wkr    JSON ``{"status", "progress", "message", "outputs": [{"format",
                          "role", "size", "name"?}]}``; output i of step n is
                          ``steps/<n>/<i>.<ext>``; at most ``MAX_OUTPUTS`` (registry)
warnings           both   JSON ``[{"code", "message", "step"}]`` (at most 20): a planned
                          tool's warnings from the API at creation (ADR-0005), then the
                          workers' appended
outputs            wkr    JSON ``[{"index", "format", "size", "role", "name"?}]``:
                          ``out/<i>.<ext>``. ``name`` (optional): the display name the tool
                          gave the output, cleaned by the runner (NFC, no path parts, control
                          or bidi characters, at most 200 characters, the format's extension);
                          the API names the download after it, else after the input
error              wkr    JSON ``{"code", "message", "step"}``
finishedAt         wkr    ISO 8601 UTC
expiresAt          both   ISO 8601 UTC
=================  =====  ===================================================================

Stream entries on ``q:<group>`` carry only ``job`` and ``step`` (the index).

Cancel and delete: the API only marks a queued or running job ``CANCELLED`` (and may delete the
``sec:*`` keys). The worker runner owns the cleanup: when it sees a ``CANCELLED`` or deleted job,
in its stream entry or while its step waits or runs, it kills the step and removes ``in/``,
``steps/``, ``out/`` and the job's remaining ``sec:*`` keys. Finished jobs are deleted by the API.

Ownership (``own:<id>``, ``ownidx:<principal>``) is API-only: the worker's ACL user cannot write
it, and the runner never touches it. ``own:<id>`` lives for the longest a job can live (24 h
queued or running plus up to 24 h retention), so it never expires before the job hash; an
``own:`` key that outlives its job grants nothing, because the hash is what makes a job exist.
"""

from __future__ import annotations

import base64
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from filex.formats import FORMATS

JOB_ID = re.compile(r"j_[A-Za-z0-9_-]{22}")
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
GROUP = "workers"  # the consumer group on every q:<group> stream
MAX_RETENTION_MIN = 1440  # review M7 fix 1: outputs never outlive 24 h


def retention_minutes(raw: str) -> int:
    """``FILEX_RETENTION_MIN`` clamped to 1..1440, with a log line when it was out of range."""
    value = int(raw)
    clamped = max(1, min(MAX_RETENTION_MIN, value))
    if clamped != value:
        logging.getLogger("filex").warning(
            "FILEX_RETENTION_MIN=%d is outside 1..%d; using %d.", value, MAX_RETENTION_MIN, clamped
        )
    return clamped


def redis_credentials(env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The Redis ACL user and password for this process (ADR-0007 item 3).

    The password comes from the Docker secret file named by ``FILEX_REDIS_PASSWORD_FILE``;
    ``FILEX_REDIS_PASSWORD`` is only for local runs outside compose. An empty or missing secret
    file is an error at startup rather than a connection that fails on every command later.
    """
    user = env.get("FILEX_REDIS_USER") or None
    path = env.get("FILEX_REDIS_PASSWORD_FILE")
    if not path:
        return user, env.get("FILEX_REDIS_PASSWORD") or None
    password = Path(path).read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError(f"FILEX_REDIS_PASSWORD_FILE {path} is empty")
    return user, password


def new_job_id() -> str:
    """``"j_"`` + base64url of 16 random bytes, no padding (ADR-0007 item 5)."""
    return "j_" + base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()


def valid_job_id(job_id: object) -> bool:
    return isinstance(job_id, str) and JOB_ID.fullmatch(job_id) is not None


def job_key(job_id: str) -> str:
    return f"job:{job_id}"


def sec_key(job_id: str, step: int) -> str:
    return f"sec:{job_id}:{step}"


def own_key(job_id: str) -> str:
    """The owning principal of a job. Written and read only by the API (ADR-0009 Ownership)."""
    return f"own:{job_id}"


def ownidx_key(principal: str) -> str:
    """Sorted set of the principal's job ids, scored by creation time in ms (API only)."""
    return f"ownidx:{principal}"


def queue_key(group: str) -> str:
    return f"q:{group}"


def ext(fmt: str) -> str:
    return FORMATS[fmt].extensions[0]


@dataclass(frozen=True)
class JobPaths:
    root: Path  # the data volume, /data
    job_id: str

    def __post_init__(self) -> None:
        if not valid_job_id(self.job_id):
            raise ValueError("malformed job id")

    @property
    def dir(self) -> Path:
        return self.root / "jobs" / self.job_id

    @property
    def in_dir(self) -> Path:
        return self.dir / "in"

    @property
    def aux_dir(self) -> Path:
        return self.in_dir / "aux"

    @property
    def steps_dir(self) -> Path:
        return self.dir / "steps"

    @property
    def out_dir(self) -> Path:
        return self.dir / "out"

    def input(self, n: int, fmt: str) -> Path:
        return self.in_dir / f"{int(n)}{ext(fmt)}"

    def aux(self, slot: str, fmt: str) -> Path:
        return self.aux_dir / f"{slot}{ext(fmt)}"  # slot names are checked by the registry

    def step_dir(self, n: int) -> Path:
        return self.steps_dir / str(int(n))

    def step_output(self, n: int, i: int, fmt: str) -> Path:
        return self.step_dir(n) / f"{int(i)}{ext(fmt)}"

    def output(self, i: int, fmt: str) -> Path:
        return self.out_dir / f"{int(i)}{ext(fmt)}"

    def scratch(self, n: int) -> Path:
        return self.root / "tmp" / f"{self.job_id}.{int(n)}"
