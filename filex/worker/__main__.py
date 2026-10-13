"""``python -m filex.worker <group>``: the worker container's entry point."""

import os
import signal
import sys

import redis

from filex import logs
from filex.jobs import redis_credentials
from filex.worker import sandbox
from filex.worker.runner import Config, Runner


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        sys.exit("usage: python -m filex.worker <group>")
    logs.configure(os.environ.get("FILEX_LOG_LEVEL", "INFO"))
    sandbox.harden_runner()
    user, password = redis_credentials(os.environ)
    r = redis.Redis.from_url(
        os.environ.get("FILEX_REDIS_URL", "redis://redis:6379/0"),
        username=user,
        password=password,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,  # above XREADGROUP's 2 s block; a half-open socket must not hang us
        health_check_interval=30,
    )
    runner = Runner(r, Config.from_env(argv[0]))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: runner.stop.set())
    runner.serve()


if __name__ == "__main__":
    main(sys.argv[1:])
