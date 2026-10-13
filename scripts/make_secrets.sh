#!/bin/sh
# POSIX twin of scripts/make_secrets.py, for hosts without Python.
# Creates the git-ignored Docker secret files compose needs (ADR-0007 item 3, plan task 54).
#
#   sh scripts/make_secrets.sh                # creates the missing files in ./secrets
#   sh scripts/make_secrets.sh --force        # rotates every password, then:
#   docker compose up -d --force-recreate
#   sh scripts/make_secrets.sh --dir <folder> # another folder (tests)
#
# Same file names and permissions as the Python script: folder 0700, files 0644 holding one
# random password with no newline (64 hex characters from /dev/urandom, 256 bits).
set -eu

dir=$(cd "$(dirname "$0")/.." && pwd)/secrets
force=0
usage() { echo "usage: sh scripts/make_secrets.sh [--force] [--dir <folder>]" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case $1 in
    --force) force=1 ;;
    --dir) [ $# -ge 2 ] || usage; dir=$2; shift ;;
    *) usage ;;
  esac
  shift
done

umask 077
mkdir -p "$dir"
chmod 700 "$dir"
wrote=0
for name in redis_api_password redis_worker_password; do
  f="$dir/$name"
  if [ -e "$f" ] && [ "$force" -eq 0 ]; then
    continue
  fi
  od -An -tx1 -N32 /dev/urandom | tr -d ' \n' > "$f"
  chmod 644 "$f"
  echo "wrote $f"  # the path only, never the password
  wrote=1
done
if [ "$wrote" -eq 0 ]; then
  echo "all secret files already exist in $dir (use --force to rotate)"
fi
