# Spec: FileX

Status: draft, updated 2026-10-10 with the maintainer's answers to O1, O3, O4 and O7, the ADRs
that close open items A1-A10, and the completed security review. Updated 2026-10-13 for the public
local mode ([ADR-0011](adr/0011-public-local-mode.md)).
Implementation plan: [plan.md](plan.md).

Built from: competitor and engine research, [architecture.md](architecture.md), [adr/](adr/), and the
maintainer decisions dated 2026-10-10 (listed in [section 2](#2-decisions-this-spec-is-built-on)).
The "required before release 1" gates of the security architecture review (review notes are
not published; each gate is restated where it applies) are R1 requirements
([5.2](#52-security-basics), [7](#7-api-surface-summary), [10](#10-acceptance-criteria-per-release));
[plan.md](plan.md#security-review-gates-to-tasks) maps each gate to the task that covers it.

---

## 1. Objective

FileX is a self-hosted, all-in-one document toolkit that runs with one `docker compose up` on a
home server or a personal computer. It does what people today need four apps for (Stirling-PDF for PDF tools, ConvertX for
conversion, Calibre for ebooks, Paperless-ngx for OCR) behind **one web UI, one REST API and one
job queue**, using free and open-source engines only. Nothing leaves the box: no cloud calls, no
CDN, no telemetry, no usage caps.

**Why it can win** (from the competitor research's gaps and opportunities).
FileX does not try to beat Stirling-PDF on PDF tool count. It wins on:

| # | Differentiator | First release |
|---|---|---|
| G1 | One app across PDF, Office, images and ebooks, with automatic multi-step conversion routing | R1 (PDF, Office, ebooks), R2 (images) |
| G5 | Compression you can control: target size ("under 2 MB"), before/after preview, size report | R1 |
| G7 | Real digital signing (pyHanko PAdES) with your own certificate, plus a built-in self-signed certificate maker | R1 (PAdES B-B), R2 (timestamp/LTV, validation) |
| G4 | Real scan cleanup, not just OCR: deskew, clean, blank-page removal (R1); dewarp, background whitening, MRC (R2) | R1 basic, R2 full |
| G2 | Private, local AI: summarize, chat with PDF, layout-preserving translation, through local Ollama | Deferred (designed in [ADR-0010](adr/0010-ai-worker-group.md), not on the roadmap) |
| G3 | Better PDF-to-Office: fast mode (R1) plus layout-AI mode with Docling (R2) | R1 fast, R2 accurate |
| G9 | Legacy and comic formats: DJVU (with OCR), CBZ/CBR to PDF and PDF to CBZ, EPUB/MOBI/AZW3/FB2 | R1 (core set), R2 (KEPUB, LIT, PDB, CB7, comic to EPUB) |
| G6 | Redaction you can verify (re-extracts text to prove nothing is left) and sanitization | R2 |
| G10 | Compare beyond PDF (DOCX/XLSX) | R2 (PDF), R3 (Office) |
| G8 | Automation across every tool: recipes, watch folders, webhooks | R3 |

**Success looks like:** a person and a few invited people use FileX instead of iLovePDF,
Smallpdf, Stirling-PDF and Calibre for their everyday document jobs, on their own home server,
and the same stack still works on a 2-4 GB box with heavy profiles switched off.

## 2. Decisions this spec is built on

| Decision | Effect on the design |
|---|---|
| Target a home server, x86-64 Docker first. ARM64, Windows and macOS later. | Multi-arch-ready Dockerfiles, but CI builds and tests `linux/amd64` only until R3. |
| Used on one computer (local mode, [ADR-0011](adr/0011-public-local-mode.md)) or shared on a private network or VPN with a few trusted people. | No public-internet hardening beyond the basics in 5.2. |
| Must run on weak hardware (2-4 GB RAM). Heavy engines are optional compose profiles. | Section 5.1 sets the budget. Default `.env.example` enables profiles `office,ocr,ebook`. |
| Web UI + REST API. | One API used by both. |
| **Optional sign-in through a Supabase-compatible server.** Supersedes "optional shared password". The public build defaults to local mode instead (ADR-0011). | Section 7.2. [ADR-0009](adr/0009-keel-auth.md) supersedes ADR-0006. |
| Optional static API token for scripts. | Sign-in access tokens expire, so automation uses `fxt_` tokens whose SHA-256 hashes are listed in `FILEX_API_TOKENS` (section 7.2). |
| Results kept **24 hours**, then auto-deleted. | `FILEX_RETENTION_MIN=1440`, capped at 1440 ([ADR-0008](adr/0008-storage-and-retention.md)). |
| Max upload **1 GB per job**, streamed to disk, configurable. | `FILEX_MAX_UPLOAD_MB=1024` ([ADR-0008](adr/0008-storage-and-retention.md)). |
| No result caching by content hash for now. | No cache layer. Every job runs fresh. |
| Default profiles in `.env.example`: `office,ocr,ebook` (O7, updated 2026-10-10). | `COMPOSE_PROFILES=office,ocr,ebook`. |
| Private local AI designed as an optional profile; deferred (ADR-0010). | Section 8 lists the requirements the architecture must already allow. |
| Match and exceed the competitor set, including ebook conversion. | Section 6 is the full catalog. |
| **R1 scope accepted, with ebooks moved into R1** (O7). | Section 6.11: 31 R1 tools, five of them in the `ebook` group. |
| **OCR languages shipped by default:** `eng`, `rus`, `deu`, `fra`, `spa`, `ita` (O3). | `ukr` and `bel` are documented add-ons through `.env` (section 6.5). |
| **R1 signing is PAdES B-B without a trusted timestamp, and `cert.create` makes self-signed certificates** (O4). | Accepted as in section 6.7; timestamps wait for A6. |
| **No auth configured means no start** (O1). Supersedes "start open with a warning". | FileX refuses to start unless sign-in or `FILEX_API_TOKENS` is configured, or `FILEX_AUTH=none` is set explicitly (section 7.2); `none` is accepted only on a loopback bind (ADR-0011). |
| **Security review C1 accepted.** | API port binds to `127.0.0.1` (or a private network address) by default; a valid sign-in is not enough, the user must also be on FileX's allowlist (sections 5.2, 7.2). |
| **Security review release gates are R1 requirements**, and ADRs 0002, 0003, 0005 and 0007-0010 resolve open items A1-A10. | Sections 5.2, 6, 7, 8, 10 and 16; gate-to-task mapping in [plan.md](plan.md#security-review-gates-to-tasks). |

## 3. Users

| User | Context | Needs |
|---|---|---|
| **Admin** | Runs the server, invites people (sign-in server's admin console or access tokens), picks compose profiles | One-command install and upgrade, low RAM, clear "which profile enables this tool" hints, logs |
| **Invited user** (family, friends, colleagues; non-technical) | Browser on laptop or phone over the home network or a VPN | Find a tool fast, drop files, get the result. No jargon. Works on a phone. |
| **Script user** (the admin's automations) | curl or Python against the REST API | Stable, documented API, a static token, predictable errors |

Everyone is trusted not to attack the server, but uploaded **files** are untrusted (they may come
from anywhere). Each user sees only their own jobs.

## 4. Non-goals

- **Public SaaS.** No billing, quotas, multi-tenant isolation beyond per-user job ownership, or
  public sign-up. Sign-ups are disabled on the sign-in server; the admin invites users.
- **Paid or cloud engines and APIs** of any kind (so no `nutrient-document-processing`, no cloud
  LLMs, no CDN-loaded WASM).
- **A full PDF editor** (editing existing text, freehand annotation) in R1-R3. Revisit later.
- **Requesting e-signatures from other people** (DocuSign-style flows).
- **Video and audio conversion** (ConvertX covers it; out of FileX's document scope).
- **DRM removal** for ebooks.
- **Result caching by content hash** (decision, for privacy).
- **Storing user files in the sign-in server's storage.** Files stay on FileX's local data volume.
- **Multi-host scaling, object storage, Kubernetes.**
- **Native desktop or mobile apps.** The web UI is responsive instead.

## 5. Quality bar

These are targets. Benchmarks confirm or adjust them with baselines from the test corpus in R1
(plan task 43). A target that turns out to be wrong is changed here, in a PR, with the numbers.

### 5.1 Performance on weak hardware

Reference "weak box": x86-64, 2 cores, 4 GB RAM, SSD, Docker limits `--cpus 2 --memory 4g` in CI.
Reference "tiny box": 2 cores, 2 GB RAM, core profile only.

| Metric | Target |
|---|---|
| Idle RAM, always-on set (API + Redis + worker-core) | ≤ 350 MB |
| Idle RAM, each optional worker (engines not preloaded) | ≤ 150 MB |
| Peak RAM with default profiles `office,ocr,ebook` on the weak box | Never OOM-kills the host. The heavy-step slot (`FILEX_HEAVY_SLOTS=1`) keeps LibreOffice, OCR and Calibre from peaking at once; estimated worst case ~3.1 GB with default limits, ~2.4 GB with the `low-memory` preset ([ADR-0002](adr/0002-worker-groups-and-compose-profiles.md) amendment; benchmarks replace the estimates with measurements). |
| Tiny box (2 GB) | Core profile runs every core tool in the R1 catalog with the `low-memory` preset (~1.1 GB worst case). `office`, `ocr` and `ebook` documented as "enable only one, at your own risk" (open item O5). |
| API memory during a 1 GB upload | RSS stays ≤ 200 MB (upload streamed to disk) |
| Job overhead (queue + child process start) | ≤ 0.5 s per step |
| `pdf.merge`, two 10-page text PDFs | ≤ 3 s end to end on the weak box |
| `pdf.compress`, 50-page scanned PDF (~30 MB), MEDIUM | ≤ 60 s, output ≤ 50% of input |
| `office.to-pdf`, 20-page DOCX | ≤ 20 s including LibreOffice cold start |
| `ocr.pdf`, 300 dpi A4 page, `tessdata_fast`, one language | ≤ 8 s per page median |
| UI first load over the home network | ≤ 2 s; initial JS ≤ 250 KB gzipped; PDF.js loaded only on pages that preview |
| Status polling cost | `GET /jobs/{id}` ≤ 20 ms server time, `304` when unchanged |

Rules that make this possible (already in the architecture): heavy engines behind profiles, one
step at a time per worker by default (`FILEX_CONCURRENCY=1`), one heavy step at a time across
`office`, `ocr` and `ebook` (`FILEX_HEAVY_SLOTS=1`; `0` turns it off on big boxes), `mem_limit` per
worker from `FILEX_MEM_CORE`, `FILEX_MEM_OFFICE`, `FILEX_MEM_OCR`, `FILEX_MEM_EBOOK` with
`memswap_limit` equal to it, pixel and page caps (`FILEX_MAX_PIXELS`, `FILEX_MAX_PAGES`), files
never in RAM or Redis, a queue cap that returns `429 QUEUE_FULL`. The `low-memory` preset is a
commented block of these values in `.env.example`, no code path.

### 5.2 Security basics

Applies to every release. Details in [ADR-0007](adr/0007-sandboxing-untrusted-files.md) (engine
and container settings in its Appendix A) and [ADR-0009](adr/0009-keel-auth.md); every PR that
touches uploads, engines, the runner, auth, secrets or crypto gets a security review.

- **Untrusted files:** magic-byte sniffing with FileX's own signature table (extension and client
  `Content-Type` ignored, no engine or libmagic in the API), per-file and per-job size caps counted
  while streaming, file-count cap, form limits (`max_fields=10`, `options` part ≤ 64 KB), stored
  names generated from the sniffed format (`in/<n>.<ext>`), the original name kept for display
  only. Archive-based inputs (DOCX, XLSX, PPTX, ODT, EPUB, CBZ, CBR) pass a pre-check before
  anything unpacks or an engine opens them: entry count ≤ `FILEX_MAX_ARCHIVE_ENTRIES` (5000),
  unpacked size ≤ `FILEX_MAX_UNPACKED_MB` (1024) counted from bytes read, ratio ≤ 200:1, depth 1,
  no absolute, `..`, symlink or device entries; FileX's own unpack loop with generated names.
  Images and pages are checked against `FILEX_MAX_PIXELS` (100 MP) and `FILEX_MAX_PAGES` (2000)
  before decoding. Our own XML parsing uses `defusedxml`.
- **Engines read only their own input (review H2):** each step gets a scratch dir holding a hard
  link of its input and nothing else, with `cwd`, `HOME`, `TMPDIR` and engine config dirs inside
  it; every other reference in a document resolves to "not found". Engine settings per ADR-0007
  Appendix A: Ghostscript `-dSAFER` with reads permitted only for the input and only on `%PDF-`
  files; LibreOffice macros, active content and link updates off with a throwaway profile per
  run; Pandoc `--sandbox`; WeasyPrint (R2) with a fetcher limited to `data:` and the scratch dir;
  Calibre `ebook-convert` only, with an input-format allowlist; DRM-protected ebooks refused;
  ImageMagick not installed. A local-file-inclusion corpus test runs per engine on every engine
  upgrade.
- **Engine patching (review H4):** each engine probe enforces a minimum version (for example
  Ghostscript ≥ 10.03.1, exiftool ≥ 12.24) and marks the tool unavailable below it; images are
  rebuilt weekly, base images pinned by digest, `pip-audit` and `npm audit` run in CI.
- **Network exposure (review C1):** compose publishes the API as
  `"${FILEX_BIND:-127.0.0.1}:8080:8080"`, never `0.0.0.0`. The install guide shows the two ways onto
  a private network: an HTTPS reverse proxy on the host in front of `http://127.0.0.1:8080`, or
  `FILEX_BIND=<LAN or VPN IP>`. `TrustedHostMiddleware` with `FILEX_ALLOWED_HOSTS` blocks
  DNS-rebinding requests.
- **Containers:** the `x-hardening` baseline on every service, `api` and `redis` included: non-root,
  read-only root FS, `cap_drop: ALL`, `no-new-privileges` (Docker's default seccomp kept),
  `pids_limit`, `mem_limit` with no extra swap, small `tmpfs`, log rotation. Workers sit only on
  the `internal` network (`internal: true`, no IPv6): no worker has egress in R1. The bridge
  gateway is checked for reachable host services on the Docker host (review M6).
- **Per step (review H1):** a fresh child per step with timeout and rlimits, an allowlisted
  environment, and a **seccomp filter** that denies `AF_INET`, `AF_INET6`, `AF_NETLINK` and
  `AF_PACKET` sockets and process-inspection syscalls (`ptrace`, `process_vm_*`, `keyctl`, `bpf`,
  `userfaultfd`, `perf_event_open`, `mount`). The same filter applies to every group, `ai`
  included (R2 `ai` steps reach Ollama only through a Unix-socket relay in the runner). The runner is a
  subreaper and, after every step and every 5 s, **kills any process that escaped** its step's
  session. Every path is built from a job id (`^j_[A-Za-z0-9_-]{22}$`) and an integer step or
  output index, never from a string read from Redis. The runner, not the child, `lstat`-checks
  outputs before linking them into `out/`; the API opens them with `O_NOFOLLOW`.
- **Redis:** ACL users `api` and `worker` instead of one password, default user off, ACL file and
  passwords mounted as Docker secret files. The `worker` user cannot write ownership, owner indexes
  or rate-limit keys, and may only `GETDEL` step secrets.
- **Auth:** every `/api/v1` route except `/health`, `/config` and the signed `/dl/...` needs a
  valid sign-in access token **from an allowed user** or a static `fxt_` token (section 7.2), unless
  local mode is on (`FILEX_AUTH=none` on a loopback bind, ADR-0011). Allowed means the user id is
  in `FILEX_ALLOWED_USER_IDS` or `app_metadata.filex` is `"user"` or `"admin"`; never by email or
  `user_metadata`, and anonymous users are rejected (review C1). Fails closed if the sign-in
  server is unreachable. Static tokens are stored as SHA-256 hashes and compared in
  constant time. FileX refuses to start with no auth configured unless `FILEX_AUTH=none` is set
  explicitly (O1). Credentials come only from the `Authorization` header: no cookie, no token in a
  URL, no CORS.
- **Ownership:** every job's owner lives in `own:<jobId>`, which only the API can write. Any job
  route for a job you do not own returns `404` (not `403`, so job ids do not leak); a test walks the
  route table and fails if a `{jobId}` route skips the check. Job ids are
  `"j_" + base64url(16 random bytes)`: 128 random bits, not ULIDs.
- **Secrets in jobs (review H5):** passwords and certificate passphrases are `SecretStr` options,
  `writeOnly` in the schema. The API stores them only in `sec:<jobId>:<step>`, the runner
  `GETDEL`s them just before the step and writes them to the child's stdin; never in the job hash,
  a stream entry, argv, the environment, logs, OpenAPI examples or error messages. An uploaded
  `.p12` exists only for its step. A redelivered step whose secret is gone fails with
  `SECRET_UNAVAILABLE`.
- **Retention:** inputs and intermediates deleted when a job finishes; outputs deleted 24 h after
  finishing (`FILEX_RETENTION_MIN` capped at 1440); a janitor sweeps orphans; `DELETE /jobs/{id}`
  removes everything at once. Deletion is an `unlink`; the install guide asks the admin to exclude
  the data volume from snapshots and backups.
- **Logs:** job id, tool, principal, sizes, durations and error codes only; never filenames,
  option values, tokens or email addresses. Engine stderr only at debug level, truncated to 2 KB,
  never returned to clients.
- **Downloads are never rendered on the FileX origin (review C2):** `Content-Disposition:
  attachment` with a sanitised name, `Content-Type` from FileX's format table (HTML, SVG and XML as
  `application/octet-stream`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy:
  sandbox; default-src 'none'`, `Cache-Control: no-store`. Outputs never go in an `<iframe>`,
  `<object>` or `<embed>`; the UI never uses `dangerouslySetInnerHTML` or `innerHTML`.
- **No third-party requests from the browser** except to the configured sign-in server, if any.
  Strict CSP on the app shell (`default-src 'self'`, `connect-src 'self' <sign-in origin>`,
  `object-src 'none'`, `frame-ancestors 'none'`), `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`, `Strict-Transport-Security` when `FILEX_HSTS=true` (set it once the UI
  is served only over HTTPS); all assets self-hosted (PDF.js, fonts). The sign-in server URL must
  be `https` (the API refuses to start otherwise) and only its origin goes into `connect-src`.
  supabase-js keeps its session in `localStorage`; R1 accepts that on purpose (review C2 fix 5),
  with the output, CSP and React rules above as the real defence.
- **`/health` reveals nothing:** it returns only `{"status":"ok"}`; worker groups, engine versions
  and the isolation mode are on the authenticated `GET /status` (review L2).
- **Dependencies:** pinned versions, image scan and dependency audits in CI.

### 5.3 Accessibility

WCAG 2.2 AA for the whole UI ([design.md](design.md) has the details):

- Everything works with a keyboard. Drag-and-drop always has a "Choose files" button next to it.
  The page-organize grid supports keyboard reordering (select, then move with arrow keys or
  "Move to position").
- Visible focus, logical focus order, focus moved to the result or error when a job ends.
- Job progress and completion announced through an `aria-live="polite"` region; errors through
  `role="alert"`.
- Option forms generated from schemas get a real `<label>` from each field's `title`, help text
  through `aria-describedby`, and error messages tied to the field.
- Contrast ≥ 4.5:1 for text, ≥ 3:1 for UI parts, in light and dark themes.
- `prefers-reduced-motion` respected. Touch targets ≥ 24×24 px (44 px preferred on mobile).
- Before/after previews have a text alternative (sizes, page count, percentage saved).
- axe checks run in Playwright on every page in CI, with zero serious or critical violations.

### 5.4 Reliability

- A broken, encrypted, empty, huge or mislabeled file never crashes a worker in a loop: it ends in
  `FAILED` with a clear `error.code` (delivery capped at 2 per step, per ADR-0001).
- Every tool has tests on real sample files from the test corpus, including at least one bad file.
- Restarting any container never leaves files behind longer than one janitor pass.

## 6. Tool catalog

**Columns.** *Id* is public API and never renamed. *In → Out* uses format ids. *Files*: `1` one
file; `N each` runs once per file (batch); `N→1` combines files. *Group* is the worker group and
compose profile (`core` is always on). *Rel* is the target release.

Groups are the six of the [ADR-0002 amendment](adr/0002-worker-groups-and-compose-profiles.md):
`core`, `office`, `ocr`, `ebook`, `ml` (R2) and `ai` (R2). There is no Chromium / `html` group.
Pandoc lives only in `ebook`; WeasyPrint (R2) lives in `office`. A tool whose route crosses
groups is planned as several steps at job creation ([ADR-0005](adr/0005-conversion-routing.md)
amendment).

Format id shorthands: **office-doc** = docx, doc, odt, rtf. **office-sheet** = xlsx, xls, ods, csv.
**office-slides** = pptx, ppt, odp. **img** = jpg, png, webp, tiff, heic, gif, bmp (avif, jxl in R2).
**ebook** = epub, mobi, azw3, fb2, kepub, lit, pdb, rtf, txt, htmlz. **comic** = cbz, cbr, cb7.

### 6.1 Organize

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `pdf.merge` | Merge PDFs (optional bookmarks per file) | pdf → pdf | N→1 | core | pikepdf | **R1** |
| `pdf.split` | Split by ranges, every N pages, or by top-level bookmarks | pdf → pdf (zip) | 1 | core | pikepdf | **R1** |
| `pdf.rotate` | Rotate all or selected pages | pdf → pdf | N each | core | pikepdf | **R1** |
| `pdf.organize` | Visual reorder, delete, duplicate, rotate pages (page-grid widget) | pdf → pdf | 1 | core | pikepdf | **R1** |
| `pdf.extract-pages` | Keep only selected pages | pdf → pdf | N each | core | pikepdf | **R1** |
| `pdf.remove-pages` | Delete selected pages | pdf → pdf | N each | core | pikepdf | **R1** |
| `pdf.split-size` | Split into parts under a size | pdf → pdf (zip) | 1 | core | pikepdf | R2 |
| `pdf.alternate-mix` | Interleave front and back scans (duplex), optional reverse | pdf → pdf | N→1 (2) | core | pikepdf | R2 |
| `pdf.crop` | Crop margins (manual box or auto-trim whitespace) | pdf → pdf | N each | core | pikepdf, pypdfium2 | R2 |
| `pdf.resize-pages` | Fit pages to A4/Letter/custom | pdf → pdf | N each | core | pikepdf | R2 |
| `pdf.nup` | N pages per sheet (2/4/6/9) | pdf → pdf | N each | core | pikepdf | R2 |
| `pdf.booklet` | Imposition for booklet printing | pdf → pdf | 1 | core | pikepdf | R2 |
| `pdf.split-halves` | Split scanned two-page spreads into single pages | pdf → pdf | N each | core | pikepdf | R2 |
| `pdf.remove-blank-pages` | Detect and drop blank pages (threshold) | pdf → pdf | N each | core | pypdfium2 | R2 |
| `pdf.split-separator` | Split on blank or QR/barcode separator pages | pdf → pdf (zip) | 1 | ocr | OpenCV, zbar | R3 |
| `pdf.bookmarks` | Add, edit, import/export bookmarks | pdf → pdf | 1 | core | pikepdf | R3 |
| `pdf.attachments` | Add, list, extract, remove embedded files | pdf → pdf, zip | 1 | core | pikepdf | R3 |

### 6.2 Optimize

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `pdf.compress` | Presets (LOW/MEDIUM/HIGH/EXTREME), **target size** mode, grayscale, DPI cap; size report and before/after preview | pdf → pdf | N each | core | Ghostscript, pikepdf | **R1** |
| `pdf.repair` | Rebuild broken PDFs (qpdf, then Ghostscript re-write fallback) | pdf → pdf | N each | core | qpdf, Ghostscript | **R1** |
| `pdf.to-pdfa` | Convert to PDF/A-1b, 2b, 3b | pdf → pdf | N each | core | Ghostscript | **R1** |
| `pdf.linearize` | Fast web view | pdf → pdf | N each | core | qpdf | R2 |
| `pdf.flatten` | Flatten form fields and annotations | pdf → pdf | N each | core | pikepdf, pypdfium2 | R2 |
| `pdf.grayscale` | Convert to grayscale | pdf → pdf | N each | core | Ghostscript | R2 |
| `pdf.rasterize` | Turn pages into images (keeps PDF) | pdf → pdf | N each | core | pypdfium2, img2pdf | R2 |
| `pdf.pdfa-validate` | Validate PDF/A (and PDF/UA) with a report | pdf → json, html | N each | ocr | veraPDF | R2 |
| `pdf.info` | Page count, metadata, fonts, encryption, PDF/A claim, signatures | pdf → json | 1 | core | pikepdf | R2 |

### 6.3 Convert

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `convert` | **Generic converter**: picks a route over the format graph, may span several groups; shows the route and lossy warnings before upload | any → any reachable | N each | (planner) | ADR-0005 | **R1** |
| `image.to-pdf` | Images to PDF, one PDF per image or combined; page size, fit, margins, orientation | img → pdf | N each or N→1 | core | img2pdf, Pillow (+pillow-heif) | **R1** |
| `pdf.to-image` | Pages to PNG/JPEG/WebP/TIFF at a chosen DPI, page selection | pdf → png, jpg, webp, tiff (zip) | N each | core | pypdfium2 | **R1** |
| `pdf.to-text` | Extract text, plain or layout-preserving | pdf → txt | N each | core | pypdfium2 / Poppler | **R1** |
| `pdf.to-docx` | PDF to Word, **fast mode** (flagged lossy) | pdf → docx | N each | core | PyMuPDF + python-docx ([benchmark](benchmarks/pdf-to-docx.md)) | **R1** |
| `office.to-pdf` | Word, Excel, PowerPoint, OpenDocument to PDF; options PDF/A, tagged PDF (accessibility), page range | office-doc, office-sheet, office-slides → pdf | N each | office | LibreOffice | **R1** |
| `office.convert` | Office to office: doc↔docx↔odt↔rtf↔txt, xls↔xlsx↔ods↔csv, ppt↔pptx↔odp | office ↔ office | N each | office | LibreOffice | **R1** |
| `pdf.to-docx` (accurate mode) | Layout-AI mode as an option on the same tool | pdf → docx | N each | ml | Docling + Pandoc | R2 |
| `pdf.to-xlsx` | Tables to Excel/CSV (lattice and stream detection) | pdf → xlsx, csv | N each | core | pdfplumber, Camelot | R2 |
| `pdf.to-pptx` | One slide per page, optional hidden text layer | pdf → pptx | N each | core | pypdfium2, python-pptx | R2 |
| `pdf.to-html` | PDF to HTML | pdf → html (zip) | N each | core | Poppler `pdftohtml` | R2 |
| `pdf.to-markdown` | Fast (no ML) or accurate (Docling) | pdf → md | N each | core / ml | markitdown / Docling | R2 |
| `pdf.extract-images` | Extract embedded images losslessly | pdf → zip | N each | core | pikepdf | R2 |
| `html.to-pdf` | HTML file or HTML zip to PDF with a print stylesheet. WeasyPrint Python API with a fetcher limited to `data:` and the scratch dir (no remote fetch, no other local files). Declares the edge `html → pdf` for `convert` | html, zip → pdf | N each | office | WeasyPrint | R2 |
| (route) Markdown to PDF | Not a tool of its own: a `convert` route and catalog tile, `md → html` (`markup.convert`, Pandoc `--sandbox`, `ebook`) then `html → pdf` (`html.to-pdf`, WeasyPrint, `office`). Both groups are in the default profiles. Replaces the earlier `markdown.to-pdf` tool id, which never shipped | md → pdf | N each | ebook, then office | Pandoc, then WeasyPrint | R2 |
| `image.convert` | HEIC/WebP/AVIF/JXL/PNG/JPEG/TIFF/GIF/BMP/SVG between each other | img → img | N each | core | libvips | R2 |
| `image.resize` | Resize, fit, crop to size | img → img | N each | core | libvips | R2 |
| `image.compress` | Lossy/lossless re-compression with quality slider and before/after | img → img | N each | core | libvips, pngquant, mozjpeg | R2 |
| `pdf.to-json` | Structured content (text blocks, tables) | pdf → json | N each | ml | Docling | R3 |
| `email.to-pdf` | EML/MSG to PDF, attachments as zip | eml, msg → pdf | N each | office | extract-msg, WeasyPrint | R3 |
| `image.trace` | Raster to SVG | png, jpg → svg | N each | core | VTracer / Potrace | R3 |
| `image.upscale` | AI upscale ×2/×4 (fast CPU model or Real-ESRGAN) | img → img | N each | ml | OpenCV dnn_superres, Real-ESRGAN ONNX | R3 |
| `image.remove-background` | Background removal | img → png | N each | ml | rembg | R3 |
| `url.to-pdf` | Web page by URL to PDF. Needs allow-listed egress through a filtering proxy and its own ADR (ADR-0007 "Later"); no group planned yet | url → pdf | 1 | (later) | Chromium | later |

### 6.4 Ebooks and comics (profile `ebook`; core set in R1)

The `ebook` worker image holds Calibre `ebook-convert`, Pandoc, djvulibre, libarchive (CBR) and img2pdf
(chosen in the engine research). Every tool here declares
its conversion **edges**, so the generic `convert` planner routes between all nine R1 ebook formats
(EPUB, FB2, MOBI, AZW3, DOCX, PDF, TXT, HTML, Markdown) in at most two steps, for example
md → epub (Pandoc) → mobi (Calibre). Edges with PDF as the source are flagged lossy (Calibre
cannot recover reflowable structure from PDF). The R1 tools live in `ebook`, not `core`, so one
profile switches all ebook and comic handling on or off and RAR support stays out of `core`.
Calibre runs `ebook-convert` only, with an input allowlist of the R1 formats (no CHM, LIT, LRF,
PDB, RB, SNB or TCR readers in R1) and its config, temp and cache dirs inside the step's scratch
dir (ADR-0007 Appendix A). Calibre steps are heavy steps and take the heavy-step slot.

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `ebook.convert` | Ebook conversion between EPUB, FB2, MOBI, AZW3, DOCX, PDF, TXT, HTML (single file or zip, no remote fetch); DRM-protected input refused with `DRM_PROTECTED` | epub, fb2, mobi, azw3, docx, pdf, txt, html ↔ same | N each | ebook | Calibre `ebook-convert` | **R1** |
| `markup.convert` | Markdown, HTML, DOCX, EPUB, FB2 to each other and to TXT (Pandoc `--sandbox`) | md, html, docx, epub, fb2 → md, html, docx, epub, fb2, txt | N each | ebook | Pandoc | **R1** |
| `djvu.to-pdf` | DJVU to PDF; option `ocr` (`auto` default, or `never`). The steps are planned **at job creation**: with `auto` and the `ocr` group online, the job is `djvu → pdf` (`ebook`) then `ocr.pdf` in skip-text mode (`ocr`), which leaves pages that already have text alone; with `ocr` offline the job has one step and carries an `OCR_SKIPPED` warning. No step appends steps at run time. Page count checked against `FILEX_MAX_PAGES` before converting | djvu → pdf | N each | ebook (+ocr) | djvulibre `ddjvu`, OCRmyPDF | **R1** |
| `comic.to-pdf` | CBZ/CBR to PDF, images kept losslessly, natural page order | cbz, cbr → pdf | N each | ebook | libarchive, img2pdf | **R1** |
| `pdf.to-cbz` | PDF pages to CBZ at a chosen DPI. No CBR output: writing RAR needs the non-free `rar` tool | pdf → cbz | N each | ebook | pypdfium2 | **R1** |
| `ebook.convert` (more formats) | Adds KEPUB, LIT, PDB, RTF, HTMLZ | ebook ↔ ebook | N each | ebook | Calibre | R2 |
| `markup.convert` (more formats) | Adds ODT, RST, LaTeX | many ↔ many | N each | ebook | Pandoc | R2 |
| `comic.to-pdf` (CB7) | Adds CB7 input | cb7 → pdf | N each | ebook | 7zip, img2pdf | R2 |
| `comic.to-epub` | CBZ/CBR/CB7 to EPUB (fixed layout) | comic → epub | N each | ebook | Calibre | R2 |
| `pdf.to-djvu` | Archival DJVU output | pdf → djvu | N each | ebook | pdf2djvu | R3 |
| `ebook.metadata` | Edit title, author, series, cover | ebook → ebook | 1 | ebook | Calibre `ebook-meta` | R3 |

### 6.5 OCR and scans (profile `ocr`)

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `ocr.pdf` | Searchable PDF from scans or images; languages, skip/redo/force existing text, deskew, auto-rotate, clean, PDF or PDF/A output, optional `.txt` sidecar (an output with role `REPORT`) | pdf, img → pdf (+txt) | N each | ocr | OCRmyPDF, Tesseract 5 | **R1** |
| `scan.clean` | **Basic** scan cleanup: deskew, unpaper clean, black-border removal, auto-rotate, blank-page removal, optional OCR; before/after preview | pdf, img → pdf | N each | ocr | unpaper, OpenCV, OCRmyPDF | **R1** |
| `scan.enhance` | **Full "Clean scan"**: dewarp, shadow and background whitening, auto-crop, contrast, then OCR, then JBIG2 (lossless) / MRC compression | pdf, img → pdf | N each | ocr | OpenCV, unpaper, jbig2enc, OCRmyPDF | R2 |
| `ocr.accurate` | ML OCR for hard scans and CJK | pdf, img → pdf | N each | ml | PaddleOCR or docTR | R3 |
| `doc.safe-pdf` | Pixel CDR: render to images, rebuild, OCR (Dangerzone pattern) | pdf, office → pdf | N each | core + ocr | pypdfium2, img2pdf, OCRmyPDF | R3 |

**OCR languages (O3).** The `worker-ocr` image ships Tesseract data for `eng`, `rus`, `deu`,
`fra`, `spa` and `ita`. `ukr` and `bel` are documented add-ons: the admin lists them in `.env`
(for example `FILEX_OCR_EXTRA_LANGS=ukr,bel`) and the install guide shows how they get into the
tessdata volume (build arg or a one-off fetch outside the worker); workers still never download
anything at job time.

### 6.6 Edit

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `pdf.watermark` | Text or image watermark; opacity, rotation, 9-point position or tiled, pages, behind/above content | pdf (+img) → pdf | N each | core | reportlab + pikepdf | **R1** |
| `pdf.page-numbers` | Page numbers with template (`Page {n} of {total}`), position, start number, font size, pages | pdf → pdf | N each | core | reportlab + pikepdf | **R1** |
| `pdf.metadata-edit` | Set or clear title, author, subject, keywords, creator, producer, dates | pdf → pdf | N each | core | pikepdf (Info + XMP) | **R1** |
| `pdf.header-footer` | Header and footer text with templates | pdf → pdf | N each | core | reportlab + pikepdf | R2 |
| `pdf.bates` | Bates numbering with prefix, digits, start | pdf → pdf | N→N (continues across files) | core | reportlab + pikepdf | R2 |
| `pdf.forms-fill` | Fill form fields (form widget) | pdf → pdf | 1 | core | pypdf | R3 |
| `pdf.einvoice` | Create and validate Factur-X / ZUGFeRD e-invoices | pdf + xml → pdf | 1 | core | factur-x | R3 |
| `pdf.adjust-colors` | Invert, replace colors, contrast | pdf → pdf | N each | core | pypdfium2 | R3 |

### 6.7 Security and privacy

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `pdf.encrypt` | AES-256 password protection, owner password, permissions (print, copy, edit) | pdf → pdf | N each | core | pikepdf | **R1** |
| `pdf.decrypt` | Remove password (needs the password) and restrictions | pdf → pdf | N each | core | pikepdf | **R1** |
| `pdf.metadata-strip` | Remove the Info dictionary, XMP, document ID, embedded files, JavaScript, document-level actions and page piece info, optionally comments; saves a full rewrite (no incremental update), then re-opens the output and fails the job if any stripped field is still present (review M8). The help text says what is and is not removed | pdf → pdf | N each | core | pikepdf | **R1** |
| `pdf.sign` | PAdES B-B signature with an uploaded PKCS#12 (.p12/.pfx) in the sensitive auxiliary slot `certificate` (deleted as soon as its step ends, never logged or returned) and a secret passphrase; invisible or visible stamp (page, 9-point position, optional image in the `stamp` slot), reason, location. FileX holds no signing keys. The help text says a `.p12` is readable by a concurrent compromised step while it exists (review H8) | pdf + p12 (+img) → pdf | N each | core | pyHanko | **R1** |
| `cert.create` | Make a self-signed signing certificate as a .p12 encrypted with `BestAvailableEncryption` under a user passphrase of at least 12 characters; the key is generated inside the step and FileX keeps no copy beyond normal output retention | (none) → p12 | 0 | core | cryptography / OpenSSL | **R1** |
| `pdf.sign` (timestamp/LTV) | PAdES B-T / B-LT / B-LTA with an RFC 3161 TSA (needs controlled egress) | pdf + p12 → pdf | N each | core | pyHanko | R2 (if A6 allows) |
| `pdf.verify-signatures` | Validation report: signer, integrity, timestamp, trust | pdf → json, html | N each | core | pyHanko | R2 |
| `pdf.redact` | Manual redaction boxes (draw-boxes widget) and search/regex redaction, **true removal**, then re-extract text and report proof | pdf → pdf (+report) | 1 | core | PyMuPDF | R2 |
| `pdf.sanitize` | Strip JavaScript, actions, embedded files, links, XFA | pdf → pdf | N each | core | pikepdf | R2 |
| `file.metadata-strip` | Strip metadata from images and Office files | img, office → same | N each | core | exiftool, then rewrite | R2 |
| `pdf.redact-pii` | Auto-detect PII (names, IBAN, emails, phones) for review, then redact | pdf → pdf | 1 | ml | Presidio | R3 |

### 6.8 Compare

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `pdf.compare` | Text diff plus visual overlay per page | pdf ×2 → pdf (report), json | 2 | core | PyMuPDF, OpenCV | R2 |
| `office.compare` | DOCX/XLSX diff (text and cells) | docx ×2, xlsx ×2 → html, json | 2 | office | python-docx, openpyxl | R3 |

### 6.9 AI (profile `ai`; deferred, requirements in section 8)

Deferred: designed in [ADR-0010](adr/0010-ai-worker-group.md) but not on the current roadmap. The
"R2" release marks below are the original plan.

| Id | Tool | In → Out | Files | Group | Engine | Rel |
|---|---|---|---|---|---|---|
| `ai.summarize` | Summary (short / detailed / bullet points), in a chosen language | pdf, office, txt, md → md | N each | core or ml (extract), then ai | `pdf.to-text` or Docling text, then Ollama | R2 |
| `ai.chat` | Chat with a document: index once, then ask questions with page citations | pdf, office, md → index; question → answer | 1 | core or ml (extract), then ai | extracted text, then embeddings + sqlite-vec + Ollama | R2 |
| `ai.translate` | Layout-preserving PDF translation; the PDF is parsed in a preceding sandboxed step and the translated text is laid out by FileX's own PDF code | pdf → pdf | N each | core (parse), then ai | BabelDOC-style layout + Ollama or Argos | R2 |
| `ai.translate-text` | Translate text documents (DOCX text extracted in a preceding step) | txt, md, docx → same | N each | ai (after extract for docx) | Argos Translate | R2 |

**AI steps take only text FileX extracted itself** ([ADR-0010](adr/0010-ai-worker-group.md)). For
PDF and Office inputs the planner prepends an extraction step (`pdf.to-text` in `core`, or Docling
in `ml`), so an `ai` step never parses a hostile binary. The planner, not the client, enforces that
AI tools receive only size-capped `text/plain` from a previous FileX step or a UTF-8-decoded
upload. The `ai` step has the same seccomp filter as every other step and talks to Ollama only
through the runner's Unix-socket relay. Answers are untrusted: saved as `text/plain` attachments
and shown as text or as Markdown with raw HTML off.
| `ai.extract` | Extract fields to JSON/CSV (invoice, receipt) | pdf → json, csv | N each | ai | Ollama | later |
| `ai.quiz` | Questions or flashcards from a document | pdf → md | 1 | ai | Ollama | later |

### 6.10 Automation (R3)

| Id | Feature | Rel |
|---|---|---|
| `recipe.*` | Saved chains of tools with per-step options ("OCR, then compress to 2 MB, then PDF/A"), runnable from UI and API; stored per user (section 7.2, "Per-user data") | R3 |
| watch folders | Per-folder recipes on mounted SMB/NFS folders (polling, since inotify does not fire on network mounts) | R3 |
| webhooks | Notify a URL when a job finishes | R3 |
| saved presets | Per-user saved options per tool, stored with row-level security (section 7.2) | R2 |
| job history | Per-user history of past jobs (metadata only, not files), stored like presets | R2 |

### 6.11 Release 1 tool list (31 tools)

Accepted on 2026-10-10 (O7), with ebooks moved into R1.

| Group | Tools |
|---|---|
| core (21) | `pdf.merge`, `pdf.split`, `pdf.rotate`, `pdf.organize`, `pdf.extract-pages`, `pdf.remove-pages`, `pdf.compress`, `pdf.repair`, `pdf.to-pdfa`, `image.to-pdf`, `pdf.to-image`, `pdf.to-text`, `pdf.to-docx`, `pdf.watermark`, `pdf.page-numbers`, `pdf.metadata-edit`, `pdf.metadata-strip`, `pdf.encrypt`, `pdf.decrypt`, `pdf.sign`, `cert.create` |
| office (2) | `office.to-pdf`, `office.convert` |
| ocr (2) | `ocr.pdf`, `scan.clean` |
| ebook (5) | `ebook.convert`, `markup.convert`, `djvu.to-pdf`, `comic.to-pdf`, `pdf.to-cbz` |
| planner (1) | `convert` |

**Changes from the originally requested R1 list, and why:**

1. **Added `cert.create`.** Signing needs a certificate, and most invited users have none. Without
   it `pdf.sign` is usable only by people who already have one. It is a small tool
   (`cryptography`), security-reviewed, and it is the first half of differentiator G7.
2. **Added `office.convert`** (office to office). The research names office-to-office as a gap no
   self-hosted PDF tool covers (G1), and it costs almost nothing once the `office` worker exists:
   the same LibreOffice call with a different target. It also gives `convert` many more routes.
3. **Included `pdf.to-docx` in fast mode only; deferred `pdf.to-xlsx` and `pdf.to-pptx` to R2.**
   PDF to Word is the most-used "from PDF" conversion, but open-source fidelity is weak (pdf2docx is
   unmaintained). R1 ships it clearly labelled "fast, layout may shift". The accurate mode (Docling)
   and the Excel/PowerPoint tools come together in R2 with a fidelity benchmark.
4. **Signing in R1 is PAdES B-B only.** Timestamps and LTV need the worker to reach a TSA and
   OCSP/CRL servers, but workers have no egress (ADR-0007); egress needs its own ADR first (A6).
5. **Scan cleanup in R1 is the basic set** (unpaper, OpenCV deskew, blank pages). The full
   "Clean scan" (dewarp, whitening, MRC) needs tuning against the corpus and is R2.
6. **Ebooks are in R1** (decision O7). The `ebook` profile joins the defaults
   (`office,ocr,ebook`), which adds ~500-800 MB of image download and one more optional worker.
   R1 covers EPUB, FB2, MOBI, AZW3, DOCX, PDF, TXT, HTML and Markdown in both directions, DJVU to
   PDF and CBZ/CBR to PDF and back to CBZ. `markup.convert` moved from R2 (`office`) to R1
   (`ebook`) because Pandoc carries the Markdown and HTML routes. `comic.to-pdf` and `pdf.to-cbz`
   moved from `core` to `ebook` so RAR support stays out of the always-on image. KEPUB, LIT, PDB,
   RTF, CB7 and comic to EPUB stay in R2.
7. **No CBR output.** Creating RAR archives needs the proprietary `rar` tool; PDF goes to CBZ only.

## 7. API surface summary

Full detail in [architecture.md section 5](architecture.md#5-api-surface-v1); the contract is
generated from the tool registry and committed as `docs/api/openapi.json` (ADR-0004). This section
lists only what the decisions in section 2 add or change.

### 7.1 Endpoints (prefix `/api/v1`)

| Method + path | Auth | Purpose | Rel |
|---|---|---|---|
| `GET /health` | none | `{"status":"ok"}` only, for Docker health checks (review L2) | R1 |
| `GET /config` | none | Public runtime config for the UI, read from env at request time: `authMode` (sign-in, `TOKEN` or `NONE`; exact values in section 7.2), the sign-in server URL and anon key, limits (`maxUploadMb`, `maxFiles`, `retentionMin`), version ([ADR-0009](adr/0009-keel-auth.md), A9) | R1 |
| `GET /status` | yes | Online worker groups, engine versions, isolation mode (`container` in R1) | R1 |
| `DELETE /session` | yes | Evicts the caller's token from the API's auth cache; the UI calls it before `signOut()` | R1 |
| `GET /tools`, `GET /tools/{toolId}` | yes | Catalog with options JSON Schema, formats, auxiliary slots, `available`, profile hint | R1 |
| `GET /formats` | yes | Known formats | R1 |
| `GET /convert/plan?from=&to=` | yes | Route preview with lossy hops, or `422 NO_ROUTE` naming the profile that would create one | R1 |
| `POST /tools/{toolId}/jobs` | yes | Multipart `files[]`, one part per auxiliary slot (e.g. `certificate`), `options` JSON part → `202` + job. Unknown part names give `422`; the part name `inputs` is reserved for job outputs as inputs (A5) and answers `422 NOT_SUPPORTED` in R1 | R1 |
| `GET /jobs` | yes | **The caller's own jobs**, newest first (`limit` up to 100, `before` cursor) | R1 |
| `GET /jobs/{jobId}` | yes, owner | Status (poll; `ETag`/`304`), including `warnings[]` and outputs with `role` | R1 |
| `GET /jobs/{jobId}/result`, `GET /jobs/{jobId}/outputs/{n}` | yes, owner | Download with the Bearer header (scripts). `result` is the single `RESULT` output or a zip of all `RESULT` outputs; reports are fetched through `/outputs/{n}`. `Range` supported | R1 |
| `POST /jobs/{jobId}/result/link`, `POST /jobs/{jobId}/outputs/{n}/link` | yes, owner | `{ "url", "expiresAt" }`: a signed download link valid 5 minutes (browser downloads, A8) | R1 |
| `GET /dl/{jobId}/{target}?exp=&sig=` | signature | Streams `result` or output `n` to the browser without an `Authorization` header; `Range` supported. Every failure (bad id, target or `exp`, bad signature, gone job or output) returns the same `404` and counts toward the per-IP `rl:auth` limit | R1 |
| `DELETE /jobs/{jobId}` | yes, owner | Cancel and delete now. Idempotent `204` | R1 |
| `GET /openapi.json` | none | Generated contract | R1 |
| `POST /jobs/{jobId}/messages` (shape to be designed) | yes, owner | Follow-up questions for `ai.chat` | R2 |

**Job JSON additions** ([ADR-0003](adr/0003-tool-plugin-model.md) amendment): job ids are
`"j_" + base64url(16 random bytes)`; `warnings: [{ "code", "message", "step" }]` (codes
UPPER_SNAKE and in the contract, such as `LOSSY_STEP`, `TARGET_NOT_REACHED`, `OCR_SKIPPED`; at
most 20 per job, 300 characters each, written by tool code, never copied from engine output);
each output has `role`: `RESULT` or `REPORT`. A heavy step waiting for the heavy-step slot leaves
the job `QUEUED` with the message "waiting for a free slot". Secret options never appear.

**Error codes** (envelope as in the architecture): `401 UNAUTHENTICATED` (no, bad or expired
token), `403 USER_NOT_ALLOWED` (valid sign-in, not on FileX's allowlist), `404 JOB_NOT_FOUND`
(unknown, expired or **not yours**), `409 TOOL_UNAVAILABLE` (names the profile to enable), `413`
upload too large, `422` invalid options, input format, `NO_ROUTE` or `NOT_SUPPORTED`,
`429 QUEUE_FULL` (group queue full) or `429 TOO_MANY_REQUESTS` (more than 20 `401`s per client IP
per minute), `503 AUTH_UNAVAILABLE` (sign-in server unreachable, with `Retry-After: 10`), `507 INSUFFICIENT_STORAGE`
(disk nearly full). Job-level `error.code` values include `ENGINE_TIMEOUT`, `WORKER_LOST`, `SECRET_UNAVAILABLE`
(a redelivered step whose secret was already consumed; the user resubmits), `DRM_PROTECTED`,
`INVALID_INPUT`, `WRONG_PASSWORD`, `WRONG_PASSPHRASE`.

Defaults: `FILEX_MAX_UPLOAD_MB=1024`, `FILEX_MAX_FILE_MB` (same), `FILEX_MAX_FILES=50`,
`FILEX_RETENTION_MIN=1440` (capped at 1440), `FILEX_MIN_FREE_MB=4096` (the install guide asks for at
least 20 GB free on the data volume), `FILEX_MAX_QUEUED=20`, `FILEX_HEAVY_SLOTS=1`,
`FILEX_MAX_PIXELS` 100 MP, `FILEX_MAX_PAGES=2000`, `FILEX_HSTS=false` (set `true` once the UI is
served only over HTTPS), `FILEX_TRUSTED_PROXIES=127.0.0.1`. The sign-in server URL must be `https`.

### 7.2 Authentication with Keel

Optional, advanced setup ([ADR-0009](adr/0009-keel-auth.md)); the public build runs in local mode
or with access tokens instead ([ADR-0011](adr/0011-public-local-mode.md)). Keel is a self-hosted,
Supabase-compatible server; any server with the same Auth API works. The code and settings use the
name Keel (`KEEL_URL`, `KEEL_ANON_KEY`, `authMode: "KEEL"`, principal `keel:<user id>`).

| Item | Requirement |
|---|---|
| Identity provider | A Keel (Supabase-compatible) server with a project for FileX, reached at `KEEL_URL`. Keel holds users. Sign-ups disabled; the admin invites users in Keel Studio. |
| UI sign-in | `@supabase/supabase-js` v2, `createClient(keelUrl, keelAnonKey)`, email + password. Invite links land on FileX with `type=invite`; FileX asks the new user to set a password (`auth.updateUser({ password })`). "Forgot password" uses Keel's reset flow. |
| Requests from the UI | `Authorization: Bearer <Keel access token>`, never a cookie, query string or form field. supabase-js refreshes tokens; Keel's session token lifetime is set to 900 s (O2), so a stolen access token alone dies within 15 minutes. Sign-out calls `DELETE /session`, then `signOut()` with the default `global` scope. |
| Token check in FastAPI | A Bearer value starting with `fxt_` is checked locally only; anything else goes to Keel `GET {keelUrl}/auth/v1/user` with `apikey: <anon key>`, over TLS with verification always on (3 s connect, 3 s read). The response shape is validated. `200` means authenticated and the principal is `keel:<user.id>`; `5xx`, `429`, timeout, TLS or transport error give `503 AUTH_UNAVAILABLE`; **every other status** (including GoTrue's `403`) and a malformed body give `401 UNAUTHENTICATED`. No status falls through to a pass. Only successes are cached: key `sha256(token)`, TTL `min(60 s, exp - now)`, bounded LRU of about 1000 entries. `DELETE /session` evicts the caller's entry before the UI signs out, so the up-to-60 s lag remains only for sessions ended elsewhere (a ban, another device). **No JWT secret and no service-role key in FileX.** `KEEL_URL` must be `https`; the API refuses to start otherwise. |
| Authorization (review C1) | Authenticated is not enough: Keel's project is reachable from the internet, so FileX also checks that the user is allowed. Allowed means `user.id` is in `FILEX_ALLOWED_USER_IDS` (comma-separated) **or** `user.app_metadata.filex` is `"user"` or `"admin"` (`app_metadata` can be set only with the service role, so users cannot grant it to themselves). Never authorize by email or `user_metadata`; reject `is_anonymous: true`. Anyone else gets `403 USER_NOT_ALLOWED`, and the UI says the account is not allowed to use FileX and to ask the admin. This keeps FileX closed even if sign-ups are turned back on in Keel. |
| Static API tokens | Optional, for scripts. A token is `fxt_` + `secrets.token_urlsafe(32)`; config holds only hashes: `FILEX_API_TOKENS=name1:<sha256 hex>,name2:<sha256 hex>`. At startup each entry must be `<name>:<64 lowercase hex>` with a unique name matching `^[a-z0-9_-]{1,32}$`, or the API refuses to start. A presented `fxt_` value shorter than 47 characters gets `401` before hashing; otherwise its SHA-256 is compared with `hmac.compare_digest` against each hash. The principal is `token:<name>`. Tokens are never logged; rotation is add new hash, restart, move scripts, remove old hash. Works while Keel is down. |
| Keel unreachable | Fail closed: `503 AUTH_UNAVAILABLE` with `Retry-After: 10` for Keel tokens, never cached; the UI shows "Sign-in server unreachable" and keeps the user's current page. `fxt_` tokens still work. |
| Abuse limit | More than 20 `401`s per client IP per minute gives `429 TOO_MANY_REQUESTS` (`rl:auth:<ip>`); failed `/dl/...` requests count too. The client IP comes from `X-Forwarded-For` only when the peer is in `FILEX_TRUSTED_PROXIES` (default `127.0.0.1`, for a reverse proxy on the same host). |
| Not configured (O1, resolved; amended by ADR-0011) | If neither `KEEL_URL`/`KEEL_ANON_KEY` nor `FILEX_API_TOKENS` is set, the API **refuses to start** and logs which variables are missing. The only way to run without auth is to set `FILEX_AUTH=none` explicitly, and only with a loopback `FILEX_BIND` (local mode, ADR-0011); then every request is the principal `anonymous`, FileX logs a warning on every start and `GET /config` reports `authMode: "NONE"`. With only static tokens configured, `authMode` is `TOKEN` and the UI asks for a token instead of an email. Keel configured without any allowlist entry (`FILEX_ALLOWED_USER_IDS` empty) is allowed, because `app_metadata.filex` may grant access, but it logs a warning at start. |
| Job ownership | The owning principal (`keel:<user id>`, `token:<name>` or `anonymous`) is stored in `own:<jobId>`, written only by the API in the same `MULTI` that creates the job; `ownidx:<principal>` indexes the principal's jobs for `GET /jobs`. One dependency `require_job` guards every route with `{jobId}` and answers `404 JOB_NOT_FOUND` for unknown and other people's jobs; a test walks the route table. No cross-user view in R1. |
| Downloads (A8) | Scripts download with their Bearer header. The browser mints a link when the user clicks Download: `POST .../result/link` or `.../outputs/{n}/link` (owner-checked) returns `/api/v1/dl/{jobId}/{target}?exp=&sig=`, `sig = HMAC-SHA256(key, "dl
{jobId}
{target}
{exp}")`, valid at most 5 minutes, reusable within that window so `Range` resume works. The key is 32 random bytes held in memory, which needs the API to stay **one uvicorn process**; if it ever runs more, the key comes from a Docker secret file (never Redis or `.env`). Redeeming checks, in order, the id regex, `target` and `exp` bounds, the HMAC, then Redis. The access log drops query strings on every route, so `sig` is never logged. All three output routes send the attachment headers of section 5.2. |
| Runtime config (A9) | The UI fetches `GET /config` before `createClient`, so a published image carries no deployment-specific value; `VITE_KEEL_URL` / `VITE_KEEL_ANON_KEY` remain a dev-server fallback only. The anon key is public by design. |
| Per-user data (later) | Per-user data (job history metadata, saved presets, recipes) lives in Keel Postgres tables in `public` with RLS keyed by `auth.uid()`. FileX writes them with the **user's own token** through PostgREST, so no service-role key is needed. Schema changes go in `supabase/migrations/<timestamp>_<name>.sql`. Nothing in R1 needs a Keel table. |
| Files | Never stored in Keel Storage. They stay on the FileX data volume and follow the 24 h retention. |
| Network | Only the API container talks to Keel. Workers still have no egress. The browser talks to Keel directly for sign-in, so CSP `connect-src` includes the Keel URL. The API port is published on `${FILEX_BIND:-127.0.0.1}` only, never on every interface (section 5.2). |

## 8. AI profile requirements (deferred)

**Deferred:** the AI profile is not on the current roadmap. These requirements stay as the design
to follow if it is picked up again. The architecture must already allow them, so a later release
can add containers and tools without reworking the job model. Design: [ADR-0010](adr/0010-ai-worker-group.md) (accepted with the security
review's section 5.2 conditions).

1. **Optional profile `ai`.** Off by default. Hidden tools show "enable the `ai` profile" like any
   other unavailable group. Documented minimum: 8 GB RAM for a 3-4B Q4 model; not offered on the
   2-4 GB reference boxes.
2. **Three services:** `ollama` (inference server, model volume `filex-models`, on the `ai`
   network only, never on `internal`, never sees `/data` or Redis; ADR-0007 A.1 hardening:
   non-root, read-only except `filex-models` and a tmpfs, `cap_drop: [ALL]`, no `ports:`; minimum
   version enforced by the engine probe and part of the weekly rebuild), `worker-ai` (the usual
   runner on `internal` and `ai`, a heavy step) and `ollama-pull` (profile `ai-pull` only, the
   only service with egress, never started by `--profile all`). `FILEX_OLLAMA_URL` pointing at
   another machine on the network needs allow-listed egress and waits for the egress ADR.
3. **No sockets in `ai` steps.** They run under the same seccomp filter as every other step. The
   `worker-ai` runner opens a Unix socket in the step's scratch dir and relays to Ollama: only
   `POST /api/generate`, `/api/chat` and `/api/embed` (anything else `403`), request and response
   size caps, the step's wall-clock timeout, and `num_predict` and context size forced from the
   tool's limits on every request. The socket is closed and unlinked when the step ends.
4. **AI tools take only text FileX extracted itself.** The planner, not the client, prepends an
   extraction step for PDF and Office inputs (`pdf.to-text` in `core`, Docling in `ml`) and passes
   only size-capped `text/plain` from a previous FileX step or a UTF-8-decoded upload. Layout
   translation parses the PDF in a preceding sandboxed step.
5. **LLM output is untrusted.** Answers are `text/plain` attachments. The UI shows them as text, or
   as Markdown with raw HTML off and no images or links built from the output, never through
   `dangerouslySetInnerHTML`. The model gets no tools, and its output never picks tools, options,
   paths or later steps.
6. **Models never download at job time.** The admin pulls them by hand with official model names
   (`docker compose --profile ai-pull run --rm ollama-pull <model>`); translation models are baked
   into the `worker-ai` image. Nothing ever calls a cloud API.
7. **Jobs, not sessions, for summarize and translate.** They fit the current job model as is.
   Progress messages report pages or chunks done, since small home-server CPUs manage only a few
   tokens per second.
8. **Chat needs follow-ups that reuse a job's output.** `ai.chat` creates an index (embeddings in
   `sqlite-vec`) as a job output; each question is a small step that reads that index. The shape
   is reserved now (multipart part `inputs`: `[{ "fromJob", "output" }]`, owner-checked, `422
   NOT_SUPPORTED` in R1; ADR-0003 amendment). Answers cite page numbers and can stream through the
   job's `message` field while polling (no SSE needed).
9. **Retention applies to AI artifacts too.** Indexes and chat transcripts are job outputs and are
   deleted after 24 h unless the user saves the chat to history (per-user data, text only).
10. **Concurrency 1** for `worker-ai`; it takes the heavy-step slot; the queue cap protects the box.

## 9. UI requirements

Visual direction and tokens are in [design.md](design.md). Every item has an acceptance check in
[section 10](#10-acceptance-criteria-per-release).

1. **Sign-in.** In local mode (`authMode` `NONE`) no sign-in screen. With a sign-in server: email
   + password; set-password screen for invite links; clear message when the server is
   unreachable; "not allowed" message for a user who is not on the allowlist
   (`403 USER_NOT_ALLOWED`). A token field instead of email when `authMode` is `TOKEN`. Reads
   `GET /config` before creating the sign-in client. Sign-out calls `DELETE /session`, then
   `signOut()` (global scope).
2. **Tool catalog.** Home page lists tools by category (section 6 groups) with title, one-line
   summary and icon. **Search** by name, synonym and format ("compress", "shrink", "docx",
   "word to pdf") with keyboard shortcut `/`. Generated "X to Y" tiles from the conversion graph.
   Unavailable tools are greyed out with "Enable the `ocr` profile" and stay findable by search.
3. **Upload.** Drag-and-drop on the whole tool page plus a "Choose files" button; accepted formats
   from the tool's `inputs`; size and count checked before upload with a clear message; upload
   progress bar; cancel.
4. **Options form generated from the tool schema** (enum → radio or select, bool → switch,
   number → input with min/max, string, page-range field, password field for secret options,
   auxiliary file field). Sensible defaults so most tools run with zero changes. Named widgets for
   richer input, added one at a time: **page-grid** (R1, for `pdf.organize`), **position picker**
   (R1, 9-point grid for watermark, page numbers and signature stamp), **draw boxes** (R2, redaction
   and crop).
5. **Progress.** Job status, step list for multi-step conversions ("DOCX → PDF via LibreOffice"),
   progress bar from `progress`, step message, cancel button. Polls `GET /jobs/{id}` about once a
   second with `ETag`, backing off when the tab is hidden. A heavy step waiting for its slot shows
   "waiting for a free slot".
6. **Result.** Download button (single file or zip), per-file downloads for batches, output size,
   "expires in 23 h", delete now, "use this file in another tool" (R2, A5). Every download mints a
   signed link on click and hands it to the browser (`<a download>` or `window.location`), so large
   files stream to disk instead of a JS `Blob`. Job `warnings` are shown in plain language; `REPORT`
   outputs (sidecars, proof reports) are listed as separate downloads.
7. **Preview where it matters.** PDF.js, bundled locally and loaded lazily, renders input and
   output pages client-side. R1 uses it in: page-grid (thumbnails), before/after for `pdf.compress`
   and `scan.clean`, and result preview for PDF outputs. No server-side preview endpoint in R1.
   Previews are only PDF.js canvas renders or images in `<img>`; outputs are never put in an
   `<iframe>`, `<object>` or `<embed>`.
8. **Before/after for compression.** Side-by-side (or slider on mobile) page render at the same
   zoom, original size, new size, percentage saved, and "target reached" or "best we could do" for
   target-size mode.
9. **Batch.** Drop many files on an `N each` tool; one job, one row per file in the result, zip of
   all. `N→1` tools (merge, images to PDF combined) show a reorderable file list before submit.
10. **My jobs.** List from `GET /jobs` (own jobs only) with status, tool, time and expiry.
11. **Convert page.** From/to pickers (from is detected from the dropped file), route preview with
    lossy warnings before upload, `NO_ROUTE` explains which profile to enable.
12. **Responsive** down to 360 px wide; light and dark themes; no third-party requests other than
    the configured sign-in server.
13. **Accessible** per section 5.3.
14. **Safe rendering (review C2 fix 3).** Lint rule `react/no-danger` in the lint config; no
    `dangerouslySetInnerHTML` or `innerHTML`; no `href` or `src` built from job data other than
    FileX API paths; filenames, progress and error messages rendered as text. supabase-js keeps
    its default `localStorage` session storage in R1 (accepted in the security review).

## 10. Acceptance criteria per release

### R1: core + office + ocr + ebook (31 tools)

- [ ] `cp .env.example .env` (plus the sign-in settings of section 7.2 when used), `python scripts/make_secrets.py`, `docker compose up -d`
      brings up API, Redis, `worker-core`, `worker-office`, `worker-ocr`, `worker-ebook`; all
      health checks green within 2 min on the weak box.
- [ ] With `COMPOSE_PROFILES=` (empty), the 21 core tools work and the office/ocr/ebook tools show
      the profile hint; `convert` plans only core routes.
- [ ] By default the API port listens on `127.0.0.1` only: it is not reachable from another LAN
      device through the host's LAN IP; with `FILEX_BIND=<LAN or VPN IP>` (or a reverse proxy) it
      is reachable on that network.
- [ ] With the optional sign-in server: an invited user can sign in, set a password from an
      invite link, and sign out. With sign-ups disabled on the server, there is no way to create an
      account from FileX.
- [ ] A valid signed-in user who is neither in `FILEX_ALLOWED_USER_IDS` nor has
      `app_metadata.filex` set gets `403 USER_NOT_ALLOWED` on every protected route; an anonymous
      user is refused; setting `user_metadata.filex` does not grant access.
- [ ] User A cannot see, poll, download or delete user B's jobs (`404`), checked by an API test.
- [ ] With the sign-in server stopped, UI sign-in shows "Sign-in server unreachable", API returns
      `503 AUTH_UNAVAILABLE`, and `fxt_` token requests still work.
- [ ] With no auth configured, the API **refuses to start** (non-zero exit) and names the missing
      variables; there is no implicit open mode. Only with `FILEX_AUTH=none` set explicitly and a
      loopback `FILEX_BIND` does it start, log a warning on every start and report
      `authMode: "NONE"` on `/config`. It also refuses to start on a malformed or duplicate
      `FILEX_API_TOKENS` entry and on a non-`https` sign-in server URL.
- [ ] `FILEX_API_TOKENS` holds only hashes; a matching `fxt_` token works, a wrong one or one
      shorter than 47 characters gets `401`; more than 20 `401`s per IP per minute give `429`.
- [ ] Unauthenticated `GET /health` returns exactly `{"status":"ok"}`; groups and engine
      versions are only on authenticated `GET /status`.
- [ ] A download link minted by the job's owner works without a header within 5 minutes and supports
      `Range`; after expiry, for another job or target, or with a changed signature it returns the
      same `404`; `sig=` never appears in the logs. `/result`, `/outputs/{n}` and `/dl/...` all send
      `Content-Disposition: attachment`, `nosniff`, `Content-Security-Policy: sandbox; default-src
      'none'` and `Cache-Control: no-store`; an HTML output is served as
      `application/octet-stream`.
- [ ] Sandbox checks in CI: a step that tries to open a TCP socket to Redis or the API is killed
      by the seccomp filter; a process that `setsid()`s out of its step is killed after the step;
      the `worker` Redis user cannot write `own:*`, `ownidx:*` or `rl:*`; every R1 engine's
      examples run under the filter; the local-file-inclusion corpus leaks no host or other-job
      file through any engine; archive bombs, traversal and nested archives fail before an engine
      runs.
- [ ] Job ids match `^j_[A-Za-z0-9_-]{22}$`; a submitted password or passphrase is absent from
      Redis (after use), logs, the job JSON and error messages; a redelivered step whose secret
      was consumed fails with `SECRET_UNAVAILABLE`.
- [ ] `tesseract --list-langs` in `worker-ocr` lists `eng`, `rus`, `deu`, `fra`, `spa`, `ita`; the
      install guide's steps add `ukr` and `bel`.
- [ ] All 31 R1 tools run end to end from the UI and from curl, each covered by tool tests on
      corpus files, including at least one bad-file case per tool that ends in a clean `FAILED`.
- [ ] A 1 GB upload completes with API RSS ≤ 200 MB; a 1.1 GB upload is refused with `413`.
- [ ] Results disappear 24 h after finishing (tested with a short retention override); `DELETE`
      removes files at once; restarting Redis or a worker leaves no orphan files after one janitor
      pass.
- [ ] `pdf.compress` target-size mode reaches the target on the corpus' scanned sample or reports
      the best achievable size; before/after view shows both renders and the sizes.
- [ ] `pdf.sign` output validates as a PAdES B-B signature with pyHanko and opens as signed in a
      common reader; the .p12 passphrase never appears in Redis, logs or API responses.
- [ ] `pdf.metadata-strip` output contains no Info/XMP author or producer data when re-read with
      exiftool and pikepdf.
- [ ] `pdf.to-pdfa` output passes veraPDF for PDF/A-2b on the corpus text samples (checked in CI
      with veraPDF in the test image, not shipped in `core`).
- [ ] `convert` handles at least: docx→pdf, pdf→docx, xlsx→ods, pptx→pdf, jpg→pdf, pdf→png,
      docx→txt, and the ebook routes docx→epub, epub→pdf, epub→mobi, fb2→azw3, md→epub, md→mobi,
      html→epub, djvu→pdf, cbr→pdf, pdf→cbz; planner golden-route tests pass.
- [ ] Ebook outputs open: EPUB passes `epubcheck` (in the CI test image only) on the corpus
      samples, MOBI/AZW3 re-open in Calibre, a Cyrillic FB2 keeps its text through FB2→EPUB→PDF;
      a DRM-protected file fails with `DRM_PROTECTED`; a DJVU without a text layer becomes a
      searchable PDF when `ocr` is on; CBR (RAR4 and RAR5) and CBZ keep image order and pixels.
- [ ] Section 5.1 targets measured on the weak box and recorded in `docs/benchmarks/`; any missed
      target has a recorded decision.
- [ ] Heavy-step slot: with `FILEX_HEAVY_SLOTS=1`, an Office conversion, an OCR job and an EPUB
      conversion submitted together never run their heavy steps at the same time (step timestamps),
      the waiting jobs show "waiting for a free slot", and a worker killed while holding the slot
      frees it within 30 s; with `FILEX_HEAVY_SLOTS=0` they overlap.
- [ ] Low-memory preset: with the `low-memory` block from `.env.example` on the weak box
      (`--memory 4g`) and default profiles, that same three-job mix finishes with no OOM kill; on
      the tiny box (2 GB, core only) every core tool runs. Each service's `mem_limit` comes from
      `FILEX_MEM_*` with `memswap_limit` equal to it.
- [ ] axe: zero serious/critical violations; full keyboard walkthrough of merge, organize,
      compress, sign and convert passes.
- [ ] Security sign-off: every "required before release 1" gate in the security review is closed
      (mapping in plan.md); over-engineering review pass done.
- [ ] README, install guide (local mode, LAN access with `fxt_` tokens, HTTPS reverse proxy, the
      optional sign-in server checklist including the 900 s session lifetime, profiles, low-memory
      preset, `FILEX_HSTS`, excluding the data volume from snapshots) and API docs published.

### R2: differentiators

- [ ] Profile `ml` exists (and `ai`, if the deferred AI profile is picked up); each is optional
      and its tools appear only when on.
- [ ] Remaining ebook formats (KEPUB, LIT, PDB, RTF, HTMLZ, ODT, RST, LaTeX, CB7) and
      `comic.to-epub` work; `convert` routes e.g. cb7→epub, epub→kepub.
- [ ] (Deferred AI profile only) `ai.summarize`, `ai.chat` (with page citations) and `ai.translate` run with a locally pulled
      model and make no outbound request (checked with the worker on a no-egress network); `ai`
      steps run under the standard seccomp filter and reach Ollama only through the relay, whose
      tests cover the endpoint allowlist, size caps, timeout and forced `num_predict`/context; AI
      tools receive only size-capped text; answers render as text or Markdown with raw HTML off.
- [ ] `scan.enhance` beats `scan.clean` on the scan-quality benchmark (OCR accuracy and size).
- [ ] `pdf.redact` proves removal by re-extracting text; `pdf.verify-signatures` reports on
      signed corpus files; `pdf.compare` shows text and visual diffs.
- [ ] `pdf.to-docx` accurate mode beats fast mode on the fidelity benchmark.
- [ ] Job history and saved presets per user in sign-in server tables with RLS (`auth.uid()`),
      migrations in `supabase/migrations/`.
- [ ] Remaining R2 catalog items (section 6) shipped or explicitly moved.

### R3: automation and breadth

- [ ] Recipes run from UI and API with per-step options; watch folders on SMB/NFS mounts; webhooks.
- [ ] ARM64 images built and tested in CI.
- [ ] Remaining R3 catalog items shipped or explicitly moved.

## 11. Project structure (scaffolded in plan task 1; may be adjusted)

```
filex/                  Python package
  api/                  FastAPI app, auth (sign-in server, tokens), schemas, routes, janitor
  worker/               runner (stream consumer) and step child process
  registry.py           @tool decorator, discovery, Files/Limits/Edge
  formats.py            format ids, extensions, MIME, magic-byte sniffing
  planner.py            conversion graph (ADR-0005)
  tools/<category>/     one module per tool
web/                    React + Vite app (src/, generated api types in src/api/)
docker/Dockerfile       one multi-stage Dockerfile: stage base, then one target per service
                        (api, worker-core, worker-office, worker-ocr, worker-ebook; later ml, ai)
compose.yaml            services (build targets of docker/Dockerfile), x-hardening on every service,
                        profiles, networks, volume, Redis ACL file and password secret files;
                        API published on ${FILEX_BIND:-127.0.0.1}
.env.example            COMPOSE_PROFILES=office,ocr,ebook, sign-in server URL (https) and anon
                        key (section 7.2), FILEX_ALLOWED_USER_IDS, FILEX_API_TOKENS (name:sha256), FILEX_AUTH,
                        FILEX_BIND, FILEX_ALLOWED_HOSTS, FILEX_TRUSTED_PROXIES, FILEX_HSTS,
                        FILEX_OCR_EXTRA_LANGS, limits (upload, files, retention, free space, queue,
                        pixels, pages), FILEX_CONCURRENCY, FILEX_HEAVY_SLOTS=1,
                        FILEX_MEM_CORE / _OFFICE / _OCR / _EBOOK, and the low-memory preset as a
                        commented block. No secrets: Redis passwords live in git-ignored secret files
supabase/migrations/    sign-in server schema changes (empty in R1)
tests/                  pytest: unit/, api/, tools/test_<id>.py, integration/
tests/corpus/           sample files; large files generated by script, not committed
e2e/                    Playwright tests
scripts/                gen_openapi.py, gen_tool_docs.py, make_corpus.py
docs/                   spec, plan, architecture, adr/, api/openapi.json, tools.md,
                        benchmarks/, diagrams/
```

## 12. Commands (scaffolded in task 1; later tasks make each one real)

```
Run:          docker compose up -d                      # profiles from COMPOSE_PROFILES in .env
Run all:      COMPOSE_PROFILES=all docker compose up -d
Backend test: pytest -q
One tool:     pytest tests/tools/test_pdf_merge.py -q
Lint:         ruff check . && ruff format --check .
Contract:     python scripts/gen_openapi.py --check     # fails if docs/api/openapi.json is stale
Tool docs:    python scripts/gen_tool_docs.py
UI dev:       npm --prefix web run dev
UI build:     npm --prefix web run build
UI test:      npm --prefix web test
E2E:          npx playwright test                        # against docker compose
```

## 13. Code style

- **Python 3.12**, `ruff` for lint and format, type hints on public functions, pydantic v2 for
  schemas. A tool looks like the example in
  [architecture.md section 6](architecture.md#6-tool-plugin-model): engine imports inside `run()`,
  options as a pydantic model with a `title` on every field, progress through `ctx.progress()`.
- **TypeScript** strict mode, React function components, types generated from
  `docs/api/openapi.json` (never hand-written API types), ESLint + Prettier defaults.
- Error codes are UPPER_SNAKE and documented in the contract; user-facing messages are plain
  language without engine jargon.
- Comments explain *why*, not *what*. No commented-out code.

## 14. Testing strategy

| Level | Tool | What | Where |
|---|---|---|---|
| Registry rules | pytest | Generic tests over every tool: imports without engines, unique ids, schema subset, examples validate | `tests/unit/` |
| Tool | pytest, inside the group's worker image | Each tool on real corpus files: happy path, options, one bad file | `tests/tools/` |
| API | pytest + httpx | Upload caps, sniffing, auth (sign-in server mocked for every branch, plus `fxt_` tokens and startup refusals), ownership route walk, download links, response headers, secrets absent, log hygiene, errors, retention | `tests/api/` |
| Queue and runner | pytest + real Redis | Hand-off, redelivery cap, cancel, `QUEUE_FULL`, heavy-step slot, Redis ACLs, seccomp filter and escaped-process sweep, secrets via stdin | `tests/integration/` |
| Untrusted input | pytest, inside each worker image | Archive bombs and traversal, pixel and page limits, local-file-inclusion corpus per engine (runs on every engine upgrade) | `tests/integration/`, `tests/corpus/` |
| Planner | pytest | Golden routes for popular pairs; route changes show as diffs | `tests/unit/` |
| E2E | Playwright + axe | Sign-in, catalog search, merge, organize, compress before/after, sign, convert (including an ebook route), OCR, batch | `e2e/` |
| Benchmarks | `scripts/bench/` | Section 5.1 targets, compression ratio, OCR accuracy | `docs/benchmarks/` |

Coverage is measured but not gated by a percentage; every tool must have its tool test and
bad-file case (enforced by a generic test that fails when a registered tool has no test file).

## 15. Boundaries

- **Always:** validate and sniff every input; keep engines in workers with limits; add a corpus
  sample and a tool test with every tool; regenerate `openapi.json` and `tools.md` with every
  contract change; get a security review for anything touching files, auth or crypto; keep the
  spec in step with scope changes.
- **Ask first (maintainers, through an issue or ADR):** a new worker group or compose profile;
  giving any container network egress; adding a heavy dependency (> 50 MB) to `core`; changing
  retention, upload caps or auth behaviour; adding sign-in server tables; renaming a tool id;
  adding a paid or cloud service (the answer is no).
- **Never:** send user files or text to any third-party service; store files in the sign-in
  server's storage; put the sign-in server's JWT secret or service-role key in FileX; log or return passwords, passphrases or
  engine stderr; accept a token from a cookie, query string or form field; give any step a network
  socket (the `ai` relay is the only path to Ollama); render an output on the FileX origin; ship
  assets from a CDN; skip or delete failing tests to get green.

## 16. Open items

### Architecture items: all resolved (2026-10-10)

| Item | Resolution | Where |
|---|---|---|
| A1 Container split | Six groups `core`, `office`, `ocr`, `ebook`, `ml`, `ai`; no Chromium / `html` group; Pandoc only in `ebook`, WeasyPrint in `office` (R2); veraPDF in the CI test image (R1) and `ocr` (R2) | [ADR-0002](adr/0002-worker-groups-and-compose-profiles.md) amendment; sections 6.3, 6.4 |
| A2 Defaults | Retention 24 h (capped at 1440 min), uploads 1 GB, `FILEX_MIN_FREE_MB=4096`, install guide asks for 20 GB free | [ADR-0008](adr/0008-storage-and-retention.md) amendment; section 7.1 |
| A3 Memory budget | One heavy-step slot (`FILEX_HEAVY_SLOTS=1`), `FILEX_MEM_*` limits, `low-memory` preset | ADR-0002 amendment; section 5.1 |
| A4 Auxiliary inputs and secrets | Named `Aux` slots (sensitive ones deleted after their step); `SecretStr` options through `sec:<job>:<step>` and the child's stdin | [ADR-0003](adr/0003-tool-plugin-model.md) amendment; section 5.2 |
| A5 Job output as input | Multipart part `inputs` reserved, `422 NOT_SUPPORTED` in R1 | ADR-0003 amendment; section 8 |
| A6 Controlled egress | No egress in R1; a filtering egress proxy needs its own ADR before any feature that needs it | [ADR-0007](adr/0007-sandboxing-untrusted-files.md) "Later" |
| A7 Warnings and reports | `warnings[]` on the job, output `role` `RESULT` / `REPORT` | ADR-0003 amendment; section 7.1 |
| A8 Authenticated downloads | 5-minute HMAC links minted on click (accepted by security with conditions, review 5.1) | [ADR-0009](adr/0009-keel-auth.md); section 7.2 |
| A9 Runtime config | `GET /config` | ADR-0009; section 7.2 |
| A10 Superseding auth ADR | ADR-0009 supersedes ADR-0006 | ADR-0009 |

### Product decisions

Open:

- **O2. Sign-in server settings** (only for the optional sign-in setup of section 7.2; the C1
  items come from the security review). The full checklist is in
  [ADR-0009](adr/0009-keel-auth.md): sign-ups off, email confirmation on, minimum password length
  12, anonymous sign-ins off, session token lifetime 900 s, Site URL set to FileX's address,
  users invited and added to `FILEX_ALLOWED_USER_IDS` (or given `app_metadata.filex`).
- **O5. Tiny boxes.** On 2 GB machines only the core profile is supported.
  **Recommendation: yes, core-only on 2 GB.** Office, OCR and ebook each peak near 1-1.5 GB on
  their own; the install guide documents enabling one of them as "at your own risk" and the
  `low-memory` preset (A3) sets `FILEX_HEAVY_SLOTS=1`.

Resolved (2026-10-10, O6 on 2026-10-13):

- **O6. Licence.** *Decision:* AGPL-3.0. FileX uses AGPL engines (Ghostscript, PyMuPDF) and GPL
  engines (Calibre, Pandoc, djvulibre, Poppler); AGPL matches them, so the combined images raise
  no licence question, and it keeps network-served forks open.
- **O1. No auth configured.** *Decision:* FileX refuses to start when neither sign-in nor
  `FILEX_API_TOKENS` is configured, unless `FILEX_AUTH=none` is set explicitly (overrides the
  earlier "start with a warning"; ADR-0011 limits `none` to a loopback bind). Applied in 5.2, 7.2,
  9 and section 10.
- **O3. OCR languages.** *Decision:* ship `eng`, `rus`, `deu`, `fra`, `spa`, `ita`; `ukr` and `bel`
  documented as easy add-ons through `.env`. Applied in 6.5 and section 10.
- **O4. Signing.** *Decision:* accepted. R1 signatures are PAdES B-B without a trusted timestamp
  (timestamps in R2 after A6), and `cert.create` makes self-signed certificates; the private key is
  in the downloaded .p12 and is deleted from the server after 24 h like any result.
- **O7. R1 scope.** *Decision:* accepted (`cert.create`, `office.convert`, PDF-to-Excel/PowerPoint
  in R2), **except ebooks move into R1**. Five `ebook` tools added (6.4, 6.11); default profiles
  become `office,ocr,ebook`. R1 is now 31 tools.
- **Security review C1.** *Decision:* accepted. Port bound to `127.0.0.1` or a private network
  address by default; signed-in users also checked against `FILEX_ALLOWED_USER_IDS` or
  `app_metadata.filex`. Applied in 5.2, 7.2 and section 10.
- **Security review release gates** (section 4) and the security sign-off of ADR-0009/0010
  (section 5). *Decision:* adopted as R1 (and, for `ai`, R2) requirements. Applied in 5.2, 6, 7, 8,
  9, 10 and 15; each gate maps to a plan task.
