# FileX architecture

Status: proposed, updated 2026-10-10 with the decisions of that day and the completed security
architecture review; updated 2026-10-13 for the public local mode
([ADR-0011](adr/0011-public-local-mode.md)). Decisions are recorded in [docs/adr/](adr/).

Diagrams:

- [System architecture](diagrams/system-architecture.html): containers, networks, optional sign-in server, worker groups, volume
- [Job lifecycle](diagrams/job-lifecycle.html): job states, heavy-step slot, step loop, redelivery and retention

This is the smallest design that meets the project's constraints. Anything not needed for
"web UI + REST API, one person or a few invited users on a private network, weak hardware must
work" is listed under [Deliberately deferred](#10-deliberately-deferred) rather than designed now.

## ADR index

| ADR | Decision | Status |
|---|---|---|
| [0001](adr/0001-redis-streams-job-queue.md) | Redis Streams job queue, thin in-house runner | Proposed |
| [0002](adr/0002-worker-groups-and-compose-profiles.md) | One image per engine family, profiles; six groups and the memory budget (amended) | Proposed, amended |
| [0003](adr/0003-tool-plugin-model.md) | Tools declared once; aux files, secret options, warnings, output roles (amended) | Proposed, amended |
| [0004](adr/0004-openapi-generated-from-registry.md) | OpenAPI generated from the registry, committed, diff-checked | Proposed |
| [0005](adr/0005-conversion-routing.md) | Conversion routing over a format graph (amended for ebooks) | Proposed, amended |
| [0006](adr/0006-optional-shared-token-auth.md) | Optional shared password | **Superseded by 0009** |
| [0007](adr/0007-sandboxing-untrusted-files.md) | Sandboxing; required-before-R1 fixes and config baseline (amended) | Accepted (security sign-off) |
| [0008](adr/0008-storage-and-retention.md) | Data volume, retention 24 h, Redis keys (amended) | Proposed, amended |
| [0009](adr/0009-keel-auth.md) | Supabase-compatible sign-in, allowlist, hashed static tokens, signed download links, `/config` | Accepted (security sign-off); amended by 0011 |
| [0010](adr/0010-ai-worker-group.md) | `ai` group: Ollama server + `worker-ai`, Unix-socket relay | **Deferred** |
| [0011](adr/0011-public-local-mode.md) | Public build runs in local mode; LAN access through access tokens | Accepted |

## 1. Constraints this design answers

| Constraint | Where it is handled |
|---|---|
| Home server x86-64 first, ARM64 / Windows / macOS later | Debian 13 (trixie) slim multi-arch base, one multi-stage Dockerfile, named volumes, no host paths ([ADR-0002](adr/0002-worker-groups-and-compose-profiles.md)) |
| Usable on 2-4 GB RAM, 2-4 cores | Always-on core is API + Redis + `worker-core`; heavy groups are profiles; one heavy-step slot; a `low-memory` preset; files streamed to disk ([section 4](#4-job-model)) |
| Web UI + REST API, pipelines later | One REST API used by both; a job is already a list of steps |
| Local mode by default; optional sign-in through a Supabase-compatible server, invited users only | Loopback-only local mode ([ADR-0011](adr/0011-public-local-mode.md)); otherwise Bearer tokens checked against the sign-in server, plus a FileX allowlist; hashed static tokens for scripts and LAN devices; refuses to start with no auth ([ADR-0009](adr/0009-keel-auth.md)) |
| Uploaded files are untrusted | No-network workers, hardened containers, a seccomp-filtered child per step, escaped processes killed, Redis ACLs, files auto-deleted ([ADR-0007](adr/0007-sandboxing-untrusted-files.md)) |
| 100+ tools, free engines only | Tool plugin model: one declaration per tool generates API, UI catalog and docs ([ADR-0003](adr/0003-tool-plugin-model.md)) |

## 2. Components

| Component | Container | Always on | Role |
|---|---|---|---|
| Web UI | built into `api` image (static files) | yes | React + Vite SPA. Reads `GET /config` at start, signs in with supabase-js when a sign-in server is configured, renders option forms from each tool's JSON Schema. |
| API | `api` | yes | FastAPI. Auth (sign-in server + static tokens, or local mode), streaming uploads to disk, sniffing and validation, conversion planning, jobs, status, download links, catalog, OpenAPI. Runs the janitor. The only container that talks to another server (the optional sign-in server). |
| Queue + job state | `redis` | yes | Valkey 8 (Redis-compatible, BSD-3-Clause), ACL users `api` and `worker`, no persistence ([ADR-0001](adr/0001-redis-streams-job-queue.md)). One stream per group, one hash per job, all with TTLs. |
| Worker `core` | `worker-core` | yes | PDF, images, signing, encryption: qpdf, pikepdf, PyMuPDF, pypdfium2, Ghostscript, Pillow, img2pdf, reportlab, pdf2docx, pyHanko, cryptography. |
| Worker `office` | `worker-office` | profile `office` (default) | LibreOffice headless; WeasyPrint (R2). |
| Worker `ocr` | `worker-ocr` | profile `ocr` (default) | OCRmyPDF, Tesseract (eng, rus, deu, fra, spa, ita), unpaper, OpenCV. |
| Worker `ebook` | `worker-ebook` | profile `ebook` (default) | Calibre `ebook-convert`, Pandoc, djvulibre, libarchive (CBR), img2pdf. |
| Worker `ml` | `worker-ml` | profile `ml` (R2) | ONNX Runtime CPU models, Docling, upscaling, background removal. |
| AI (deferred) | `ollama` + `worker-ai` | profile `ai` (not on the roadmap) | Local LLM server (on its own `ai` network only) and the worker whose runner relays step requests to it over a Unix socket ([ADR-0010](adr/0010-ai-worker-group.md)). |
| Data volume | named volume `filex-data` at `/data` | yes | Job inputs, step outputs, results. Shared by API and workers. |
| Sign-in server (external, optional) | a self-hosted Supabase-compatible server ([ADR-0009](adr/0009-keel-auth.md)) | n/a | Users and sessions. The browser signs in there; the API verifies tokens there. Holds no files. Not used in local mode. |

There is no Chromium / `html` group: HTML and Markdown to PDF use WeasyPrint; URL to PDF waits
for allow-listed egress. Each tool's engine is chosen from engine research; a tool's group
follows from its engine. All images are targets of one
multi-stage `docker/Dockerfile` (`base`, `api`, `worker-core`, then one stage per group). Every
image contains every tool *declaration*; only the engines differ.

### How they talk

```
Browser ──sign-in (supabase-js)──────────────────────────────► sign-in server (optional)
   │                                                                        ▲
   │ 127.0.0.1:8080, or HTTPS proxy / LAN bind          GET /auth/v1/user  │
   ▼                                                                        │
  API (Bearer: sign-in token or fxt_ token) ───────────────────────────────┘
   │  XADD q:<group>, HSET job:<id>        ┌──────────── Redis (ACLs) ◄────────┐
   ├──────────────────────────────────────►│                                   │ XREADGROUP q:<group>
   │ stream upload / download              └───────────────────────────────────┤ next step: XADD
   ▼                                                                           │
 /data volume ◄──────────── read inputs, write outputs ───────────── workers (per group, no network)
```

- **Browser → sign-in server (optional):** email + password sign-in, token refresh, sign-out.
  The only third-party request the browser makes; CSP `connect-src` names it.
- **Browser and scripts → API:** HTTPS/JSON + multipart, `Authorization: Bearer` only. No
  cookies, no WebSockets or SSE: clients poll `GET /jobs/{id}` (`ETag` / `304`).
- **API → sign-in server:** `GET /auth/v1/user` per token, cached up to 60 s, fail closed.
- **API → Redis:** creates the job hash, `own:<id>`, secrets under `sec:*`, and adds step 0 to
  `q:<group of step 0>`.
- **Worker ↔ Redis:** a worker reads its group stream, takes the heavy-step slot if its group is
  heavy, reports progress into the job hash, and on success enqueues the next step on that
  step's group stream. No central orchestrator.
- **API and workers ↔ /data:** files never travel through Redis; Redis carries ids and indexes
  only, and every path is derived from them.
- **Workers → network:** none. Workers sit only on `internal` (`internal: true`), and each step
  child additionally has the same seccomp filter, denying network sockets, in every group. In the
  deferred AI design, `ai` steps reach `ollama` only through an `AF_UNIX` relay in their runner, which forwards three
  Ollama endpoints with size, token and time caps ([ADR-0010](adr/0010-ai-worker-group.md)).

## 3. Storage and temp-file lifecycle

```
/data/jobs/<job_id>/
  in/<n>.<ext>        uploaded files (stored name from the sniffed format), written while streaming
  in/aux/<slot>.<ext> auxiliary files (e.g. the .p12 certificate; sensitive ones deleted after their step)
  steps/<n>/          output of step n (input of step n+1); cleared before a step (re)runs
  out/<index>.<ext>   final outputs, linked by the runner after lstat checks
/data/tmp/<step-id>/  scratch dir for one running step: a hard link of its input, engine HOME/config; deleted when the step ends
```

- Uploads are streamed to disk (`TMPDIR=/data/tmp`), never held in RAM, with caps
  `FILEX_MAX_UPLOAD_MB=1024` per job, `FILEX_MAX_FILE_MB` per file, `FILEX_MAX_FILES=50`, and
  `413` the moment a cap is crossed. The API refuses uploads with `507` when free space would
  drop below `FILEX_MIN_FREE_MB` (default 4096).
- Inputs are sniffed by magic bytes with a small FileX signature table (no libmagic, no engine in
  the API) and must match a declared input format of the tool.
- When a job reaches a terminal state, `in/` and `steps/` are deleted at once; `out/` is kept
  until `expiresAt`, **24 h after finishing** (`FILEX_RETENTION_MIN=1440`, capped at 1440).
- `DELETE /jobs/{id}` cancels and deletes immediately.
- **Janitor** (API background task, every 60 s and at startup): deletes any `/data/jobs/<id>`
  whose Redis hash is gone, and any `/data/tmp/*` older than the longest step timeout.
- No database and **no result cache** (decision, for privacy). Redis keys and who may write them:
  [ADR-0008](adr/0008-storage-and-retention.md) amendment.

## 4. Job model

A **job** is one user request. It owns input files and an ordered list of **steps**. Each step
is one tool run in one worker group. A single-tool job has one step; a conversion through
intermediate formats, or a tool that plans several steps (`djvu.to-pdf` with OCR), has several;
a future pipeline is the same structure with user-chosen steps.

```jsonc
// GET /api/v1/jobs/{jobId}
{
  "id": "j_3q2-7wX9kPb4Zr0tLmN8aQ",        // "j_" + base64url(16 random bytes): 128 bits, not a ULID
  "tool": "convert",
  "status": "RUNNING",                     // QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED
  "progress": 0.62,                        // 0..1 over all steps
  "createdAt": "2026-10-10T12:00:00Z",
  "finishedAt": null,
  "expiresAt": "2026-10-11T12:00:00Z",     // queued/running: hard cap 24 h; finished: +24 h retention
  "inputs":  [{ "name": "thesis.md", "format": "md", "size": 48211 }],
  "steps": [
    { "index": 0, "tool": "markup.convert", "group": "ebook", "status": "SUCCEEDED", "progress": 1 },
    { "index": 1, "tool": "ebook.convert",  "group": "ebook", "status": "RUNNING", "progress": 0.24,
      "message": "rendering chapter 3" }
  ],
  "warnings": [],                           // [{ "code": "LOSSY_STEP", "message": "...", "step": 1 }]
  "outputs": [],                            // on SUCCEEDED: [{ "index", "name", "format", "size", "role": "RESULT" | "REPORT" }]
  "error": null                             // { "code": "ENGINE_TIMEOUT", "message": "...", "step": 1 }
}
```

Secret options never appear here, nor in the job hash ([ADR-0003](adr/0003-tool-plugin-model.md) amendment).

Status rules ([lifecycle diagram](diagrams/job-lifecycle.html)):

- `QUEUED` → `RUNNING` when step 0's child process starts. A heavy step first waits for the
  heavy-step slot; the job stays `QUEUED` with the message "waiting for a free slot". Between
  steps the job stays `RUNNING`.
- A step that exits cleanly hands off to the next step's group, or, if it was the last, the
  runner checks and links its outputs into `out/` and sets `SUCCEEDED`.
- A step that fails (engine error, invalid file, timeout, memory or file-size limit) sets the job
  `FAILED` with a machine-readable `error.code`. Engine stderr is logged at debug level, never
  returned.
- If a worker container dies mid-step, another worker of that group reclaims the stream entry
  after the step timeout plus grace. A step is delivered at most twice; the second loss fails the
  job with `WORKER_LOST`. A redelivered step whose secrets were already consumed fails with
  `SECRET_UNAVAILABLE`.
- `CANCELLED` is set by `DELETE /jobs/{id}`; a running step's child is killed within about a
  second, and the runner's sweep kills anything that escaped its process group.
- Every terminal job expires (`expiresAt`), after which `GET` returns `404`.

**Delivery is at-least-once**, so a step writes only into its own `steps/<n>/`, cleared before
it starts. Job creation is not idempotent: a retried `POST` creates a second job (harmless, it
expires); an `Idempotency-Key` header is deferred.

**Batches:** a per-file tool (compress) given 10 files runs once over all 10 inside one step
and produces 10 outputs; a many-to-one tool (merge) takes them together. `GET /jobs/{id}/result`
returns the single `RESULT` output or a zip of all `RESULT` outputs; each output, reports
included, is also at `/outputs/{n}`.

**Bounded concurrency and memory** ([ADR-0002](adr/0002-worker-groups-and-compose-profiles.md) amendment):

- Each worker runs `FILEX_CONCURRENCY` steps at once (default 1) under its own `mem_limit`.
- **Heavy-step slot:** every group except `core` runs heavy steps. A heavy step takes a Redis
  lease `slot:heavy:<i>` before its child starts (`FILEX_HEAVY_SLOTS`, default 1, `0` = off), so
  LibreOffice, OCR and Calibre never peak together on a default install.
- Worst case with default profiles `office,ocr,ebook`: about 3.1 GB. The `low-memory` preset
  (lower `FILEX_MEM_*` values and pixel/page caps) brings it to about 2.4 GB for 4 GB boxes, or
  about 1.1 GB core-only for 2 GB. A big box (16 GB or more) can set `FILEX_HEAVY_SLOTS=0`.
- The API refuses new jobs with `429 QUEUE_FULL` when a group's stream holds `FILEX_MAX_QUEUED`
  (default 20) waiting steps.

## 5. API surface (v1)

Prefix `/api/v1`. JSON uses camelCase; enum values UPPER_SNAKE. One error shape everywhere:

```json
{ "error": { "code": "INVALID_INPUT_FORMAT", "message": "thesis.pages is not a supported input for pdf.compress", "details": {} } }
```

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | `{"status":"ok"}` only, for Docker health checks |
| `GET /config` | none | Runtime UI config: `authMode`, sign-in server URL and anon key, limits, version ([ADR-0009](adr/0009-keel-auth.md)) |
| `GET /status` | yes | Online worker groups, engine versions, isolation mode (review L2) |
| `DELETE /session` | yes | Evicts the caller's token from the auth cache before the UI signs out |
| `GET /tools`, `GET /tools/{toolId}` | yes | Catalog: options JSON Schema, formats, auxiliary slots, `available`, profile hint |
| `GET /formats` | yes | Known formats (id, extensions, MIME) |
| `GET /convert/plan?from=&to=` | yes | Route preview with lossy hops, or `422 NO_ROUTE` naming the profile that would create one |
| `POST /tools/{toolId}/jobs` | yes | Multipart: `files[]`, one part per auxiliary slot (e.g. `certificate`), `options` (JSON part). `202` + job. The part name `inputs` is reserved for job outputs as inputs (R2). |
| `GET /jobs` | yes | **The caller's own jobs**, newest first (`limit`, `before` cursor) |
| `GET /jobs/{jobId}` | yes, owner | Status (poll; `ETag` / `304`) |
| `GET /jobs/{jobId}/result`, `GET /jobs/{jobId}/outputs/{index}` | yes, owner | Download with the Bearer header (scripts); `Range` supported |
| `POST /jobs/{jobId}/result/link`, `POST /jobs/{jobId}/outputs/{index}/link` | yes, owner | `{url, expiresAt}`: a signed download link valid 5 minutes (browser downloads) |
| `GET /dl/{jobId}/{target}?exp=&sig=` | signature | Streams the output to the browser; `Range` supported |
| `DELETE /jobs/{jobId}` | yes, owner | Cancel + delete now. Idempotent `204`. |
| `GET /openapi.json` | none | Generated contract |

Notable status codes: `401 UNAUTHENTICATED`, `403 USER_NOT_ALLOWED` (valid signed-in user not on the
allowlist), `404 JOB_NOT_FOUND` (unknown, expired **or not yours**), `409 TOOL_UNAVAILABLE` (names
the profile to enable), `413` upload too large, `422` invalid options / input format / no route /
`NOT_SUPPORTED`, `429 QUEUE_FULL` / `TOO_MANY_REQUESTS`, `503 AUTH_UNAVAILABLE` (sign-in server unreachable),
`507 INSUFFICIENT_STORAGE` disk nearly full.

Every download response is an attachment: `Content-Disposition: attachment` with a sanitised
name, `Content-Type` from FileX's format table (HTML, SVG, XML as `application/octet-stream`),
`X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`,
`Cache-Control: no-store`, on `/result`, `/outputs/{n}` and `/dl/...` alike. Outputs are never
rendered on the FileX origin: previews are PDF.js renders or worker-rendered PNG/JPEG in `<img>`,
never an `iframe`, `object` or `embed`. LLM answers (R2) are `text/plain` (review C2, 5.2).

The contract is generated from the tool registry, committed as `docs/api/openapi.json`, and CI
fails if it differs from what the code generates. The UI's TypeScript types are generated from
it ([ADR-0004](adr/0004-openapi-generated-from-registry.md)).

## 6. Tool plugin model

One tool = one Python module under `filex/tools/<category>/` + one test file. The declaration
is the single source of truth for the API route, the OpenAPI schema, the UI catalog entry and
form, the generated docs page, and which worker group runs it ([ADR-0003](adr/0003-tool-plugin-model.md)).

```python
# filex/tools/optimize/pdf_compress.py
from typing import Literal
from pydantic import BaseModel, Field
from filex.registry import tool, Files, Limits, StepContext


class Options(BaseModel):
    level: Literal["LOW", "MEDIUM", "HIGH"] = Field("MEDIUM", title="Compression level")
    target_mb: float | None = Field(None, title="Target size (MB)", gt=0)


@tool(
    id="pdf.compress",
    title="Compress PDF",
    summary="Shrink a PDF by recompressing images and removing unused objects.",
    category="optimize",  # catalog grouping in the UI and docs
    group="core",  # worker group = which image runs it
    inputs=["pdf"],  # format ids from filex/formats.py
    outputs=["pdf"],
    files=Files(min=1, max=50, each=True),  # each=True: run once per input file
    options=Options,  # JSON Schema for API, UI form and docs
    engines=["ghostscript", "qpdf"],  # probed at worker start, with minimum versions
    limits=Limits(timeout_s=300, memory_mb=1024),
    examples=[{"input": "corpus/pdf/scanned-10p.pdf", "options": {"level": "HIGH"}}],
)
def run(ctx: StepContext, src: Path, opts: Options) -> list[Path]:
    import pikepdf  # engine imports stay inside run(): the API image has no engines

    out = ctx.out_dir / "0.pdf"
    ctx.progress(0.1, "recompressing images")
    ...
    if opts.target_mb and out.stat().st_size > opts.target_mb * 1e6:
        ctx.warn("TARGET_NOT_REACHED", f"Best we could do: {out.stat().st_size / 1e6:.1f} MB.")
    return [out]
```

A tool with an auxiliary file and a secret option:

```python
class SignOptions(BaseModel):
    passphrase: SecretStr = Secret(
        title="Certificate passphrase"
    )  # writeOnly, never stored in the job hash
    reason: str = Field("", title="Reason", max_length=200)


@tool(
    id="pdf.sign",
    title="Sign PDF",
    category="security",
    group="core",
    inputs=["pdf"],
    outputs=["pdf"],
    options=SignOptions,
    files=Files(
        min=1,
        max=20,
        each=True,
        aux={
            "certificate": Aux(
                formats=["p12"], required=True, sensitive=True, title="Certificate (.p12)"
            )
        },
    ),
    engines=["pyhanko"],
    limits=Limits(timeout_s=60, memory_mb=512),
)
def run(ctx, src, opts):
    p12 = ctx.aux["certificate"]  # path in the scratch dir; deleted by the runner after the step
    ...
```

A **converter** declares conversion edges instead of a fixed output, and receives the requested
target in `ctx.target`. Converters cannot declare auxiliary slots or secret options, so any
route can run on defaults:

```python
@tool(
    id="office.to-pdf",
    title="Office to PDF",
    category="convert",
    group="office",
    converts=[Edge("docx", "pdf"), Edge("xlsx", "pdf"), Edge("pptx", "pdf"), Edge("odt", "pdf")],
    files=Files(min=1, max=20, each=True),
    limits=Limits(timeout_s=180, memory_mb=1536),
)
def run(ctx, src, opts): ...
```

Rules, each enforced by a generic test over every registered tool:

1. Tool modules import only the stdlib, pydantic and `filex` at module level.
2. `id` is unique and stable (it is in URLs). `group`, `category` and every format id exist.
3. The options schema uses only what the UI form renderer supports: enum, bool, int / number
   with min/max, short string, page-range string, password (secret), one level of nesting;
   auxiliary files come from `Files(aux=...)`. Every field has a default or is required, and a
   `title`. Every `SecretStr` field is `writeOnly`.
4. Each `examples` entry validates and runs as a smoke test inside that group's image in CI,
   under the seccomp filter.

**Discovery** is a package scan of `filex.tools` at startup. Third-party plugins are deferred.

**What the registry generates:**

| Consumer | Generated from the declaration |
|---|---|
| API | One `POST /tools/{id}/jobs` route per tool: options validated by the pydantic model, inputs and aux slots checked against declared formats, secrets split out |
| OpenAPI | Per-tool request schema, formats enum, catalog schema, warning codes |
| UI | `GET /tools`: categories, titles, summaries, accepted formats, aux slots, options JSON Schema, `available` |
| Docs | `scripts/gen_tool_docs.py` writes `docs/tools.md` |
| Workers | Group routing, heavy-step slot (every group but `core`), limits, engine probes |

**Availability:** each worker writes a heartbeat `worker:<group>:<id>` (TTL 30 s) listing the
tools whose engine probes passed (including minimum versions). The API marks a tool
`available: false` when no live worker of its group lists it, and refuses jobs for it with
`409 TOOL_UNAVAILABLE` naming the compose profile to enable.

**Adding a tool:** write the module, add a corpus sample if none fits, write
`tests/tools/test_<id>.py`, regenerate `docs/api/openapi.json` and `docs/tools.md`. A new engine
goes into its group's Dockerfile stage and gets a `security` review against ADR-0007 Appendix A;
a new heavy engine *family* is a new group and a new ADR.

## 7. Conversion graph

Formats are nodes; every `Edge(src, dst)` declared by a converter is a directed edge. `convert`
is a built-in planner tool: given an input format and a target, it finds a route and turns it
into the job's steps ([ADR-0005](adr/0005-conversion-routing.md)).

- **Algorithm:** Dijkstra over edges whose tool is currently available. Cost `1` per step, `+4`
  if lossy, plus an optional per-edge `cost`. Ties break on fewer steps, then tool id. Max 4 steps.
- **Example with the default profiles:** Pandoc (`ebook`) declares `md → epub`, Calibre (`ebook`)
  declares `epub → mobi` and `pdf → epub (lossy)`, LibreOffice (`office`) declares `pptx → pdf`.
  MD → MOBI is `md → epub → mobi` (cost 2); PPTX → EPUB is `pptx → pdf → epub` (cost 6, with a
  `LOSSY_STEP` warning). With `ebook` off, MD → MOBI answers `422 NO_ROUTE` naming the profile.
- **Static multi-step tools:** `djvu.to-pdf` with OCR plans `djvu → pdf` (`ebook`) then `ocr.pdf`
  in skip-text mode (`ocr`) at creation time; nothing appends steps at run time.
- **Preview:** `GET /convert/plan` returns the steps before uploading.
- **Catalog tiles** ("Word to PDF", "EPUB to PDF") are generated from reachable pairs.
- Intermediate steps use default options; per-hop options are a pipeline feature (deferred).

## 8. Security and sandboxing (summary)

Details in [ADR-0007](adr/0007-sandboxing-untrusted-files.md) (with the configuration baseline as
Appendix A), [ADR-0009](adr/0009-keel-auth.md) and [ADR-0011](adr/0011-public-local-mode.md).
Changes to uploads, engines, the runner, auth, secrets or crypto get a security review.

- **Exposure:** the API binds `127.0.0.1` by default; other devices reach it through an HTTPS
  reverse proxy or a LAN/VPN `FILEX_BIND`; `TrustedHostMiddleware`; no other container publishes
  a port.
- **Auth:** Bearer only (sign-in token verified at the sign-in server, or a hashed `fxt_` static
  token); signed-in users must be on the allowlist (`FILEX_ALLOWED_USER_IDS` or
  `app_metadata.filex`); fail closed; refuses to start with no auth unless `FILEX_AUTH=none`,
  which is accepted only on a loopback bind (local mode). Every job route checks the job's owner
  and answers `404` for other people's jobs.
- **Browser:** strict CSP on the app shell (sign-in server URL must be `https`; only its origin in
  `connect-src`; HSTS once HTTPS-only), no CORS, no cookies (no CSRF), outputs only as sandboxed
  attachments, React rules (`react/no-danger`, no `innerHTML`, no `href`/`src` from job data).
  supabase-js `localStorage` token storage is accepted on purpose for R1.
- **Containers:** the `x-hardening` baseline on every service: non-root, read-only root FS,
  `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `mem_limit` with no extra swap, small
  `tmpfs`, log rotation. Workers are only on `internal`: no egress.
- **Per step:** a fresh child per step with timeout and rlimits, an input-only scratch dir, an
  allowlisted environment, and a **seccomp filter** that denies network sockets and
  process-inspection syscalls. The runner is a subreaper and **kills any escaped process**.
  Redis uses **ACL users** (`api`, `worker`); ownership keys are api-only. Paths come only from
  job id + step index.
- **Residual risk (R1):** a compromised step can read and write `/data` while it runs. It
  cannot persist, reach the network, or touch the queue. Landlock (later) narrows it to its own
  job.
- **Secrets:** write-only options go through `sec:*` and the child's stdin, never the job hash,
  argv, environment or logs. Signing uses a per-job uploaded `.p12`; FileX holds no keys.
- **Engines:** per-engine flags, format allowlists, minimum versions and weekly rebuilds
  (ADR-0007 Appendix A); archive, pixel and page limits before parsing.

## 9. Compose shape (illustrative; `compose.yaml` is the source of truth)

Matches the scaffolded `compose.yaml`: services build targets of `docker/Dockerfile` and share
its `x-hardening` block (ADR-0007 Appendix A). Shown abbreviated; groups not built yet are added
the same way.

```yaml
x-hardening: &hardening { user: "10001:10001", read_only: true, cap_drop: [ALL], security_opt: ["no-new-privileges:true"],
                          init: true, ipc: private, pids_limit: 256, ulimits: { nofile: 1024, core: 0 },
                          tmpfs: ["/tmp:size=64m,mode=1777,nosuid,nodev,noexec"],
                          logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }, restart: unless-stopped }
x-worker: &worker { <<: *hardening, networks: [internal], volumes: [filex-data:/data] }

services:
  api:           { <<: *hardening, build: { context: ., dockerfile: docker/Dockerfile, target: api },
                   ports: ["${FILEX_BIND:-127.0.0.1}:8080:8080"], networks: [default, internal], volumes: [filex-data:/data],
                   mem_limit: 256m }
  redis:         { <<: *hardening, image: valkey/valkey:8.1-alpine, networks: [internal], mem_limit: 64m,
                   command: ["redis-server", "--aclfile", "/run/secrets/redis-acl", "--save", "", "--appendonly", "no"] }
  worker-core:   { <<: *worker, build: { target: worker-core },   mem_limit: "${FILEX_MEM_CORE:-1g}" }
  worker-office: { <<: *worker, build: { target: worker-office }, mem_limit: "${FILEX_MEM_OFFICE:-1536m}", profiles: [office, all] }
  worker-ocr:    { <<: *worker, build: { target: worker-ocr },    mem_limit: "${FILEX_MEM_OCR:-1536m}",    profiles: [ocr, all] }
  worker-ebook:  { <<: *worker, build: { target: worker-ebook },  mem_limit: "${FILEX_MEM_EBOOK:-1g}",     profiles: [ebook, all] }
  # R2: worker-ml (profile ml). Deferred (ADR-0010): ollama on network ai only + worker-ai on
  #     internal and ai (profile ai); ollama-pull on default network (profile ai-pull).
networks:
  internal: { internal: true, enable_ipv6: false }
  # ai: { internal: true }   # deferred (ADR-0010)
volumes:
  filex-data: {}
```

`memswap_limit` equals `mem_limit` on every service. Each worker gets `FILEX_CONCURRENCY` and
`FILEX_HEAVY_SLOTS` from `.env`. The shipped `.env.example` sets
`COMPOSE_PROFILES=office,ocr,ebook`, `FILEX_RETENTION_MIN=1440`, `FILEX_MAX_UPLOAD_MB=1024`,
`FILEX_HEAVY_SLOTS=1`, and carries the `low-memory` preset as a commented block. Redis runs with
the ACL users `api` and `worker` from `docker/redis/users.acl` (default user off); their
passwords are Docker secret files made by `scripts/make_secrets.py`, never `.env` values
(ADR-0007 item 3, task 54).

## 10. Deliberately deferred

| Deferred | Why not now | Room left for it |
|---|---|---|
| Landlock per-step filesystem view | Needs kernel support, which many home-server kernels lack; R1 baseline is enough for invited users | Runner probes at start; ADR-0007 "Later" |
| Allow-listed egress (signing timestamps, external Ollama, URL to PDF, Chromium) | No R1 feature needs it; reopening the network needs care | ADR-0007 "Later"; its own ADR before R2 features that need it |
| Pipelines / chaining, per-hop options, job output as input | R2/R3 | Jobs are step lists; the `inputs` part name is reserved (ADR-0003) |
| `ml` group (R2); `ai` group (deferred) | Not needed for R1 | ADR-0002 amendment, ADR-0010 |
| Server-held signing keys (`signer` service) | R1 holds no keys | Review H8 fix 5 |
| Backend-for-frontend (refresh token in `HttpOnly` cookie) | Only matters outside a private network | ADR-0009 alternatives |
| Per-owner job caps and rate limits (review L1) | Few invited users | `rl:*` keys already exist for auth |
| Watch folders, webhooks | R3 | Separate service calling the REST API |
| SSE / WebSocket progress | Polling is enough | Progress lives in one hash |
| `Idempotency-Key` on job creation | Duplicate job is harmless and expires | Additive header |
| Third-party plugins, hot-reload | Tools ship with the image | Discovery is one function |
| Multi-host / object storage | Single host | Paths derived from ids, not hosts |
| Persistence of queued jobs across Redis restart | Jobs are minutes long | Turn on AOF |
| Metrics / tracing | Logs + `/status` first | JSON logs carry job id and step |

## 11. Open questions

None after 2026-10-10. Remaining product items are in the
[spec's open items](spec.md#16-open-items).
