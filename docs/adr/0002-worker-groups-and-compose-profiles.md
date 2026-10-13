# ADR-0002: One worker image per engine family, heavy groups behind compose profiles

## Status
Proposed. Amended 2026-10-10: final group split (six groups) and memory budget, see the Amendment.

## Date
2026-10-10

## Context
FileX engines vary hugely in size and RAM: qpdf/pikepdf are megabytes, LibreOffice and
Calibre are hundreds of MB each and use hundreds of MB per conversion, ML upscaling/OCR
models need GBs. The first target is a home server that may have plenty of RAM, but FileX must stay usable on
2-4 GB / 2-4 cores, and ARM64 / Docker Desktop must not be blocked later.

## Decision
Split workers into five images, all `FROM filex-base` (Python 3.12 slim + `filex` + runner):

| Group | Engines | Compose profile |
|---|---|---|
| `core` | qpdf, pikepdf, PyMuPDF, Ghostscript, Pillow, img2pdf, pyHanko, OpenSSL | none (always on) |
| `office` | LibreOffice headless, Pandoc | `office` |
| `ocr` | OCRmyPDF, Tesseract + languages, unpaper, OpenCV | `ocr` |
| `ebook` | Calibre, djvulibre | `ebook` |
| `ml` | ONNX Runtime CPU models (upscaling, ML OCR) | `ml` |

Every optional group is also in profile `all`. The admin selects groups with
`COMPOSE_PROFILES` in `.env`. Each worker has `mem_limit`, `cpus` and
`FILEX_CONCURRENCY` (default 1). Tools of a disabled group show as unavailable, with the
profile name to enable.

A new group is created only for a new *heavy* engine family; light engines join an
existing group (usually `core`).

## Alternatives Considered

### One fat worker image
- Pros: simplest build and compose.
- Cons: multi-GB image everyone must pull; weak boxes pay for LibreOffice and ML they never
  use; one leaky engine shares a container with all others.
- Rejected.

### One container per engine
- Pros: maximal isolation and tiny images.
- Cons: 15+ containers, idle RAM per container adds up; many tools combine engines
  (OCRmyPDF uses Tesseract + Ghostscript + unpaper).
- Rejected.

### Engines installed on demand at runtime
- Pros: one small image.
- Cons: needs network in workers (violates ADR-0007), not reproducible.
- Rejected.

## Consequences
- The always-on footprint is API + Redis + `worker-core`, targeted under ~1.5 GB peak
  (to be measured).
- Five Dockerfiles to keep multi-arch; `ml` may need an x86-only list until ARM64 wheels are
  checked.
- The engine picked per tool (from engine research) decides its group; moving a tool between groups
  is a one-line change in its declaration.
- Ghostscript ends up in both `core` and `ocr` images; accepted duplication.

## Amendment (2026-10-10): final group split and memory budget

Resolves spec open items A1 and A3 with the decisions of 2026-10-10 (ebooks in R1, default
profiles `office,ocr,ebook`, local AI in R2; AI deferred since, see ADR-0010).

### Groups (A1)

| Group | Engines | Profile | Heavy step | Release |
|---|---|---|---|---|
| `core` | qpdf, pikepdf, PyMuPDF, pypdfium2, Ghostscript, Pillow (+pillow-heif), img2pdf, reportlab, python-docx, pyHanko, cryptography/OpenSSL | none (always on) | no | R1 |
| `office` | LibreOffice headless + fonts; WeasyPrint (R2) | `office` | yes | R1 |
| `ocr` | OCRmyPDF, Tesseract (eng, rus, deu, fra, spa, ita), unpaper, OpenCV-headless; veraPDF (R2) | `ocr` | yes | R1 |
| `ebook` | Calibre `ebook-convert`, **Pandoc**, djvulibre, libarchive (CBR), img2pdf, pypdfium2 | `ebook` | yes | R1 |
| `ml` | ONNX Runtime CPU models, Docling, upscaling, background removal (the earlier `ml-image` group folded in) | `ml` | yes | R2/R3 |
| `ai` | Ollama server + `worker-ai` ([ADR-0010](0010-ai-worker-group.md)) | `ai` | yes | Deferred |

- **No Chromium / `html` group.** HTML and Markdown to PDF use WeasyPrint (no JS, no network,
  ~60 MB). Chromium is only needed for URL to PDF, which needs egress; it waits for the egress ADR.
- **Pandoc lives in `ebook` only.** `markup.convert` (R1) is there, and it is ~150 MB we do not
  want twice. R2 `markdown.to-pdf` becomes a two-step route, `md → html` (Pandoc, `ebook`) then
  `html → pdf` (WeasyPrint, `office`), planned by `convert` (ADR-0005); both groups are in the
  default profiles. Pandoc in `office` was dropped.
- `ml-image` is not a group: one optional ML image until it passes ~5 GB.
- veraPDF stays out of `core`: CI test image in R1, `ocr` image in R2 with `pdf.pdfa-validate`.
  Camelot + OpenCV join `core` only if R2 `pdf.to-xlsx` needs lattice mode.
- Images: one multi-stage `docker/Dockerfile` with a `base` stage and one target per service
  (`api`, `worker-core`, then `worker-office`, `worker-ocr`, `worker-ebook`, ...), as scaffolded.
- Base image (2026-10-10): Debian 13 trixie, `python:3.12-slim-trixie` (web build:
  `node:24-trixie-slim`). Bookworm's Ghostscript 10.00.0 is below the runner's 10.03.1 minimum
  (security review section 2, H4), so `pdf.compress` was unavailable; trixie main ships
  Ghostscript 10.05.1 and LibreOffice 25.2, which also removes the bookworm-backports source.
  Engines come from Debian main with its security updates; the probe minimums stay as they are.

### Memory budget (A3)

`mem_limit` reserves nothing; it caps. What has to fit in RAM is the idle footprint of every
running container plus the peaks that can happen **at the same time**. So:

1. **One heavy-step slot.** Every group except `core` runs heavy steps. Before a heavy step's
   child starts, the runner takes a lease `slot:heavy:<i>` (`SET NX PX 30000`, `i <
   FILEX_HEAVY_SLOTS`), renews it every 10 s while the step runs, and releases it with a
   compare-and-delete when the step ends. While it waits it keeps its stream entry alive with
   `XCLAIM ... JUSTID` (no delivery-count bump, so waiting is never mistaken for a dead worker)
   and shows "waiting for a free slot" as the step message; the job stays `QUEUED`. A runner that
   dies holding the slot loses it after 30 s. `FILEX_HEAVY_SLOTS=0` turns the slot off.
2. **Memory limits from `.env`:** `FILEX_MEM_CORE`, `FILEX_MEM_OFFICE`, `FILEX_MEM_OCR`,
   `FILEX_MEM_EBOOK` (and later `_ML`, `_AI`) feed each service's `mem_limit` and
   `memswap_limit`. The runner clamps each tool's `limits.memory_mb` to its container's limit.

| Setting | Default `.env.example` | `low-memory` preset (4 GB box, or 2 GB with core only) | Big box (16 GB or more) |
|---|---|---|---|
| `COMPOSE_PROFILES` | `office,ocr,ebook` | `office,ocr,ebook` on 4 GB; empty on 2 GB | `office,ocr,ebook` (+`ml`, `ai` later) |
| `FILEX_HEAVY_SLOTS` | `1` | `1` | `0` (off) or `3` |
| `FILEX_MEM_CORE` | `1g` | `768m` | `2g` |
| `FILEX_MEM_OFFICE` / `_OCR` / `_EBOOK` | `1536m` / `1536m` / `1g` | `1g` each | `2g` each |
| `FILEX_CONCURRENCY` | `1` | `1` | `2`-`4` (not `office`: always `1`, ADR-0007 consequences, security review L-2) |
| `FILEX_MAX_PIXELS` / `FILEX_MAX_PAGES` | 100 MP / 2000 | 50 MP / 1000 | defaults |
| Worst-case RAM (API 256m + Redis 64m + core peak + one heavy peak + two idle heavy workers at ~150 MB) | ~3.1 GB | ~2.4 GB (4 GB box keeps ~1.6 GB for the OS); ~1.1 GB core-only | not a constraint |

The preset is a commented block in `.env.example` plus a line in the install guide; it is values
only, no code path. Calibre is a heavy step like LibreOffice and OCR, so `office`, `ocr` and
`ebook` never peak together on a default install. Benchmarks replace these estimates with
measured peaks in R1; a wrong number is changed here with the measurement.

### Consequences of the amendment
- On a default install heavy steps from different groups queue behind each other; a large OCR
  job delays an Office conversion. That is the price of running three heavy groups on 4 GB; big
  boxes set `FILEX_HEAVY_SLOTS=0`.
- The `worker` Redis ACL user gets `~slot:*` ([ADR-0007](0007-sandboxing-untrusted-files.md)).
- Six groups, six Dockerfile targets eventually; `ml` and `ai` may stay x86-only until ARM64
  wheels are checked.
