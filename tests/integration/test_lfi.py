"""Local-file-inclusion test per engine (plan task 64, review H2 fix 4, tools review M-2).

Every hostile sample of ``lfi_corpus`` goes through every tool that accepts its format, and the
test passes only if no output (nor the error message) holds the other job's canaries or the
host's name, and, for samples whose references are never drawn, the pixels match a control run
whose references point at missing files. LibreOffice's binary doc / xls / ppt samples are made
in the test by ``office.convert`` from the ODF samples.

- In a worker image (``FILEX_TEST_GROUP`` set, Linux; ``scripts/ci/image-tests.sh`` runs it for
  each group): the group's tools run through the real runner and step child, so under the
  seccomp filter and rlimits, with the other job's files in the same data dir.
- Elsewhere (Windows dev machines): the tools whose engines are all pure Python run in-process
  through ``filex.worker.step.run``.

``LFI_COVERAGE`` maps each engine to the tests that cover it; ``test_every_engine_has_an_lfi_entry``
fails when a tool brings an engine without one. The matrix is in docs/reviews/lfi-coverage.md.

    FILEX_TEST_GROUP=core python -m pytest tests/integration/test_lfi.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lfi_corpus as corpus
import pytest
from conftest import LINUX, job

from filex.jobs import JobPaths, job_key, new_job_id, queue_key, sec_key
from filex.registry import StepError, discover

ROOT = Path(__file__).resolve().parents[2]
GROUP = os.environ.get("FILEX_TEST_GROUP", "")
IN_IMAGE = bool(GROUP) and LINUX
ALL = discover()

THIS = "tests/integration/test_lfi.py::test_engine_reads_no_references"
NA = "n/a: "
# Engine -> the tests that feed it documents with references, or "n/a: why".
LFI_COVERAGE: dict[str, tuple[str, ...]] = {
    "ghostscript": (
        THIS,
        "tests/tools/test_pdf_to_pdfa.py::test_ghostscript_reads_no_host_or_other_job_files",
        "tests/tools/test_pdf_compress.py::test_ghostscript_reads_no_host_or_other_job_files",
    ),
    "libreoffice": (
        THIS,
        "tests/tools/test_office_to_pdf.py::test_linked_files_and_images_are_not_pulled_in",
        "tests/tools/test_office_to_pdf.py::test_webservice_and_external_references_fetch_nothing",
        "tests/tools/test_office_convert.py::test_external_references_and_webservice_become_errors",
    ),
    "calibre": (
        THIS,
        "tests/tools/test_ebook_convert.py::test_html_with_local_file_references_leaks_nothing",
        "tests/tools/test_ebook_convert.py::test_epub_referencing_host_files_leaks_nothing_into_pdf",
    ),
    "pandoc": (
        THIS,
        "tests/tools/test_markup_convert.py::test_html_with_local_file_references_leaks_nothing",
        "tests/tools/test_markup_convert.py::test_markdown_with_local_file_references_leaks_nothing",
    ),
    "ocrmypdf": (THIS,),
    "tesseract": (THIS,),
    "unpaper": (THIS,),
    "pikepdf": (THIS,),
    "pypdfium2": (THIS,),
    "pymupdf": (THIS,),
    "python-docx": (
        NA + "pdf.to-docx writes the DOCX with it from its own template and reads no input "
        "document (the PDF input is covered under pymupdf)",
    ),
    "pyhanko": (THIS,),
    "reportlab": (THIS,),
    "pillow": (THIS,),
    "img2pdf": (THIS,),
    "djvulibre": (THIS,),
    "libarchive": (
        THIS,
        "tests/unit/test_archive_checks.py::test_symlink_and_device_entries_are_refused",
    ),
    "cryptography": (
        NA + "cert.create reads no document; the .p12 pdf.sign gets is a key container "
        "without file references (pdf.sign's PDF input is covered under pyhanko)",
    ),
}

# Converters: up to two targets per sample, the ones that show text or embed resources.
TARGET_ORDER = (
    "pdf",
    "docx",
    "epub",
    "txt",
    "csv",
    "ods",
    "odt",
    "odp",
    "xlsx",
    "html",
    "md",
    "fb2",
    "png",
    "cbz",
)
# Options that make a tool run its engines on the page (examples are tuned for speed instead).
OPTIONS = {
    "ocr.pdf": {"mode": "force", "output": "pdfa", "clean": True, "sidecar": True},
    "scan.clean": {"ocr": True},
    "pdf.compress": {"level": "HIGH"},
    "djvu.to-pdf": {"ocr": "never"},
}
# Refusing the file is safe too, but only where it is the expected answer.
REFUSED = {
    ("pdf.decrypt", "lfi_pdf"): {"NOT_ENCRYPTED"},
    ("comic.to-pdf", "cbz_symlink"): {"INVALID_INPUT"},
    ("ebook.convert", "fb2_entity"): {"INVALID_INPUT"},
    ("markup.convert", "fb2_entity"): {"INVALID_INPUT"},
    # LibreOffice follows INCLUDEPICTURE in RTF; the office tools refuse such RTF (filex/tools/
    # office/_rtf.py). Before that fix both tools leaked the other job's PNG.
    ("office.to-pdf", "rtf_include"): {"INVALID_INPUT"},
    ("office.convert", "rtf_include"): {"INVALID_INPUT"},
    # djvulibre cannot open a page whose INCL ids do not resolve inside the document and gives
    # up: nothing is read or written. The same hand-built page without INCL (planted_djvu)
    # converts, so the refusal comes from the INCL chunks, not from a malformed sample.
    ("djvu.to-pdf", "djvu_incl"): {"INVALID_INPUT"},
    # Pandoc's FB2 reader fails on <image l:href> that is not a "#id" of a <binary> in the book,
    # so it stops before fetching anything.
    ("markup.convert", "fb2_lfi"): {"ENGINE_FAILED"},
    # Calibre's DOCX input embedded linked_docx's external r:link image (the other job's PNG).
    # Every OOXML reader now refuses packages with relationships to local files first
    # (filex/tools/_ooxml.py); for Pandoc and LibreOffice that is defence in depth.
    **{
        (t, s): {"INVALID_INPUT"}
        for t in ("ebook.convert", "markup.convert", "office.to-pdf", "office.convert")
        for s in ("linked_docx", "docx_template_altchunk")
    },
    **{
        (t, s): {"INVALID_INPUT"}
        for t in ("office.to-pdf", "office.convert")
        for s in ("xlsx_external_link", "pptx_linked_media")
    },
}
# (tool, sample) cases that fail today: strict xfail, so a fix makes the entry go away.
KNOWN_BROKEN: dict[tuple[str, str], str] = {}
PURE_PYTHON = {
    "pikepdf",
    "pypdfium2",
    "pyhanko",
    "reportlab",
    "pillow",
    "img2pdf",
    "cryptography",
    "pymupdf",
    "python-docx",
}
MODULES = {"pillow": "PIL", "python-docx": "docx"}  # engine -> import name, where they differ
PASSPHRASE = "Lfi-Test-Pass-2026"
PLANTED = {  # group -> positive controls: (tool, sample, target)
    "core": [("pdf.to-text", "planted_pdf", "txt"), ("pdf.to-docx", "planted_pdf", "docx")],
    "office": [("office.to-pdf", "planted_rtf", "pdf")],
    "ocr": [("ocr.pdf", "planted_pdf", None)],
    "ebook": [("djvu.to-pdf", "planted_djvu", "pdf"), ("ebook.convert", "planted_pdf", "txt")],
}
SAMPLES = {s.name: s for s in (*corpus.SAMPLES, *corpus.PLANTED)}


def _targets(tool, fmt: str) -> list[str | None]:
    if not tool.converts:
        return [None]
    have = tool.targets([fmt])
    return [t for t in TARGET_ORDER if t in have][:2] or sorted(have)[:1]


def _cases(tools: dict) -> list[tuple[str, str, str | None]]:
    """(tool id, sample name, target) for every tool and every sample of a format it reads.
    Derived samples are named ``<base sample>.<format>``."""
    found = []
    for tool in sorted(tools.values(), key=lambda t: t.id):
        names = [s.name for s in corpus.SAMPLES if s.fmt in tool.inputs]
        names += [f"{b}.{f}" for f, b in corpus.DERIVED.items() if f in tool.inputs]
        for name in names:
            fmt = name.rsplit(".", 1)[1] if "." in name else SAMPLES[name].fmt
            found += [(tool.id, name, t) for t in _targets(tool, fmt)]
    return found


def _usable_here(tool) -> bool:
    if IN_IMAGE:
        return tool.group == GROUP
    if not set(tool.engines) <= PURE_PYTHON:
        return False
    return all(importlib.util.find_spec(MODULES.get(e, e)) for e in tool.engines)


TOOLS = {i: t for i, t in ALL.items() if _usable_here(t)}
CASES = [
    pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=KNOWN_BROKEN[c[:2]]))
    if c[:2] in KNOWN_BROKEN
    else c
    for c in _cases(TOOLS)
]


# --------------------------------------------------------------------------- harness


@dataclass
class Result:
    status: str
    outputs: list[Path]
    error: str = ""
    message: str = ""


def _p12() -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LFI Test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    enc = serialization.BestAvailableEncryption(PASSPHRASE.encode())
    return pkcs12.serialize_key_and_certificates(b"t", key, cert, None, enc)


def _aux(tool) -> tuple[dict[str, tuple[str, bytes]], dict]:
    """Required aux files and secrets: only pdf.sign needs any."""
    if tool.id == "pdf.sign":
        return {"certificate": ("p12", _p12())}, {"passphrase": PASSPHRASE}
    assert not any(a.required for a in tool.files.aux.values()), f"{tool.id}: add its aux here"
    return {}, {}


class Harness:
    """Runs one step of one tool on files and returns its outputs."""

    def __init__(self, tmp_path: Path, r=None):
        self.data = tmp_path / "data"
        self.r = r
        other = JobPaths(self.data, new_job_id())
        self.other = corpus.OtherJob.create(other.in_dir, self.data)
        self.n = 0

    def new_job(self) -> JobPaths:
        return JobPaths(self.data, new_job_id())

    def sample(self, name: str, paths: JobPaths, control: bool = False) -> tuple[Path, str]:
        """Build sample ``name`` for job ``paths`` (relative references start at its scratch
        dir). Derived samples run office.convert first."""
        other = replace(self.other, base=paths.scratch(0))
        if control:
            other = other.missing()
        self.n += 1
        work = self.data.parent / "samples" / str(self.n)
        work.mkdir(parents=True)
        if "." in name:
            base, fmt = name.rsplit(".", 1)
            src, _ = self.sample(base, paths, control)
            res = self.run(ALL["office.convert"], [(src, SAMPLES[base].fmt)], {}, fmt)
            assert res.status == "SUCCEEDED", f"making {name}: {res.error}"
            made = work / f"{name}"
            shutil.copyfile(res.outputs[0], made)
            return made, fmt
        s = SAMPLES[name]
        return s.build(work / f"{s.name}.{s.fmt}", other), s.fmt

    def run(self, tool, files: list[tuple[Path, str]], options: dict, target, paths=None) -> Result:
        paths = paths or self.new_job()
        aux, secret = _aux(tool)
        n = max(tool.files.min, 1)
        files = (files * n)[:n] if len(files) < n else files
        if IN_IMAGE:
            return self._runner(tool, paths, files, options, target, aux, secret)
        return self._direct(tool, paths, files, options, target, aux, secret)

    def _runner(self, tool, paths, files, options, target, aux, secret) -> Result:
        from filex.worker.runner import Config, Runner

        paths.in_dir.mkdir(parents=True)
        for i, (src, fmt) in enumerate(files):
            shutil.copyfile(src, paths.input(i, fmt))
        for slot, (fmt, data) in aux.items():
            paths.aux_dir.mkdir(exist_ok=True)
            paths.aux(slot, fmt).write_bytes(data)
        step = {"tool": tool.id, "options": options, "secret": bool(secret), "target": target}
        self.r.hset(
            job_key(paths.job_id),
            mapping={
                "status": "QUEUED",
                "steps": json.dumps([step]),
                "inputs": json.dumps([{"format": f, "name": p.name} for p, f in files]),
                "aux": json.dumps({slot: fmt for slot, (fmt, _) in aux.items()}),
            },
        )
        if secret:
            self.r.set(sec_key(paths.job_id, 0), json.dumps(secret))
        self.r.xadd(queue_key(tool.group), {"job": paths.job_id, "step": "0"})
        group_tools = {i: t for i, t in ALL.items() if t.group == tool.group}
        runner = Runner(
            self.r, Config(group=tool.group, data=self.data, heavy_slots=0), tools=group_tools
        )
        assert tool.id in runner.tools, f"{tool.id}: an engine probe failed in this image"
        assert runner.run_once()
        state = job(self.r, paths.job_id)
        if state["status"] != "SUCCEEDED":
            err = state.get("error") or {}
            return Result(state["status"], [], err.get("code", ""), err.get("message", ""))
        outs = [paths.output(o["index"], o["format"]) for o in state["outputs"]]
        return Result("SUCCEEDED", outs)

    def _direct(self, tool, paths, files, options, target, aux, secret) -> Result:
        from pydantic import ValidationError

        from filex.worker import step

        scratch = paths.scratch(0)
        scratch.mkdir(parents=True)
        names = []
        for i, (src, fmt) in enumerate(files):
            names.append(str(shutil.copyfile(src, scratch / paths.input(i, fmt).name)))
        aux_names = {}
        for slot, (fmt, data) in aux.items():
            p = scratch / f"{slot}.{fmt}"
            p.write_bytes(data)
            aux_names[slot] = str(p)
        out_dirs = [
            paths.step_dir(0) / f"w{k}" for k in range(len(files) if tool.files.each else 1)
        ]
        for d in out_dirs:
            d.mkdir(parents=True)
        req = {
            "module": tool.module,
            "tool": tool.id,
            "options": options,
            "secrets": secret,
            "inputs": names,
            "input_names": [Path(n).name for n in names],
            "aux": aux_names,
            "out_dirs": [str(d) for d in out_dirs],
            "target": target,
            "max_pages": 2000,
            "max_pixels": 100_000_000,
        }
        try:
            done = step.run(req, lambda **event: None)
        except StepError as e:
            return Result("FAILED", [], e.code, e.message)
        except ValidationError:
            return Result("FAILED", [], "INVALID_OPTIONS")
        return Result("SUCCEEDED", [out_dirs[k] / name for k, name, *_ in done])


@pytest.fixture
def harness(tmp_path, request):
    r = request.getfixturevalue("r") if IN_IMAGE else None
    return Harness(tmp_path, r)


def _options(tool) -> dict:
    ex = dict(tool.examples[0].options) if tool.examples else {}
    return {**ex, **OPTIONS.get(tool.id, {})}


def _ids(case) -> str:
    tool_id, name, target = case
    return f"{tool_id}-{name}" + (f"-{target}" if target else "")


# --------------------------------------------------------------------------- the engine test


@pytest.mark.parametrize(
    "case", CASES or [pytest.param(("", "", None), marks=pytest.mark.skip)], ids=_ids
)
def test_engine_reads_no_references(harness, case):
    tool_id, name, target = case
    tool = ALL[tool_id]
    paths = harness.new_job()
    src, fmt = harness.sample(name, paths)
    if "." in name:  # a derived binary sample must still hold the references
        blob = src.read_bytes()
        other_id = harness.other.text.parent.parent.name
        assert other_id.encode() in blob or other_id.encode("utf-16-le") in blob, (
            f"{name}: LibreOffice dropped the references when saving"
        )
    res = harness.run(tool, [(src, fmt)], _options(tool), target, paths)

    assert corpus.leaks(res.message.encode()) == []
    if res.status != "SUCCEEDED":
        assert res.error in REFUSED.get((tool_id, name.split(".")[0]), set()), (
            f"{tool_id} failed on {name}: {res.error} {res.message}"
        )
        return
    assert res.outputs
    for out in res.outputs:
        data = out.read_bytes()
        assert corpus.leaks(data) == [], f"{tool_id} on {name}: {out.name} holds a canary"
        base = SAMPLES[name.split(".")[0]]
        if base.max_images is not None and data[:5] == b"%PDF-":
            assert corpus.pdf_image_count(data) <= base.max_images, f"{out.name}: linked image"
    if SAMPLES[name.split(".")[0]].raster:
        control_paths = harness.new_job()
        csrc, _ = harness.sample(name, control_paths, control=True)
        ctl = harness.run(tool, [(csrc, fmt)], _options(tool), target, control_paths)
        assert ctl.status == res.status
        got = [corpus.rasters(p.read_bytes()) for p in res.outputs]
        want = [corpus.rasters(p.read_bytes()) for p in ctl.outputs]
        assert got == want, f"{tool_id} on {name}: pixels differ from the control run"


def _planted() -> list[tuple[str, str, str | None]]:
    if IN_IMAGE:
        return PLANTED.get(GROUP, [])
    return [c for c in PLANTED["core"] if ALL[c[0]].id in TOOLS]


@pytest.mark.parametrize(
    "case", _planted() or [pytest.param(("", "", None), marks=pytest.mark.skip)], ids=_ids
)
def test_planted_canary_is_reported(harness, case):
    """The checks are not blind: the canary inside the document itself must be found in the
    output of the same engines."""
    tool_id, name, target = case
    tool = ALL[tool_id]
    paths = harness.new_job()
    src, fmt = harness.sample(name, paths)
    res = harness.run(tool, [(src, fmt)], _options(tool), target, paths)
    assert res.status == "SUCCEEDED", res.error
    assert any(corpus.leaks(p.read_bytes()) for p in res.outputs)


# --------------------------------------------------------------------------- coverage bookkeeping


def test_every_engine_has_an_lfi_entry():
    engines = {e for t in ALL.values() for e in t.engines}
    missing = sorted(engines - LFI_COVERAGE.keys())
    assert not missing, (
        f"engines without an LFI test: {missing}. Add a sample to tests/integration/"
        "lfi_corpus.py (or a test) and an LFI_COVERAGE entry (plan task 64)."
    )


@pytest.mark.parametrize("engine", sorted(LFI_COVERAGE))
def test_lfi_entries_name_real_tests(engine):
    for ref in LFI_COVERAGE[engine]:
        if ref.startswith(NA):
            assert len(ref) > len(NA) + 20, f"{engine}: say why no test is needed"
            continue
        path, func = ref.split("::")
        text = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(func)}\(", text, re.MULTILINE), f"{ref} does not exist"


def test_engines_claimed_by_this_test_get_a_sample():
    """An engine mapped to this test must have a tool here that reads one of the samples."""
    run = {e for tool_id, *_ in _cases(ALL) for e in ALL[tool_id].engines}
    claimed = {e for e, refs in LFI_COVERAGE.items() if THIS in refs}
    assert claimed <= run, f"no sample reaches {sorted(claimed - run)}"


def test_coverage_matrix_lists_every_engine():
    doc = (ROOT / "docs" / "reviews" / "lfi-coverage.md").read_text(encoding="utf-8")
    assert [e for e in LFI_COVERAGE if f"| `{e}` |" not in doc] == []


def test_corpus_readme_lists_every_sample():
    readme = (ROOT / "tests" / "corpus" / "README.md").read_text(encoding="utf-8")
    assert [s.name for s in (*corpus.SAMPLES, *corpus.PLANTED) if f"`{s.name}`" not in readme] == []


@pytest.mark.parametrize("sample", corpus.SAMPLES, ids=lambda s: s.name)
def test_samples_hold_references_and_no_canary(tmp_path, sample):
    """Each sample names the other job's files (or the host file) and carries no canary of its
    own, so a canary in an output can only come from following a reference."""
    other = corpus.OtherJob.create(tmp_path / "jobs" / "x" / "in", tmp_path / "tmp" / "x.0")
    data = sample.build(tmp_path / f"0.{sample.fmt}", other).read_bytes()
    assert corpus.leaks(data) in ([], ["hostname"])
    blobs = b"".join(corpus._texts(data))
    names = [
        str(other.text),
        str(other.png),
        str(other.djvi),
        str(other.docx),
        str(other.ods),
        other.text.as_uri(),
        other.png.as_uri(),
        other.docx.as_uri(),
        other.ods.as_uri(),
        corpus.HOST,
        str(other.pdf),
    ]
    assert any(n.encode() in blobs for n in names), f"{sample.name} refers to nothing"


def test_planted_samples_are_detected(tmp_path):
    other = corpus.OtherJob.create(tmp_path / "in", tmp_path)
    for s in corpus.PLANTED:
        assert corpus.leaks(s.build(tmp_path / f"{s.name}.{s.fmt}", other).read_bytes())
