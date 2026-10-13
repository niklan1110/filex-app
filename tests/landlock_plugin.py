"""pytest plugin: run the whole session under the step's Landlock allowlist (ADR-0007 item 11).

    python -m pytest -p tests.landlock_plugin tests/tools/test_office_convert.py

The tool tests call ``run()`` in-process, so the step child's ruleset never applies to them. This
plugin applies the same system read list (``sandbox.SYSTEM_READ`` plus the import path) to the
pytest process itself, with the checkout read-only and only ``/tmp`` (pytest's temp dirs, HOME
in the image tests) writable. An engine that needs a system path the list forgot fails here.
Linux with Landlock only; anywhere else the plugin refuses to run, so a skip cannot pass silently.
"""

import os
import sys

from filex.worker import sandbox


def pytest_configure(config):
    abi = sandbox.landlock_abi()
    if abi < sandbox.LANDLOCK_MIN_ABI:
        raise RuntimeError(f"landlock_plugin needs Landlock ABI >= 2, kernel has {abi}")
    tmp = os.environ.get("TMPDIR", "/tmp")
    rules = sandbox.landlock_ruleset(abi, write=[tmp], read=[os.getcwd(), *sys.path])
    sandbox.install_landlock(rules)
    print(f"landlock_plugin: abi{abi}, writable {tmp}", file=sys.stderr)
