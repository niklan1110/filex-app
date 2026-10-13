"""JSON log lines for the API and the worker runner (ADR-0008 "Logs", review M7 fix 2, task 65).

One JSON object per line: ``ts``, ``level``, ``logger``, ``msg`` and the structured fields a call
passes in ``extra`` (only the keys in ``FIELDS``), e.g.
``log.info("step done", extra={"job": job_id, "step": n, "tool": tool.id, "duration_s": 1.2})``.

What may go in: job ids, step numbers, tool ids, principals (``keel:<uuid>``, ``token:<name>``),
groups, counts, sizes, durations and error codes. What never goes in, neither in the message nor
in a field: file names (upload and output display names are personal data), option values,
secrets, tokens, link signatures (uvicorn's access log is stripped of query strings in
``filex.api.main``) and email addresses. Exceptions are logged as type and stack only, because an
exception message can quote any of those. Engine stderr goes in only at DEBUG, as the ``stderr``
field, cut to its last 2 KB by the runner.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import UTC, datetime

FIELDS = (
    "job",
    "step",
    "tool",
    "principal",
    "group",
    "worker",
    "code",
    "files",
    "bytes",
    "duration_s",
    "entry",
    "pid",
    "sid",
    "stderr",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in FIELDS:
            value = record.__dict__.get(key)
            if value is not None:
                line[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            kind, _, tb = record.exc_info
            line["exc"] = kind.__name__ + "\n" + "".join(traceback.format_tb(tb))
        return json.dumps(line, ensure_ascii=False, default=str)


def configure(level: str | int = "INFO") -> None:
    """JSON lines on stderr for the whole process, uvicorn's loggers included."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []  # keeps its filters (DropQueryString) and propagates to root
        logger.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines carry request URLs
