"""Weak-hardware benchmark (plan task 43): run a separate FileX stack whose containers share one
parent cgroup capped at e.g. 2 GB / 2 CPUs, push representative jobs through the real API and
record wall time, peak memory per container, OOM kills and idle memory. Standard library only;
runs on the host (Windows or Linux) next to Docker. Images must exist (``docker compose build``).

    python scripts/bench/weak_hardware.py stack --mem-mb 2048 --cpus 2 --preset low --reps 3
    python scripts/bench/weak_hardware.py stack --mem-mb 4096 --cpus 4 --preset default --reps 3
    python scripts/bench/weak_hardware.py calibre --reps 2     # Calibre EPUB -> PDF memory sweep

The stack is project ``filex-bench`` on 127.0.0.1:8090 with its own volume and internal subnet,
sign-in off; it is taken down with its volume at the end (``--keep`` leaves it up). The parent
cgroup is managed by a helper container (``filex-bench-probe``, privileged, no network) running
``cgprobe.py``; it is removed at the end too. Results: JSON to ``--out`` and a Markdown summary
on stdout. Inputs come from tests/corpus (``scan_50p_30mb.pdf`` and ``text_500p.pdf`` need
``python scripts/make_corpus.py --large``); long EPUBs are generated into a temp dir.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests" / "corpus"
PROJECT, PARENT, PROBE = "filex-bench", "filex-bench", "filex-bench-probe"
PORT, SUBNET = 8090, "10.213.90.0/24"
BASE = f"http://127.0.0.1:{PORT}/api/v1"
SERVICES = ["api", "redis", "worker-core", "worker-office", "worker-ocr", "worker-ebook"]
CORE = ["api", "redis", "worker-core"]

PRESETS = {  # .env.example: defaults and the low-memory block
    "default": {
        "FILEX_MEM_CORE": "1g",
        "FILEX_MEM_OFFICE": "1536m",
        "FILEX_MEM_OCR": "1536m",
        "FILEX_MEM_EBOOK": "1g",
    },
    "low": {
        "FILEX_MEM_CORE": "768m",
        "FILEX_MEM_OFFICE": "1g",
        "FILEX_MEM_OCR": "1g",
        "FILEX_MEM_EBOOK": "1g",
        "FILEX_MAX_PIXELS": "50000000",
        "FILEX_MAX_PAGES": "1000",
    },
}


def sh(*args: str, timeout: float = 600, check: bool = True, **kw) -> str:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    p = subprocess.run(
        args, check=False, capture_output=True, text=True, timeout=timeout, env=env, **kw
    )
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(args)}: {p.stderr.strip()[-800:]}")
    return p.stdout


# --- long books ------------------------------------------------------------------------------
WORDS = [
    "the",
    "of",
    "and",
    "to",
    "in",
    "a",
    "is",
    "that",
    "for",
    "it",
    "as",
    "was",
    "with",
    "be",
    "by",
    "on",
    "not",
    "he",
    "this",
    "are",
    "or",
    "his",
    "from",
    "at",
    "which",
    "but",
    "have",
    "an",
    "they",
    "you",
    "were",
    "her",
    "she",
    "there",
    "been",
    "one",
    "all",
    "we",
    "their",
    "has",
    "would",
    "when",
    "river",
    "garden",
    "window",
    "morning",
    "letter",
    "quietly",
    "station",
    "harbour",
    "mountain",
    "lantern",
]


def make_epub(path: Path, est_pages: int) -> Path:
    """EPUB whose text makes ``est_pages`` by ebook_convert.estimated_pages (2200 B/page, x4/5)."""
    target = est_pages * 2200 * 5 // 4
    chapters, i, rnd = [], 0, 12345
    per_ch = 60_000
    while (done := sum(len(c) for c in chapters)) < target - 1000:
        paras = []
        size = 0
        while size < min(per_ch, target - done - 1000):
            words = []
            for _ in range(90):
                rnd = (rnd * 1103515245 + 12345) & 0x7FFFFFFF
                words.append(WORDS[rnd % len(WORDS)])
            p = "<p>" + " ".join(words).capitalize() + ".</p>\n"
            paras.append(p)
            size += len(p)
        i += 1
        chapters.append(
            '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>Chapter {i}</title></head><body><h1>Chapter {i}</h1>\n"
            + "".join(paras)
            + "</body></html>"
        )
    items = "".join(
        f'<item id="c{n}" href="c{n}.xhtml" media-type="application/xhtml+xml"/>'
        for n in range(len(chapters))
    )
    spine = "".join(f'<itemref idref="c{n}"/>' for n in range(len(chapters)))
    nav = (
        '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Nav</title></head><body>'
        '<nav epub:type="toc"><ol>'
        + "".join(
            f'<li><a href="c{n}.xhtml">Chapter {n + 1}</a></li>' for n in range(len(chapters))
        )
        + "</ol></nav></body></html>"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/'
        f'1.1/"><dc:identifier id="id">urn:uuid:{uuid.uuid4()}</dc:identifier><dc:title>Long '
        f'book {est_pages}</dc:title><dc:language>en</dc:language><meta property="dcterms:'
        'modified">2026-10-13T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.'
        f'xhtml" media-type="application/xhtml+xml" properties="nav"/>{items}</manifest>'
        f"<spine>{spine}</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:'
            'opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.'
            'opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
            zipfile.ZIP_DEFLATED,
        )
        z.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, zipfile.ZIP_DEFLATED)
        for n, c in enumerate(chapters):
            z.writestr(f"OEBPS/c{n}.xhtml", c, zipfile.ZIP_DEFLATED)
    return path


# --- probe -----------------------------------------------------------------------------------
class Probe:
    def __init__(self) -> None:
        if not sh("docker", "ps", "-q", "-f", f"name=^{PROBE}$"):
            sh("docker", "rm", "-f", PROBE, check=False)
            sh(
                "docker",
                "run",
                "-d",
                "--name",
                PROBE,
                "--privileged",
                "--cgroupns=host",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "sleep",
                "filex-worker-core:local",
                "infinity",
            )
        sh("docker", "cp", str(Path(__file__).with_name("cgprobe.py")), f"{PROBE}:/cgprobe.py")
        self.p = subprocess.Popen(
            ["docker", "exec", "-i", PROBE, "python3", "-u", "/cgprobe.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def __call__(self, **cmd) -> dict:
        self.p.stdin.write(json.dumps(cmd) + "\n")
        self.p.stdin.flush()
        reply = json.loads(self.p.stdout.readline())
        if "error" in reply:
            raise RuntimeError(f"probe {cmd['cmd']}: {reply['error']}")
        return reply

    def close(self, remove: bool) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)
        if remove:
            sh("docker", "rm", "-f", PROBE, check=False)


# --- API -------------------------------------------------------------------------------------
def http(method: str, path: str, body: bytes | None = None, ctype: str | None = None) -> dict:
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"httpError": e.code, "body": e.read()[:500].decode(errors="replace")}


def submit(tool: str, files: list[Path], target: str | None, options: dict | None) -> dict:
    b = uuid.uuid4().hex
    parts = []
    for f in files:
        parts.append(
            f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{f.name}"'
            f"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
            + f.read_bytes()
            + b"\r\n"
        )
    for name, value in (("target", target), ("options", options and json.dumps(options))):
        if value:
            parts.append(
                f'--{b}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode()
            )
    body = b"".join(parts) + f"--{b}--\r\n".encode()
    return http("POST", f"/tools/{tool}/jobs", body, f"multipart/form-data; boundary={b}")


def wait(job: dict, timeout: float) -> dict:
    if "id" not in job:
        return {"status": "REJECTED", "error": job}
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j = http("GET", f"/jobs/{job['id']}")
        if j.get("status") in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return j
        time.sleep(0.25)
    return {"id": job["id"], "status": "TIMEOUT"}


# --- stack -----------------------------------------------------------------------------------
def compose(env_file: Path, *args: str, timeout: float = 600) -> str:
    return sh(
        "docker",
        "compose",
        "-p",
        PROJECT,
        "--env-file",
        str(env_file),
        "-f",
        str(REPO / "compose.yaml"),
        "-f",
        str(REPO / "scripts" / "bench" / "weak-hw.compose.yaml"),
        *args,
        cwd=REPO,
        timeout=timeout,
    )


def container_ids(env_file: Path) -> dict[str, str]:
    ids = {}
    for line in compose(env_file, "ps", "--format", "{{.Service}} {{.ID}}").split("\n"):
        if line.strip():
            svc, short = line.split()
            ids[svc] = sh("docker", "inspect", "-f", "{{.Id}}", short).strip()
    return ids


def cases(books: Path) -> list[dict]:
    pdf, gen = CORPUS / "pdf", CORPUS / "generated"
    return [
        {
            "name": "pdf.compress scan 50p 30MB",
            "tool": "pdf.compress",
            "files": [gen / "scan_50p_30mb.pdf"],
            "options": {"level": "MEDIUM"},
        },
        {
            "name": "pdf.merge 2x10p",
            "tool": "pdf.merge",
            "files": [pdf / "text_10p.pdf", pdf / "text_10p.pdf"],
        },
        {
            "name": "pdf.merge 500p+50p scan+10p",
            "tool": "pdf.merge",
            "files": [gen / "text_500p.pdf", gen / "scan_50p_30mb.pdf", pdf / "text_10p.pdf"],
        },
        {
            "name": "pdf.to-docx 10p",
            "tool": "pdf.to-docx",
            "files": [pdf / "text_10p.pdf"],
            "target": "docx",
        },
        {
            "name": "pdf.to-docx 500p",
            "tool": "pdf.to-docx",
            "files": [gen / "text_500p.pdf"],
            "target": "docx",
        },
        {
            "name": "office.to-pdf docx 20p",
            "tool": "office.to-pdf",
            "files": [CORPUS / "office" / "report_20p.docx"],
        },
        {
            "name": "office.to-pdf xlsx",
            "tool": "office.to-pdf",
            "files": [CORPUS / "office" / "budget.xlsx"],
        },
        {
            "name": "ocr.pdf scan 3p",
            "tool": "ocr.pdf",
            "files": [pdf / "scanned_3p.pdf"],
            "options": {"languages": "eng"},
        },
        {
            "name": "ocr.pdf scan 50p",
            "tool": "ocr.pdf",
            "files": [gen / "scan_50p_30mb.pdf"],
            "options": {"languages": "eng"},
        },
        {
            "name": "ebook.convert epub->mobi",
            "tool": "ebook.convert",
            "files": [CORPUS / "ebook" / "book.epub"],
            "target": "mobi",
        },
        {
            "name": "ebook.convert long epub->pdf (~200 est. p)",
            "tool": "ebook.convert",
            "files": [books / "long_200.epub"],
            "target": "pdf",
        },
        {
            "name": "ebook.convert long epub->pdf (~228 est. p, at the cap)",
            "tool": "ebook.convert",
            "files": [books / "long_228.epub"],
            "target": "pdf",
        },
        {
            "name": "pdf.compress scan 200MB (stress)",
            "tool": "pdf.compress",
            "files": [gen / "scan_200mb.pdf"],
            "options": {"level": "MEDIUM"},
        },
    ]


def mix_jobs(books: Path, heavy: bool = False) -> list[dict]:
    """Office, OCR and Calibre submitted together: the heavy slot should serialise them.
    ``heavy``: the biggest of each (50-page OCR, a book at the PDF page cap)."""
    scan = (
        CORPUS / "generated" / "scan_50p_30mb.pdf" if heavy else CORPUS / "pdf" / "scanned_3p.pdf"
    )
    return [
        {"tool": "office.to-pdf", "files": [CORPUS / "office" / "report_20p.docx"]},
        {"tool": "ocr.pdf", "files": [scan], "options": {"languages": "eng"}},
        {
            "tool": "ebook.convert",
            "files": [books / f"long_{228 if heavy else 200}.epub"],
            "target": "pdf",
        },
    ]


def measure(probe: Probe, ids: dict[str, str], jobs: list[dict], timeout: float) -> dict:
    probe(cmd="reset", ids=ids)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(len(jobs)) as ex:
        done = list(
            ex.map(
                lambda c: (
                    wait(submit(c["tool"], c["files"], c.get("target"), c.get("options")), timeout),
                    time.perf_counter() - t0,
                ),
                jobs,
            )
        )
    mem = probe(cmd="read")
    return {
        "wall_s": [round(w, 2) for _, w in done],
        "status": [j.get("status") for j, _ in done],
        "error": [j.get("error") for j, _ in done],
        "job": [j.get("id") for j, _ in done],
        "mem": mem,
    }


def idle(probe: Probe, ids: dict[str, str], samples: int = 5) -> dict:
    probe(cmd="reset", ids=ids)
    got: dict[str, list[float]] = {}
    for _ in range(samples):
        for name, m in probe(cmd="read").items():
            got.setdefault(name, []).append(m.get("current_mb", 0.0))
            got.setdefault(name + ":anon", []).append(m.get("anon_mb", 0.0))
        time.sleep(2)
    return {k: statistics.median(v) for k, v in got.items()}


def step_durations(env_file: Path) -> dict[str, float]:
    out = {}
    logs = compose(
        env_file,
        "logs",
        "--no-color",
        "--no-log-prefix",
        "worker-core",
        "worker-office",
        "worker-ocr",
        "worker-ebook",
    )
    for line in logs.splitlines():
        if '"step ' in line:
            try:
                e = json.loads(line[line.index("{") :])
            except ValueError:
                continue
            if "duration_s" in e:
                out[e["job"]] = e["duration_s"]
    return out


def run_stack(a: argparse.Namespace) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="filex-bench-"))
    books = tmp
    make_epub(books / "long_200.epub", 200)
    make_epub(books / "long_228.epub", 228)
    profiles = a.profiles
    env = {
        "COMPOSE_PROFILES": profiles,
        "FILEX_PORT": str(PORT),
        "FILEX_BIND": "127.0.0.1",
        "FILEX_INTERNAL_SUBNET": SUBNET,
        "FILEX_AUTH": "none",
        "FILEX_CONCURRENCY": str(a.concurrency),
        "FILEX_HEAVY_SLOTS": str(a.heavy_slots),
        "FILEX_MAX_QUEUED": "50",
        "FILEX_MIN_FREE_MB": "1024",
        **PRESETS[a.preset],
    }
    env_file = tmp / "bench.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    probe = Probe()
    result: dict = {"config": {"mem_mb": a.mem_mb, "cpus": a.cpus, **env}, "runs": {}}
    try:
        result["cgroup"] = probe(cmd="limits", parent=PARENT, mem_mb=a.mem_mb, cpus=a.cpus)
        compose(env_file, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
        result["images"] = {
            s: sh("docker", "inspect", "-f", "{{.Id}}", f"filex-{s}:local").strip()[7:19]
            for s in ("api", "worker-core", "worker-office", "worker-ocr", "worker-ebook")
        }
        ids = container_ids(env_file)
        groups = {"core", *profiles.split(",")} - {""}
        for _ in range(120):  # every worker heartbeat in
            tools = http("GET", "/tools")
            if isinstance(tools, list) and all(
                t["available"] for t in tools if t["group"] in groups
            ):
                break
            time.sleep(1)
        time.sleep(a.settle)
        result["idle"] = idle(probe, ids)
        result["idle_core_only_sum_mb"] = sum(result["idle"].get(s, 0) for s in CORE)
        result["idle_all_sum_mb"] = sum(result["idle"].get(s, 0) for s in ids)
        todo = [c for c in cases(books) if not a.only or any(o in c["name"] for o in a.only)]
        todo = [c for c in todo if any(c["tool"] == t["id"] for t in tools if t["group"] in groups)]
        for rep in range(a.reps):  # interleaved, so drift hits every case alike
            for c in todo:
                r = measure(probe, ids, [c], a.timeout)
                result["runs"].setdefault(c["name"], []).append(r)
                print(
                    f"[{rep}] {c['name']}: {r['status'][0]} {r['wall_s'][0]} s "
                    f"parent peak {r['mem']['_parent'].get('peak_mb')} MB",
                    file=sys.stderr,
                )
            if a.mix and groups >= {"office", "ocr", "ebook"}:
                r = measure(probe, ids, mix_jobs(books, a.mix_heavy), a.timeout)
                label = (
                    "mix office+ocr 50p+calibre 228p" if a.mix_heavy else "mix office+ocr+calibre"
                )
                result["runs"].setdefault(label, []).append(r)
                print(
                    f"[{rep}] mix: {r['status']} {r['wall_s']} parent peak "
                    f"{r['mem']['_parent'].get('peak_mb')} MB",
                    file=sys.stderr,
                )
        if a.burst:
            for rep in range(a.reps):
                c = next(c for c in cases(books) if a.burst_case in c["name"])
                r = measure(probe, ids, [c] * a.burst, a.timeout)
                result["runs"].setdefault(f"burst {a.burst}x {c['name']}", []).append(r)
                print(f"[{rep}] burst: {r['status']} {r['wall_s']}", file=sys.stderr)
        result["step_s"] = step_durations(env_file)
        result["restarts"] = {
            s: sh("docker", "inspect", "-f", "{{.RestartCount}} {{.State.OOMKilled}}", cid).strip()
            for s, cid in ids.items()
        }
    finally:
        if not a.keep:
            compose(env_file, "down", "-v", "--timeout", "20", timeout=180)
            probe(cmd="rmdir")
        probe.close(remove=not a.keep)
    return result


# --- Calibre sweep ---------------------------------------------------------------------------
def run_calibre(a: argparse.Namespace) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="filex-bench-cal-"))
    for n in a.pages:
        make_epub(tmp / f"long_{n}.epub", n)
    # Like the real step (filex.tools.ebook._engine): RLIMIT_DATA on the Calibre process tree,
    # fresh config/temp dirs, headless Qt, the 300 s engine timeout.
    script = """
import json, os, resource, subprocess, sys, time
rl = int(os.environ["RL_MB"]) * 2**20
w = "/tmp/w"
env = {**os.environ, "HOME": w, "CALIBRE_CONFIG_DIRECTORY": w + "/c", "CALIBRE_TEMP_DIR": w + "/t",
       "CALIBRE_CACHE_DIRECTORY": w + "/k", "QT_QPA_PLATFORM": "offscreen",
       "QTWEBENGINE_DISABLE_SANDBOX": "1",
       "QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox --disable-gpu --disable-dev-shm-usage"}
os.makedirs(w + "/t", exist_ok=True)
t = time.time()
try:
    rc = subprocess.run(["ebook-convert", "/in/" + os.environ["BOOK"], w + "/out.pdf",
                         "--paper-size", "a4"], env=env, cwd=w, capture_output=True, timeout=300,
                        preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_DATA, (rl, rl))
                        ).returncode
except subprocess.TimeoutExpired:
    rc = "timeout"
s = time.time() - t
try:
    import pikepdf
    pages = len(pikepdf.open(w + "/out.pdf").pages)
except Exception:
    pages = 0
ev = dict(l.split() for l in open("/sys/fs/cgroup/memory.events"))
print(json.dumps({"rc": rc, "s": round(s, 1), "pages": pages, "oom_kill": int(ev["oom_kill"]),
                  "peak_mb": int(open("/sys/fs/cgroup/memory.peak").read()) // 2**20}))
"""
    out: dict = {"runs": []}
    for rep in range(a.reps):
        for rl_mb, ct_mb in a.configs:
            for n in a.pages:
                line = sh(
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    "10001:10001",
                    "-m",
                    f"{ct_mb}m",
                    "--memory-swap",
                    f"{ct_mb}m",
                    "--cpus",
                    str(a.cpus),
                    "-v",
                    f"{tmp}:/in:ro",
                    "-e",
                    f"RL_MB={rl_mb}",
                    "-e",
                    f"BOOK=long_{n}.epub",
                    "--entrypoint",
                    "python3",
                    "filex-worker-ebook:local",
                    "-c",
                    script,
                    timeout=420,
                    check=False,
                )
                try:
                    got = json.loads(line.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    got = {"rc": "no output (container killed?)"}
                r = {"rlimit_mb": rl_mb, "container_mb": ct_mb, "est_pages": n, **got}
                out["runs"].append(r)
                print(r, file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("stack")
    s.add_argument("--mem-mb", type=int, default=2048)
    s.add_argument("--cpus", type=float, default=2)
    s.add_argument("--preset", choices=PRESETS, default="low")
    s.add_argument("--profiles", default="office,ocr,ebook")
    s.add_argument("--concurrency", type=int, default=1)
    s.add_argument("--heavy-slots", type=int, default=1)
    s.add_argument("--reps", type=int, default=3)
    s.add_argument("--settle", type=float, default=45, help="seconds before the idle sample")
    s.add_argument("--timeout", type=float, default=1900, help="per job")
    s.add_argument("--only", nargs="*", help="substrings of case names")
    s.add_argument("--no-mix", dest="mix", action="store_false")
    s.add_argument("--mix-heavy", action="store_true", help="the biggest job of each heavy group")
    s.add_argument("--burst", type=int, default=0, help="N copies of one case at once, at the end")
    s.add_argument("--burst-case", default="pdf.compress scan 50p", help="substring of its name")
    s.add_argument("--keep", action="store_true")
    s.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("calibre")
    c.add_argument("--pages", type=int, nargs="*", default=[150, 230, 300, 400])
    c.add_argument(
        "--configs",
        type=lambda v: tuple(map(int, v.split(":"))),
        nargs="*",
        default=[(964, 1024), (1600, 1536), (1600, 2048)],
        help="RLIMIT_DATA_MB:CONTAINER_MB pairs",
    )
    c.add_argument("--cpus", type=float, default=2)
    c.add_argument("--reps", type=int, default=2)
    c.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    res = run_stack(a) if a.mode == "stack" else run_calibre(a)
    a.out.write_text(json.dumps(res, indent=1, default=str))
    print(f"wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
