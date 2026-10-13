"""Create the git-ignored Docker secret files compose needs (ADR-0007 item 3, plan task 54).

    python scripts/make_secrets.py           # creates the missing files in secrets/
    python scripts/make_secrets.py --force   # rotates every password, then:
    docker compose up -d --force-recreate   # every service: all workers use the worker password

Each file holds one random password with no newline. The directory is 0700 and the files 0644:
compose bind-mounts each file into its container at /run/secrets/<name>, where the non-root
container user must be able to read it, while other users on the host cannot enter the folder.
Redis reads the passwords only at start, so a rotation needs the recreate above.
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

NAMES = ("redis_api_password", "redis_worker_password")
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "secrets"


def make_secrets(folder: Path, force: bool = False) -> list[Path]:
    """Write the missing (or, with ``force``, all) secret files; returns the files written."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    written = []
    for name in NAMES:
        path = folder / name
        if path.exists() and not force:
            continue
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8", newline="")
        os.chmod(path, 0o644)
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="default: ./secrets")
    parser.add_argument("--force", action="store_true", help="replace existing files (rotate)")
    args = parser.parse_args()
    written = make_secrets(args.dir, args.force)
    for path in written:
        print(f"wrote {path}")  # the path only, never the password
    if not written:
        print(f"all secret files already exist in {args.dir} (use --force to rotate)")


if __name__ == "__main__":
    main()
