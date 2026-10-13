#!/bin/sh
# Run a worker group's tests inside its built image (plan task 11).
#
#     sh scripts/ci/image-tests.sh core filex-ci-worker-core:test
#     sh scripts/ci/image-tests.sh api filex-ci-api:test    # the import check only
#
# Adds a throwaway test layer on top of the image (pytest and the test deps from pyproject's
# extras; the engines stay exactly as built), then runs, with the checkout mounted read-only,
# no network, a read-only root, all capabilities dropped and the image's uid 10001
# (after scripts/ci/check_imports.py has checked the bare image's runtime dependencies):
#   - the group's tool tests (tests/tools/test_<tool>.py of every tool in the group),
#   - every tool example of the group under the per-step seccomp filter (task 53),
#   - the engine probe tests,
#   - tests/integration/test_lfi.py: the local-file-inclusion corpus through the group's tools
#     (task 64),
#   - core only: tests/integration/test_sandbox.py (seccomp, escaped-process sweep);
#     ebook only: tests/unit/test_archive_checks.py (RAR needs the image's libarchive).
# Redis is fakeredis: the container has no network. CI runs the real-Redis tests elsewhere.
set -eu

group=${1:?usage: image-tests.sh <core|office|ocr|ebook|api> <image>}
image=${2:?usage: image-tests.sh <group> <image>}
# FILEX_CI_TEST_IMAGE names the throwaway test layer (default filex-ci-test-<group>:local-ci).
test_image="${FILEX_CI_TEST_IMAGE:-filex-ci-test-$group:local-ci}"
repo=$(cd "$(dirname "$0")/../.." && (pwd -W 2>/dev/null || pwd))

# office tool tests read their outputs with pikepdf/PyMuPDF, which only the core extra brings.
# core adds pikepdf's PDF/A validator (test_pdf_to_pdfa checks real conversions with it).
more=
case "$group" in
  core) extras=api,dev; more="pikepdf[pdfa]"; tests="tests/integration/test_sandbox.py tests/integration/test_landlock.py" ;;
  office) extras=api,dev,core; tests= ;;
  ocr) extras=api,dev; tests= ;;
  ebook) extras=api,dev; tests=tests/unit/test_archive_checks.py ;;
  api) ;;  # no engines to test: the import check below is all
  *) echo "unknown group: $group" >&2; exit 2 ;;
esac

# First, on the BARE image (no test layer, whose dev extras could hide a missing runtime
# dependency): every third-party module the group's tools and the worker import must import.
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges -v "$repo/scripts/ci:/ci:ro" \
  "$image" python /ci/check_imports.py "$group"

# Licence notices: FileX's own, a copyright file for every Debian package, Pandoc's (ebook)
# and the ones served next to the web UI (api).
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges -e GROUP="$group" --entrypoint sh "$image" -c '
    set -eu; bad=0
    for f in /usr/share/doc/filex/LICENSE /usr/share/doc/filex/THIRD_PARTY_NOTICES.md; do
      [ -s "$f" ] || { echo "missing $f"; bad=1; }; done
    for p in $(dpkg-query -W -f "\${Package}\n"); do
      [ -f "/usr/share/doc/${p%%:*}/copyright" ] || { echo "no copyright file: $p"; bad=1; }; done
    case $GROUP in
      ebook) for f in COPYRIGHT COPYING.md; do
               [ -s "/usr/share/doc/pandoc/$f" ] || { echo "missing pandoc $f"; bad=1; }; done ;;
      api) for f in LICENSE THIRD_PARTY_NOTICES.md; do
             [ -s "/app/web/dist/$f" ] || { echo "missing web/dist/$f"; bad=1; }; done ;;
    esac
    [ $bad = 0 ] && echo "licence notices: ok"; exit $bad'
[ "$group" = api ] && exit 0

# The current context's own builder (docker driver) sees images loaded into the local engine; a
# docker-container builder (setup-buildx) would try to pull "$image" from a registry instead.
docker buildx build --builder "${FILEX_CI_BUILDER:-$(docker context show)}" --load --quiet \
  --build-arg BASE="$image" --build-arg EXTRAS="$extras" --build-arg MORE="$more" -t "$test_image" - <<'EOF' >/dev/null
ARG BASE
FROM ${BASE}
ARG EXTRAS
ARG MORE
USER root
RUN pip install --no-cache-dir "/app[${EXTRAS}]" ${MORE}
USER filex
EOF

# Test files of the tools in this group that exist (test_tools_generic fails on a missing one).
list_py='import sys
from pathlib import Path
from filex.registry import discover
ids = sorted(t.id for t in discover().values() if t.group == sys.argv[1])
paths = [Path("tests/tools") / f"test_{i.replace(".", "_").replace("-", "_")}.py" for i in ids]
print(" ".join(str(p) for p in paths if p.is_file()))'

# Two pytest runs: tests/tools and tests/integration each have a conftest imported as plain
# "conftest", so one session cannot collect both. PYTHONPYCACHEPREFIX keeps Python and pytest
# off any __pycache__ in the (read-only) checkout.
status=0
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only   --tmpfs /tmp:rw,exec,nosuid,nodev,size=1g,mode=1777   --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges --pids-limit 1024   -v "$repo:/src:ro" -w /src   -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/pycache   -e FILEX_TEST_GROUP="$group" -e LIST_PY="$list_py" -e TESTS="$tests"   "$test_image" sh -c '
    set -u; rc=0; pytest="python -m pytest -p no:cacheprovider -q -rs"
    unit=$(python -c "$LIST_PY" "$FILEX_TEST_GROUP")
    integ="tests/integration/test_examples.py tests/integration/test_lfi.py"
    for t in $TESTS; do case $t in tests/integration/*) integ="$integ $t";; *) unit="$unit $t";; esac; done
    echo "== $unit tests/unit/test_engine_probes.py"
    $pytest -p tests.landlock_plugin $unit tests/unit/test_engine_probes.py || rc=1
    echo "== $integ"
    $pytest $integ || rc=1
    exit $rc' || status=$?

docker image rm "$test_image" >/dev/null 2>&1 || true
exit "$status"
