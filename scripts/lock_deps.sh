#!/bin/sh
# Refresh the hashed Python lockfiles the images install from (plan task 66):
#
#     sh scripts/lock_deps.sh
#
# Writes requirements/<image>.txt for api, core, office, ocr and ebook (the base dependencies
# plus that image's extra from pyproject.toml) and requirements/build.txt (setuptools, to build
# filex itself). Every version is pinned with the SHA-256 of every file PyPI has for it, so
# `pip install --require-hashes` in docker/Dockerfile refuses anything else, on amd64 and arm64.
# Every run upgrades every pin to the newest version pyproject.toml allows: review the diff,
# then rebuild and run scripts/ci/image-tests.sh. Needs Docker only (uv runs in a container).
set -eu

# uv 0.12.24 with Python 3.12 on Debian 13, same Python as the images.
UV_IMAGE=ghcr.io/astral-sh/uv:0.12.24-python3.12-trixie-slim@sha256:c45e18882c3b26a1631ac8b64537a2213e301b752edaa2471d32226090ccaafe
repo=$(cd "$(dirname "$0")/.." && (pwd -W 2>/dev/null || pwd))

MSYS_NO_PATHCONV=1 docker run --rm -v "$repo:/src" -w /src -e UV_NO_CACHE=1 "$UV_IMAGE" sh -euc '
  mkdir -p requirements
  compile="uv pip compile --quiet --upgrade --universal --python-version 3.12 --generate-hashes
    --no-header"
  for extra in api core office ocr ebook; do
    { echo "# Locked dependencies of the $extra image. Refresh: sh scripts/lock_deps.sh"
      $compile pyproject.toml --extra "$extra"; } > "requirements/$extra.txt"
  done
  { echo "# Build backend for filex itself. Refresh: sh scripts/lock_deps.sh"
    sed -n "s/^requires = \[\"\(.*\)\"\]$/\1/p" pyproject.toml | $compile -; } > requirements/build.txt
  for f in requirements/*.txt; do echo "$f: $(grep -c "^[a-z0-9]" "$f") packages"; done
'
