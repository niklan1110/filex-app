# Implementation plan: FileX

Status: public roadmap and task list. Updated 2026-10-10 with decisions O1, O3, O4, O7, the
ADRs that resolve A1-A10, the security review's release gates (section 4) and its sign-off of
ADR-0009/0010 (section 5); updated 2026-10-13 for local mode (ADR-0011) and the deferred AI
profile (ADR-0010) and with every task's status checked against the code and tests. Spec:
[spec.md](spec.md).

Each task names its **Area** (backend, frontend, engines, ops, tests, design, benchmarks,
security, review, docs) so contributors can pick work in the part of the code they know.

## Status

Updated 2026-10-13. Of the 66 tasks: 58 done, 6 partly done, 1 in progress, 1 not started.
Each task's **Status** line is the source of truth; acceptance boxes are ticked only where each
check was confirmed on its own.

**Release 1: nearly complete.** Built and tested: 30 tools plus Convert (with the conversion
planner) and the page grid for `pdf.organize`; the per-step sandbox with seccomp and Landlock;
the local-file-inclusion suite; browser end-to-end tests (64 of 64 passing against the live
stack); in-image tests for all five images; the final security review (no open High findings);
the supply-chain gate (locked, hashed dependencies, digest-pinned images, a weekly image rebuild,
`pip-audit` and `npm audit`); Valkey as the queue store; local mode (ADR-0011); the AGPL-3.0
licence with third-party notices; the README and the install guide.

Left for release 1:

- Task 43: the weak-hardware benchmarks are running; results and limit tuning to follow.
- Task 66: the first GitHub run of the weekly image workflow is pending.
- Task 48: versioned images, a pull-only compose file, release and upgrade notes.
- Task 47: the API guide with curl examples.
- Smaller gaps: the 9-point position picker (17), one cross-tool broken-file suite (41), the
  written accessibility walkthrough (45), the whole-repo over-engineering review (46).
- Deployment checks from the final security review, on the target host: Landlock status, the
  worker gateway check, and the sign-in server settings when sign-in is used.

**Release 2 and release 3: not started.** The AI group ([ADR-0010](adr/0010-ai-worker-group.md))
is deferred and not on the current roadmap.

## Overview

Release 1 ships 31 tools on the `core`, `office`, `ocr` and `ebook` groups ([spec 6.11](spec.md#611-release-1-tool-list-31-tools)),
optional sign-in with an allowlist (local mode by default, ADR-0011), per-user jobs, 24 h retention and 1 GB uploads, on hardware down
to 4 GB RAM. Default profiles: `office,ocr,ebook`. Every "required before release 1" gate of the
security architecture review is covered by a task
([mapping](#security-review-gates-to-tasks)).

The order is: a **walking skeleton** first (compose + API + one worker + UI shell + `pdf.merge`
end to end), then **auth and platform limits** (needed before anyone else uses it), then
**tool batches** that can run in parallel (core tools and the ebook group), then the `office` and
`ocr` groups and `convert`, then **release hardening**. Every task leaves the stack runnable.

Task numbers are stable ids. Tasks added after the first draft get the next free number (49-52
for ebooks, 53-66 for the architecture and security changes of 2026-10-10) and sit in the phase
where they run, so existing references stay valid.

Tasks are tracked in this file. Tick boxes as tasks merge.

## Standing rules for every task

- One PR per task. The PR links the spec section it implements.
- Every PR gets a code review. Any PR touching uploads, engines, the runner, auth, secrets or
  crypto also gets a security review (marked **[sec]** below).
- A tool PR includes: the tool module, `tests/tools/test_<id>.py` on corpus files (happy path,
  options, one bad file), regenerated `docs/api/openapi.json` and `docs/tools.md`.
- Definition of done: tests pass in CI, lint clean, contract check clean, the stack still starts
  with `docker compose up`.
- Sizes: **S** 1-2 files, **M** 3-5 files. Nothing larger; split instead.
- Once task 53 merges, every tool test and every `examples` smoke test runs under the per-step
  seccomp filter; once task 64 merges, every engine's first tool PR adds that engine's
  local-file-inclusion case.

## Decisions this plan assumes

Architecture decisions (2026-10-10) that the tasks now follow; spec open items A1-A10 are closed:

| Decision | Source | Tasks affected |
|---|---|---|
| Six groups, no `html` group; Pandoc only in `ebook`; one multi-stage `docker/Dockerfile` | ADR-0002 amendment (A1) | 33, 38, 49 |
| Heavy-step slot `FILEX_HEAVY_SLOTS`, `FILEX_MEM_*` limits, `low-memory` preset | ADR-0002 amendment (A3) | 43, 55, 56 |
| `Aux` slots (sensitive ones deleted after the step); `SecretStr` via `sec:<job>:<step>`, `GETDEL` and stdin; `SECRET_UNAVAILABLE` | ADR-0003 amendment (A4, review H5, H8) | 16, 29, 30, 31, 57 |
| `inputs` part reserved, `422 NOT_SUPPORTED` in R1 | ADR-0003 amendment (A5) | 16 |
| `warnings[]` and output `role` | ADR-0003 amendment (A7) | 23, 25, 32, 36, 52, 58 |
| Static multi-step plans at job creation (`djvu.to-pdf` + OCR, `OCR_SKIPPED`); `LOSSY_STEP` | ADR-0005 amendment | 36, 52 |
| Seccomp per step, escaped-process sweep, Redis ACLs, id-derived paths, 128-bit ids, Appendix A engine settings | ADR-0007 amendment (accepted) | 5, 53, 54, 61, 63, 64 |
| Retention cap, Redis key table, per-owner index, log rules | ADR-0008 amendment (A2) | 15, 62, 65 |
| Optional sign-in + allowlist, hashed `fxt_` tokens, signed download links, `/config`, `/status` | ADR-0009 (accepted; A8, A9, A10) | 13, 14, 59, 60, 62 |
| `ai` group with a Unix-socket relay, text-only inputs | ADR-0010 (deferred) | R2 outline (deferred) |
| Local mode by default, `none` only on loopback, LAN access with `fxt_` tokens | ADR-0011 | 13, 47 |

Open product items that affect the plan, with the default the plan uses:

| Item | Default used by the plan | Tasks affected |
|---|---|---|
| O5 tiny boxes (open) | Recommended: core-only supported on 2 GB with the `low-memory` preset | 43, 47 |

Decisions already applied (resolved 2026-10-10; O6 on 2026-10-13):

| Decision | Tasks affected |
|---|---|
| O1: no auth configured → API refuses to start unless `FILEX_AUTH=none` | 13, 14 |
| O3: OCR languages `eng,rus,deu,fra,spa,ita`; `ukr`, `bel` as `.env` add-ons | 38, 47 |
| O4: PAdES B-B without timestamp and self-signed `cert.create` accepted | 31 |
| O7: R1 scope accepted, ebooks moved into R1; default profiles `office,ocr,ebook` | 36, 48, 49-52 |
| O6: licence AGPL-3.0 | 47, 48 |
| Review C1: bind `127.0.0.1` (or a private network IP) by default; signed-in user allowlist | 1, 13, 14, 47 |
| Review section 4 gates and section 5 sign-off adopted as release requirements | see [mapping](#security-review-gates-to-tasks) |

## Security review gates to tasks

Every item of the security review's section 4 "Required before release 1" and every condition
of its section 5 sign-off maps to a task. Every task listed here gets a security review (all
marked **[sec]**), and task 44 checks the whole table.

| Gate | Item | Covered by |
|---|---|---|
| 1 | C1: sign-in server sign-ups off, email confirmation, password length 12 | O2 (spec 16); install guide 47 |
| 1 | C1: allowlist, no unauthenticated mode, `TrustedHostMiddleware` | 13 |
| 1 | C1: port bound to `127.0.0.1` / private network IP | 1 (done) |
| 2 | C2 fix 1: outputs only as sandboxed attachments on `/result`, `/outputs/{n}`, `/dl` | 59 |
| 2 | C2 fix 2: strict app-shell CSP and headers, `FILEX_HSTS`, `https` sign-in server URL | 60 (startup check in 13) |
| 2 | C2 fix 3: React rules (`react/no-danger`, no `innerHTML`, no `href`/`src` from job data, previews only PDF.js or `<img>`) | 9, 10, 22, 26 |
| 2 | C2 fix 4: 900 s token lifetime; global sign-out | O2 + 47; 14 |
| 2 | C2 fix 5: `localStorage` token storage accepted for R1 | 14 (recorded) |
| 3 | H1 fixes 1-2: per-step seccomp, subreaper and escaped-process sweep | 53 |
| 3 | H1 fix 3: Redis ACLs | 54 |
| 3 | H1 fix 4: paths from job id and step index | 61 |
| 4 | H2 fix 1: input-only scratch dirs | 5 |
| 4 | H2 fix 2: engine settings (ADR-0007 Appendix A) | Each engine's first tool: 27, 30, 31, 34, 39, 40, 51, 52 |
| 4 | H2 fix 3: `defusedxml` | 63 |
| 4 | H2 fix 4: LFI corpus test | 64 |
| 5 | H3 fixes 1-4: archive checks, own unpack loop, RAR/7z post-check, pixel/page limits | 63 |
| 5 | H3 fix 5: runner checks on outputs, API `O_NOFOLLOW` | 61 |
| 6 | H4 fixes 1-2: Appendix A adopted, format allowlists | ADR-0007 (done); 2 (sniffing), 27, 51 |
| 6 | H4 fix 3: weekly rebuilds, digest pins, dependency audits | 66 |
| 6 | H4 fix 3: minimum engine versions in probes | 5 |
| 6 | H4 fix 4: mandatory per-tool limits | 2 (registry rule) |
| 7 | H5 all: secrets out of the job hash, `GETDEL`, stdin, tests | 16 (API side), 57 (runner side) |
| 8 | H6 fix 1: no network in any worker | 1 (done), 33, 38, 49 |
| 8 | H6 fix 2: Pandoc `--sandbox`; WeasyPrint restricted fetcher; no Chromium | 51; WeasyPrint arrives with R2 `html.to-pdf` |
| 9 | H7 all: `require_job` on every route, `own:*` keys, no cross-owner list | 62 |
| 9 | H7: 128-bit random ids, integer output indexes | 61 |
| 10 | H8 fixes 1-4: no server-held keys, per-job `.p12`, PAdES B-B only | 31, 57 |
| 11 | M1 all, with section 5.3 (sign-in server 403 → `401`; 429, 5xx, transport → `503`) | 13; `DELETE /session` from the UI in 14 |
| 11 | M2 all, with section 5.3 (entry format, 47-character minimum, never logged) | 13 |
| 11 | M3: no CORS, header-only credentials, `TrustedHostMiddleware` | 13 |
| 11 | M3 downloads: signed 5-minute links (accepted deviation, section 5.1) | 59 |
| 11 | M4: size counting, form limits, sniffing, stored names | 6, 2 |
| 11 | M5: download filenames, types, `no-store` | 59 |
| 12 | M6 fixes 1, 2, 4 (+3 if the check finds the host reachable) | 56 (1, 3, 4), 53 (2) |
| 13 | M7 fix 1: retention cap, "Delete now" | 15, 10 |
| 13 | M7 fix 2: log content | 65 |
| 13 | M7 fix 3: log rotation | 56 (verified on every service) |
| 13 | M7 fixes 4-5: snapshot exclusion, `unlink` note | 47 |
| 14 | M8 for R1 tools (`pdf.metadata-strip`) | 30 |
| 15 | L2: minimal `/health`, authenticated `/status` | 60 |
| 15 | L3: API and Redis hardened | 56 (containers), 54 (Redis ACL file, secret files) |
| 16 | Paperwork: ADR superseding 0006, ADR-0007 amendment and Appendix A, architecture section 9 | Done (ADR-0009, ADR-0007); compose catches up in 54 and 56 |
| 5.1 | Download links: one uvicorn process or key from a secret file; query strings out of logs; check order; same `404` counted by `rl:auth` | 59 |
| 5.2 | `ai`: relay instead of a seccomp exception; `ollama` hardening and version probe; size-capped text-only inputs; safe answer rendering | R2 outline item 2 (deferred) |

---

## Phase 0: Walking skeleton

Goal: `docker compose up`, open the UI, merge two PDFs, download the result.

### Task 1: Repo scaffold, base images and compose skeleton
- **Status:** **done**: `compose.yaml`, `docker/Dockerfile`, `.env.example`. The stack runs
  with `docker compose up` on a Docker host, the workers sit on an internal network with no
  egress, and the API binds `127.0.0.1` by default. Still to run on the target host: the LAN-bind
  check from a second machine. Follow-ups done in tasks 13, 54 and 56.
- **Area:** ops
- **Description:** `pyproject.toml`, empty `filex` package, one multi-stage `docker/Dockerfile`
  (stage `base`: Python 3.12 slim + `filex`; targets `api` and `worker-core`), `compose.yaml` with
  `api`, `redis` (password, no persistence), `worker-core`, the `internal` network
  (`internal: true`), the `filex-data` volume, the `x-hardening` block (non-root, read-only,
  `cap_drop`, limits), the API published as `"${FILEX_BIND:-127.0.0.1}:8080:8080"` (review C1),
  `.env.example`.
- **Acceptance:**
  - [ ] `docker compose up -d` starts all three services; `curl :8080/api/v1/health` returns `200`.
        *(needs the Docker run)*
  - [ ] `worker-core` cannot reach the internet (`curl` from inside fails). *(needs the Docker run)*
  - [ ] By default port 8080 listens on `127.0.0.1` only: `curl http://<host LAN IP>:8080` from
        another LAN machine fails; with `FILEX_BIND=<LAN or VPN IP>` it answers on that network.
        *(needs the Docker run)*
  - [x] `.gitignore`, `.env.example` with every variable documented (including `FILEX_BIND`).
- **Verify:** `docker compose up -d && curl -f localhost:8080/api/v1/health`; `docker compose exec worker-core python -c "import urllib.request as u; u.urlopen('https://example.com', timeout=5)"` fails; `docker compose port api 8080` prints `127.0.0.1:8080`.
- **Depends on:** none. **Size:** M. **[sec]**

### Task 2: Tool registry, formats and generic tool tests
- **Status:** **done**: `filex/registry.py`, `filex/formats.py`, `tests/unit/test_registry.py`,
  `tests/unit/test_formats.py`, `tests/unit/test_tools_generic.py`.
- **Area:** backend
- **Description:** `filex/registry.py` (`@tool`, `Files`, `Limits`, `Edge`, package scan),
  `filex/formats.py` (format ids, extensions, MIME, magic-byte sniffing), the generic tests from
  ADR-0003 (import without engines, unique ids, known groups/formats, schema subset, examples
  validate, every tool has a test file).
- **Acceptance:**
  - [ ] A dummy tool registers and appears in the registry with its JSON Schema.
  - [ ] Generic tests fail on a duplicate id, an unknown format, a field without `title`, and a
        module-level engine import.
  - [ ] Sniffing identifies pdf, png, jpg, tiff, heic, webp, docx, xlsx, pptx, odt, doc, xls, ppt
        from bytes, and rejects a `.pdf` that is really a zip.
- **Verify:** `pytest tests/unit -q`.
- **Depends on:** none (parallel with 1). **Size:** M.

### Task 3: Test corpus v0
- **Status:** **done**: `tests/corpus/README.md`, `scripts/make_corpus.py`.
- **Area:** tests
- **Description:** `tests/corpus/` with small, licence-clean samples: text PDF, scanned PDF,
  encrypted PDF (known password), broken PDF, PDF with forms, PDF/A, JPG/PNG/TIFF/HEIC, DOCX,
  XLSX, PPTX, ODT; a `README.md` with provenance; `scripts/make_corpus.py` that generates large
  files (500-page PDF, 1 GB file) on demand instead of committing them.
- **Acceptance:**
  - [x] Every file has a provenance and licence line in the README.
  - [x] `python scripts/make_corpus.py --large` produces the big files in a git-ignored folder.
- **Verify:** `python scripts/make_corpus.py --large && ls tests/corpus/generated`.
- **Depends on:** none (parallel). **Size:** M.

### Task 4: Visual direction and design tokens v0
- **Status:** **done**: `web/src/styles/tokens.css`, `docs/design.md`.
- **Area:** design
- **Description:** Visual direction for the shell (catalog, tool page, progress, result), design
  tokens as CSS custom properties (colour light/dark, type scale, spacing, radius, motion with
  reduced-motion variants), contrast checked to WCAG AA.
- **Acceptance:**
  - [x] `web/src/styles/tokens.css` and a short `docs/design.md` with rationale.
  - [x] All text/background token pairs ≥ 4.5:1; UI parts ≥ 3:1.
- **Verify:** contrast table in `docs/design.md`; frontend can import the tokens.
- **Depends on:** none (parallel). **Size:** S.

### Task 5: Worker runner
- **Status:** **done**: `filex/worker/runner.py`, `filex/worker/step.py`,
  `tests/integration/test_runner.py`.
- **Area:** backend
- **Description:** `filex/worker/`: consume `q:<group>` with a consumer group, run each step in a
  fresh child process (timeout, `RLIMIT_AS/CPU/FSIZE/NOFILE`, `RLIMIT_DATA` instead of `RLIMIT_AS`
  for Qt WebEngine steps, process-group kill), report progress into `job:<id>`, hand off to the
  next step's stream or finish, `XACK`+`XDEL`, `XAUTOCLAIM` with a delivery cap of 2
  (`WORKER_LOST`), heartbeat with engine probes, cancel within ~1 s, create streams on startup
  and on `NOGROUP`. Per ADR-0007 items 7 and 10: the step's **scratch dir holds a hard link of its
  input and nothing else** (review H2 fix 1), `cwd`, `HOME`, `TMPDIR` and engine config dirs point
  inside it, the child's environment is an allowlist (`PATH`, `LANG`, `HOME`, `TMPDIR`,
  `OMP_THREAD_LIMIT=1`, engine variables), the runner keeps `PR_SET_DUMPABLE=0`. Engine probes
  enforce each engine's **minimum version** and leave the tool out of the heartbeat below it
  (review H4 fix 3). The runner clamps a tool's `memory_mb` to its container's limit.
- **Acceptance:**
  - [ ] A dummy two-step job crosses two group streams and succeeds.
  - [ ] A step that exceeds its timeout or memory limit fails the job with the right code, and the
        worker keeps serving.
  - [ ] A worker killed mid-step: the step is redelivered once, a second loss gives `WORKER_LOST`.
  - [ ] Cancelling a running step kills its child within 2 s.
  - [ ] A dummy step lists its scratch dir and sees only its input; its environment holds only the
        allowlisted variables; a probe reporting a version below the minimum marks the tool
        unavailable.
- **Verify:** `pytest tests/integration/test_runner.py -q` against a real Redis.
- **Depends on:** 2. **Size:** M. **[sec]**

### Task 6: Job API
- **Status:** **done**: `filex/api/`, `tests/api/test_api_jobs.py`.
- **Area:** backend
- **Description:** `filex/api/`: `POST /tools/{id}/jobs` (streamed multipart to `/data/jobs/<id>/in`,
  `TMPDIR` on the volume, per-file and per-job byte counting with `FILEX_MAX_FILE_MB` and
  `FILEX_MAX_UPLOAD_MB=1024`, partial file deleted on `413`, form limits `max_files=50`,
  `max_fields=10`, `options` part ≤ 64 KB, sniffing against the tool's inputs, stored names
  `in/<n>.<ext of sniffed format>`, display name sanitised (NFC, no path parts or control
  characters, ≤ 200 characters), options validation), `GET /jobs/{id}` with `ETag`,
  `GET /jobs/{id}/result` and `/outputs/{n}` with `Range`, `DELETE /jobs/{id}`, `GET /tools`,
  `GET /tools/{id}`, `GET /formats`, `GET /health` returning only `{"status":"ok"}` (online groups
  go to `GET /status`, task 60), the error envelope, `warnings: []`, serving the built UI. The
  owner is a fixed `anonymous` until tasks 13 and 62; task 61 then replaces the job id generator
  and makes every path id-derived.
- **Acceptance:**
  - [ ] A 1 GB upload completes with API RSS ≤ 200 MB; 1 GB + 1 byte returns `413` and leaves no
        partial file; an 11th form field or a 65 KB `options` part is refused.
  - [ ] Wrong file type returns `422 INVALID_INPUT_FORMAT`; a tool whose group is offline returns
        `409 TOOL_UNAVAILABLE` naming the profile.
  - [ ] `GET /jobs/{id}` returns `304` when unchanged.
  - [ ] An upload named `../../etc/x.pdf` is stored as `in/0.pdf` and displayed as `x.pdf`.
- **Verify:** `pytest tests/api -q`; manual 1 GB upload while watching `docker stats`.
- **Depends on:** 1, 2, 5. **Size:** M. **[sec]**

### Task 7: First tool, `pdf.merge`
- **Status:** **done**: `filex/tools/organize/pdf_merge.py`, `tests/tools/test_pdf_merge.py`.
- **Area:** engines
- **Description:** `filex/tools/organize/pdf_merge.py` with pikepdf (options: add a bookmark per
  input file), plus its tool test.
- **Acceptance:**
  - [ ] Merging the corpus text PDF and scanned PDF gives a PDF with the summed page count and
        one bookmark per input when enabled.
  - [ ] The broken PDF fails with `INVALID_PDF`, not a crash.
- **Verify:** `pytest tests/tools/test_pdf_merge.py -q` inside the `worker-core` image.
- **Depends on:** 2, 3. **Size:** S.

### Task 8: Generated contract and tool docs
- **Status:** **done**: `scripts/gen_openapi.py`, `scripts/gen_tool_docs.py`,
  `tests/api/test_contract.py`.
- **Area:** backend
- **Description:** `scripts/gen_openapi.py` (deterministic ordering, `--check`),
  `docs/api/openapi.json`, `scripts/gen_tool_docs.py` → `docs/tools.md`.
- **Acceptance:**
  - [ ] Two runs produce byte-identical output; `--check` fails when the committed file is stale.
  - [ ] `pdf.merge` has its own typed options schema in the contract.
- **Verify:** `python scripts/gen_openapi.py --check`.
- **Depends on:** 6, 7. **Size:** S.

### Task 9: UI shell and tool catalog
- **Status:** **done**: `web/`, `web/src/pages/Catalog.tsx`, `web/src/lint.test.ts`.
- **Area:** frontend
- **Description:** Vite + React + TS app in `web/`, types generated from `docs/api/openapi.json`
  (`openapi-typescript`), API client, app layout with design tokens, catalog page from
  `GET /tools` grouped by category, search (name, synonyms, formats, `/` shortcut), unavailable
  tools greyed with the profile hint. Built assets copied into the API image. Lint config with
  `react/no-danger` (error) and a rule or review checklist item against `innerHTML` and against
  `href`/`src` built from job data other than FileX API paths; filenames and messages render as
  text (review C2 fix 3).
- **Acceptance:**
  - [ ] Catalog lists `pdf.merge`; typing "combine" or "pdf" finds it.
  - [ ] Initial JS ≤ 250 KB gzipped; no request leaves the FileX origin.
  - [ ] A component using `dangerouslySetInnerHTML` fails `npm --prefix web run lint`.
- **Verify:** `npm --prefix web run build && npm --prefix web run lint && npm --prefix web test`; open `localhost:8080`.
- **Depends on:** 4, 8. **Size:** M. **[sec]** (lint rules)

### Task 10: Run a tool from the UI
- **Status:** **done**: `web/src/pages/RunTool.tsx`, `web/src/components/OptionsForm.tsx`,
  `web/src/components/JobView.tsx`.
- **Area:** frontend
- **Description:** Tool page: drag-and-drop + "Choose files", accepted formats and size checks,
  reorderable file list for `N→1` tools, options form generated from the JSON Schema (enum, bool,
  number, string, page range), submit with upload progress, poll the job (1 s, back off when
  hidden), live-region announcements, result with download, expiry and "Delete now", error
  display, cancel. Result previews are PDF.js canvas renders or `<img>` only; outputs are never put
  in an `<iframe>`, `<object>` or `<embed>`. Downloads use a Bearer `fetch` until task 14 moves
  them to signed links.
- **Acceptance:**
  - [ ] Merge two PDFs from the browser with only the keyboard and download the result.
  - [ ] A wrong file type is refused before upload with a clear message.
- **Verify:** manual run against compose; component tests for the form renderer.
- **Depends on:** 6, 9. **Size:** M.

### Task 11: CI pipeline
- **Status:** **done**: `.github/workflows/ci.yml` and `images.yml`. Both run on demand rather than
  on every PR, a maintainer decision to save CI minutes.
- **Area:** ops
- **Description:** CI: ruff, pytest (unit, api, integration with Redis service), tool tests inside
  the worker images, `gen_openapi.py --check`, UI build and tests, image builds (`linux/amd64`),
  image vulnerability scan.
- **Acceptance:**
  - [ ] A PR with a stale `openapi.json` or a failing tool test is red.
- **Verify:** open a test PR with a deliberately stale contract.
- **Depends on:** 1, 8. **Size:** M.

### Task 12: E2E for the skeleton
- **Status:** **done**: `web/e2e/`, including the axe scans in `web/e2e/a11y.e2e.ts`.
- **Area:** tests
- **Description:** Playwright against `docker compose up`: merge happy path, wrong-file-type
  path, axe scan on catalog and tool page.
- **Acceptance:**
  - [ ] Both flows pass in CI; axe reports zero serious/critical violations.
- **Verify:** `npx playwright test` in CI.
- **Depends on:** 10, 11. **Size:** S.

### Checkpoint A: Walking skeleton
- [ ] `docker compose up` → open UI → merge two PDFs → download, from a clean clone.
- [ ] CI green, including E2E.
- [ ] A maintainer looks at it in the browser before Phase 1 starts.

---

## Phase 1: Auth and platform limits

### Task 13: Sign-in, API side
- **Status:** **done**: `filex/api/auth.py`, `tests/api/test_auth.py`; local mode added with
  ADR-0011.
- **Area:** backend (paired with the UI side, task 14). **[sec]**
- **Description:** The `require_principal` dependency of [ADR-0009](adr/0009-keel-auth.md),
  reading only the `Authorization: Bearer` header (no cookie, query string or form field; no CORS
  middleware). Values starting with `fxt_` are checked locally only; anything else goes to the sign-in
  server's `GET /auth/v1/user` with its anon key as `apikey` over one shared `httpx.AsyncClient`
  (TLS verification always on, 3 s connect / 3 s read), response shape validated. Branches:
  `200` → a user principal (ADR-0009); `5xx`, `429`, timeout, TLS or transport error →
  `503 AUTH_UNAVAILABLE` with `Retry-After: 10`, never cached; **every other status (including
  `403`) or a malformed body → `401 UNAUTHENTICATED`**; `is_anonymous: true` or not on the
  allowlist (`FILEX_ALLOWED_USER_IDS` or `app_metadata.filex` `"user"`/`"admin"`, never email or
  `user_metadata`) → `403 USER_NOT_ALLOWED`. Success-only cache keyed by `sha256(token)`, TTL
  `min(60 s, exp - now)`, LRU ~1000. `DELETE /session` evicts the caller's entry. Static tokens:
  `FILEX_API_TOKENS=name:<sha256 hex>,...`, `hmac.compare_digest` per hash, principal
  `token:<name>`, a presented `fxt_` value under 47 characters → `401` before hashing, tokens never
  logged. **Startup refusal** (non-zero exit naming the problem) when no auth is configured and
  `FILEX_AUTH` is not `none`, when a `FILEX_API_TOKENS` entry is not `<name>:<64 lowercase hex>`
  with a unique name matching `^[a-z0-9_-]{1,32}$`, and when the sign-in server URL is not `https`.
  `FILEX_AUTH=none`: principal `anonymous`, warning on every start, accepted only with a loopback
  `FILEX_BIND` (ADR-0011). Abuse limit: more than 20
  `401`s per client IP per minute → `429 TOO_MANY_REQUESTS` (`rl:auth:<ip>`), client IP from
  `X-Forwarded-For` only when the peer is in `FILEX_TRUSTED_PROXIES`. `TrustedHostMiddleware`
  with `FILEX_ALLOWED_HOSTS`. Task 1 follow-up: `.env.example` replaces `FILEX_API_TOKEN` with
  `FILEX_API_TOKENS` and documents how to make a token and its hash. Ownership is task 62,
  `/config` task 60, download links task 59. No JWT secret or service-role key anywhere.
- **Acceptance:**
  - [ ] Sign-in server mocked for every branch: `200` allowed → `200`; `401` → `401`; **`403` → `401`**;
        **`429` → `503`**; `5xx` → `503`; timeout and **transport error** → `503` with
        `Retry-After`; malformed body → `401`; no status falls through to a pass.
  - [ ] A valid signed-in user not in `FILEX_ALLOWED_USER_IDS` and without `app_metadata.filex` →
        `403 USER_NOT_ALLOWED`; `user_metadata.filex = "admin"` does not grant access;
        `is_anonymous: true` → `403`; `app_metadata.filex = "user"` → `200`.
  - [ ] A matching `fxt_` token → `200` with the sign-in server down; a wrong one → `401`; a 46-character `fxt_`
        value → `401` without hashing; a token never appears in captured logs.
  - [ ] The API exits non-zero at startup with no sign-in server and no tokens (naming the missing
        variables), with a **malformed or duplicate `FILEX_API_TOKENS` entry**, and with a
        **non-`https` sign-in server URL**; with `FILEX_AUTH=none` on loopback it starts and logs the
        warning, on a non-loopback bind it refuses to start.
  - [ ] The sign-in server is called at most once per token per 60 s; after `DELETE /session`
        the next request calls it again.
  - [ ] The 21st `401` from one IP within a minute gets `429`; `X-Forwarded-For` is ignored from an
        untrusted peer.
  - [ ] A request with a `Host` not in `FILEX_ALLOWED_HOSTS` gets `400`; a cross-origin preflight
        gets no CORS headers.
- **Verify:** `pytest tests/api/test_auth.py -q` (sign-in server mocked with `respx`); manual
  sign-in against a real Supabase-compatible server.
- **Depends on:** Checkpoint A. **Size:** M.

### Task 14: Sign-in, UI side
- **Status:** **done**: `web/src/auth/`, `web/src/pages/SignIn.tsx`, `web/src/pages/MyJobs.tsx`,
  `web/e2e/auth-screens.e2e.ts`.
- **Area:** frontend. **[sec]**
- **Description:** Read `GET /api/v1/config` before anything else; with a sign-in server
  configured, `createClient(<url>, <anon key>)` from `@supabase/supabase-js` v2 (default `localStorage`
  session storage, accepted for R1 in review 5.3); with `TOKEN`, a token field instead of
  email; with `NONE` (local mode), no sign-in screen. Email + password sign-in; set-password screen for
  `type=invite` links; forgot-password via the sign-in server's reset; sign-out calls `DELETE /api/v1/session`
  and then `signOut()` with the default `global` scope; Bearer token on every API call
  (auto-refresh); "Sign-in server unreachable" state on `503`; "Your account is not allowed to use
  FileX" state on `403 USER_NOT_ALLOWED`; "My jobs" page from `GET /jobs`. **Every download button**
  (single result, per-file, reports) mints a signed link on click and hands it to the browser
  (`<a download>` or `window.location`), never a `Blob` of a large file and never a URL built from
  job data other than the API's link. The `VITE_*` sign-in variables remain a dev-server fallback only.
- **Acceptance:**
  - [ ] An invited user opens the invite link, sets a password, lands in the catalog.
  - [ ] There is no sign-up form anywhere.
  - [ ] With the sign-in server stopped, the sign-in page shows the unreachable message.
  - [ ] A signed-in user who is not allowed sees the "not allowed" message and a sign-out button,
        not an empty catalog.
  - [ ] Sign-out sends `DELETE /session` before `signOut()`; a production build contains no
        sign-in server URL or anon key.
  - [ ] Downloading a result triggers one `POST .../link` and a browser download of the `/dl/...`
        URL, with no `Authorization` header on the download request.
- **Verify:** Playwright with the sign-in server mocked (route interception); manual run against a
  real one.
- **Depends on:** 13, 59, 60. **Size:** M.

### Task 15: Retention, janitor and back-pressure
- **Status:** **done**: `tests/integration/test_retention.py`, `tests/api/test_backpressure.py`.
- **Area:** backend
- **Description:** Delete `in/` and `steps/` on terminal state; outputs at `expiresAt`
  (`FILEX_RETENTION_MIN=1440`, config validation caps it at 1440, review M7 fix 1); janitor every
  60 s and at startup; `507` below `FILEX_MIN_FREE_MB` (default 4096); `429 QUEUE_FULL` at
  `FILEX_MAX_QUEUED`.
- **Acceptance:**
  - [ ] With retention set to 1 min in the test, outputs and the job hash are gone after expiry.
  - [ ] `FILEX_RETENTION_MIN=2000` is refused at startup (or clamped to 1440 with a log line).
  - [ ] Kill Redis mid-job: orphaned files are removed within one janitor pass.
- **Verify:** `pytest tests/integration/test_retention.py -q`.
- **Depends on:** 6. **Size:** S. **[sec]**

### Task 16: Auxiliary file inputs and secret options (registry and API side)
- **Status:** **done**: `Aux` and `Secret` in `filex/registry.py`, secret split in the job API;
  tests in `tests/unit/test_registry.py` and `tests/api/test_api_jobs.py`.
- **Area:** backend. **[sec]**
- **Description:** Per the [ADR-0003 amendment](adr/0003-tool-plugin-model.md): `Files(aux={...})`
  with `Aux(formats, required, sensitive, title)`; one multipart part per slot, sniffed like any
  input, stored as `in/aux/<slot>.<ext>`; unknown part names → `422`; the part name `inputs` →
  `422 NOT_SUPPORTED`; `Files(min=0, max=0)` allowed (`cert.create`); converters may not declare
  aux slots or secrets (generic test). `Secret(title=..., min_length=...)` makes a `SecretStr`
  field with `writeOnly: true` and `format: password` and no OpenAPI example. The API splits
  secrets out before anything is stored and writes them to `sec:<job_id>:<step>` (one JSON string,
  TTL = the 24 h queued-job cap); they never enter the job hash, a stream entry, a log line, an
  error message or `GET /jobs/{id}`. The runner side is task 57.
- **Acceptance:**
  - [ ] A dummy tool with a secret field and a sensitive aux slot: the job hash, stream entry,
        `GET /jobs/{id}`, error messages and captured logs hold no trace of the value; `sec:<id>:0`
        holds it.
  - [ ] Generic tests fail on a `SecretStr` field without `writeOnly` and on a converter with an
        aux slot; an `inputs` part gives `422 NOT_SUPPORTED`.
- **Verify:** `pytest tests/api/test_secrets.py tests/unit -q` (greps a Redis dump and captured logs).
- **Depends on:** 6. **Size:** M.

### Task 17: Form renderer additions
- **Status:** **partly done**. Password fields, the auxiliary file field and the `x-widget`
  mechanism are in `web/src/components/OptionsForm.tsx`; the 9-point position picker is not built
  yet, so stamp and watermark positions are a plain list.
- **Area:** frontend
- **Description:** Password field for secret options, auxiliary file field, widget-hint mechanism
  (`x-widget`), and the **position picker** widget (9-point grid, keyboard operable).
- **Acceptance:**
  - [ ] Position picker works with arrow keys and announces the selected position.
- **Verify:** component tests; axe on a demo page.
- **Depends on:** 10, 16. **Size:** M.

### Task 18: Batch UX
- **Status:** **done**: one job per batch with per-file and zip downloads in
  `web/src/components/JobView.tsx`; the split flow in `web/e2e/tools.e2e.ts` covers it.
- **Area:** frontend
- **Description:** Many files on an `N each` tool: one job, one result row per file, per-file and
  zip downloads, per-file errors.
- **Acceptance:**
  - [ ] Ten PDFs through a per-file tool give ten downloadable results and one zip.
- **Verify:** Playwright batch test (uses `pdf.rotate` once task 19 lands; a dummy tool before).
- **Depends on:** 10. **Size:** S.

### Task 19: Security review of the skeleton
- **Status:** **done**: the skeleton review ran and its fixes merged with tasks 54 and 56.
- **Area:** security
- **Description:** The architecture review is complete and folded into this plan
  ([gate mapping](#security-review-gates-to-tasks)). This task reviews what Phase 0 and Phase 1
  built against it: uploads and sniffing (6), runner sandbox (5, 53, 61), Redis ACLs (54),
  compose hardening (56), auth and ownership (13, 62), secrets (16, 57), download links and
  headers (59, 60), logs (65); plus the M6 gateway result from task 56.
- **Acceptance:**
  - [ ] Every gate row whose task has merged is confirmed or reopened as a fix.
- **Verify:** review notes recorded.
- **Depends on:** 13, 15, 16, 53, 54, 56, 57, 59, 60, 61, 62, 65. **Size:** S.

### Task 53: Per-step seccomp filter and escaped-process sweep
- **Status:** **done**: `filex/worker/sandbox.py`, `tests/integration/test_sandbox.py`,
  `tests/integration/test_examples.py`; Landlock added on top
  (`tests/integration/test_landlock.py`).
- **Area:** backend. **[sec]**
- **Description:** ADR-0007 items 1 and 2 (review H1 fixes 1-2, M6 fix 2). The step child installs
  a libseccomp filter (`python3-seccomp`, added to the `base` stage) before it runs the engine:
  deny `socket()` for `AF_INET`, `AF_INET6`, `AF_NETLINK`, `AF_PACKET` (keep `AF_UNIX`), deny
  `ptrace`, `process_vm_readv`, `process_vm_writev`, `keyctl`, `bpf`, `userfaultfd`,
  `perf_event_open`, `mount`. One filter for every group, no per-tool loosening without a security
  review. The runner sets `PR_SET_CHILD_SUBREAPER` and, after every step and every 5 s, kills any
  process whose session id is neither its own nor that of a running step. CI runs every
  registered tool's `examples` under the filter in its group's image.
- **Acceptance:**
  - [ ] A test step that connects to `redis:6379` or `api:8080` is denied (`EPERM` or killed) and
        the job fails cleanly; a step using an `AF_UNIX` socket works.
  - [ ] A test step that `setsid()`s and leaves a sleeping process behind: that process is gone
        within 5 s of the step ending.
  - [ ] The examples of every tool merged so far pass under the filter in CI.
- **Verify:** `pytest tests/integration/test_sandbox.py -q` inside the `worker-core` image.
- **Depends on:** 5. **Size:** M.

### Task 54: Redis ACL users `api` and `worker` with secret files
- **Status:** **done**: `docker/redis/`, `tests/integration/test_redis_acl.py`; the store is now
  Valkey, with the same ACL users.
- **Area:** ops (backend wires the clients). **[sec]**
- **Description:** ADR-0007 item 3 (review H1 fix 3, L3), replacing `requirepass` from task 1.
  An ACL file mounted read-only: default user off; user `api` on all FileX prefixes; user
  `worker` with `~q:* ~job:* ~worker:* ~slot:* &* -@all +@read +@write +@stream +@connection
  -@admin -@dangerous` plus the selector `(~sec:* +getdel)`. Passwords come from git-ignored
  Docker secret files, not `.env`; `FILEX_REDIS_PASSWORD` leaves `.env.example`. Redis keeps
  `--save ""`, `--appendonly no` and the protected-config defaults. The API and the runner connect
  as their own user.
- **Acceptance:**
  - [ ] As `worker`: `SET own:x y`, `ZADD ownidx:x 1 y`, `INCR rl:x`, `GET sec:x`, `CONFIG SET`
        and `MODULE LOAD` all fail; `GETDEL sec:x`, `XADD q:core`, `HSET job:x` work.
  - [ ] Connecting with no user or as `default` fails; `docker inspect` of every service shows
        no Redis password.
  - [ ] The skeleton's merge job still runs end to end.
- **Verify:** `pytest tests/integration/test_redis_acl.py -q`; `docker compose config` shows the
  secrets and the ACL file mount.
- **Depends on:** 5, 6. **Size:** M.

### Task 55: Heavy-step slot
- **Status:** **done**: in `filex/worker/runner.py`; tests in `tests/integration/test_runner.py`.
- **Area:** backend
- **Description:** ADR-0002 amendment. Before a heavy step's child starts (every group except
  `core`), the runner takes `slot:heavy:<i>` (`SET NX PX 30000`, `i < FILEX_HEAVY_SLOTS`), renews it
  every 10 s, and releases it with compare-and-delete. While waiting it keeps the stream entry
  with `XCLAIM ... JUSTID` (no delivery-count bump) and sets the step message "waiting for a free
  slot"; the job stays `QUEUED`. `FILEX_HEAVY_SLOTS=0` turns the slot off.
- **Acceptance:**
  - [ ] With `FILEX_HEAVY_SLOTS=1`, two heavy dummy steps in different groups never overlap; the
        waiting job shows the message and stays `QUEUED`; its delivery count is unchanged.
  - [ ] A runner killed while holding the slot frees it within 30 s.
  - [ ] With `FILEX_HEAVY_SLOTS=0` the two steps overlap; `core` steps never wait.
- **Verify:** `pytest tests/integration/test_heavy_slot.py -q` against a real Redis.
- **Depends on:** 5, 54. **Size:** S.

### Task 56: Compose follow-ups: memory limits, low-memory preset, API and Redis hardening, gateway check
- **Status:** **done**: `compose.yaml`, `.env.example`; the M6 gateway check
  (`scripts/check_gateway.sh`) ran on a Docker Desktop host and the internal network was pinned as a
  result.
- **Area:** ops. **[sec]**
- **Description:** Task 1 follow-ups. Each service's `mem_limit` and `memswap_limit` from
  `FILEX_MEM_CORE`, `FILEX_MEM_OFFICE`, `FILEX_MEM_OCR`, `FILEX_MEM_EBOOK` (defaults `1g`,
  `1536m`, `1536m`, `1g`); `FILEX_CONCURRENCY` and `FILEX_HEAVY_SLOTS=1` passed to every worker;
  `.env.example` gains these plus `FILEX_MAX_PIXELS`, `FILEX_MAX_PAGES`, `FILEX_TRUSTED_PROXIES`,
  `FILEX_HSTS`, and the `low-memory` preset as a commented block (ADR-0002 table). The
  `x-hardening` block on `api` and `redis` too, each with its own non-root uid, log rotation on
  every service (review L3, M7 fix 3). M6: from inside `worker-core`, as the runner, try TCP to the
  internal network's gateway and the host's LAN address on the host's open ports; record the result; if reachable, pin the subnet with `ipam` and document the host `INPUT` rule.
  `enable_ipv6: false` on `internal`.
- **Acceptance:**
  - [ ] `docker compose config` shows `mem_limit` = `memswap_limit` from `.env` on every service,
        the hardening keys on `api` and `redis`, and log rotation everywhere.
  - [ ] Uncommenting the `low-memory` block changes the limits without any other edit.
  - [ ] The M6 result is recorded on a real Docker host.
- **Verify:** `docker compose config`; `docker inspect` of each container; the M6 check script.
- **Depends on:** 1. **Size:** S.

### Task 57: Secrets and sensitive auxiliary files in the runner
- **Status:** **done**: `GETDEL` and stdin hand-off in `filex/worker/runner.py`; tests in
  `tests/integration/test_runner.py`.
- **Area:** backend. **[sec]**
- **Description:** Runner side of the ADR-0003 amendment (review H5, H8). The runner `GETDEL`s
  `sec:<job_id>:<step>` just before spawning the step and writes it to the child's stdin; the
  child hands secrets to engines in-process (pikepdf, pyHanko, cryptography) or through a pipe
  (`qpdf --password-file=-`), never argv or the environment. A redelivered step whose secret is
  gone fails with `SECRET_UNAVAILABLE`. Sensitive aux files (`.p12`) are deleted from `in/aux/` as
  soon as their step ends, whatever the outcome. When a job ends for any reason the API deletes any
  remaining `sec:<job_id>:*`. `ctx.aux[...]` gives the step a hard link in its scratch dir.
- **Acceptance:**
  - [ ] A dummy step reads its secret from stdin; during the step, `/proc/*/cmdline` and
        `/proc/*/environ` of the child hold no trace of it; after the job, Redis, logs, job JSON
        and errors hold none.
  - [ ] A worker killed after `GETDEL`: the redelivered step fails with `SECRET_UNAVAILABLE`.
  - [ ] A sensitive aux file is gone after a successful step and after a failed one.
- **Verify:** `pytest tests/integration/test_secrets_runner.py -q`.
- **Depends on:** 16, 54. **Size:** M.

### Task 58: Warnings and output roles
- **Status:** **done**: `ctx.warn` and `Report` in `filex/registry.py`; tests in
  `tests/api/test_api_jobs.py` and `tests/api/test_output_files.py`.
- **Area:** backend
- **Description:** ADR-0003 amendment (A7). `ctx.warn(code, message)` and `Report(path)` in the
  registry; the runner copies warnings from the child's result into the job hash (at most 20 per
  job, 300 characters each); job JSON `warnings: [{code, message, step}]` and output `role`
  (`RESULT`/`REPORT`); `GET /jobs/{id}/result` returns the single `RESULT` output or a zip of all
  `RESULT` outputs (names made unique, no directories); warning codes in the OpenAPI contract.
- **Acceptance:**
  - [ ] A dummy tool returning a result and a report: `/result` returns only the result,
        `/outputs/1` the report; the job lists both with their roles.
  - [ ] A 400-character warning is truncated to 300; the 21st warning is dropped.
- **Verify:** `pytest tests/api/test_warnings.py -q`; `python scripts/gen_openapi.py --check`.
- **Depends on:** 5, 6. **Size:** S.

### Task 59: Signed download links and output response headers
- **Status:** **done**: `tests/api/test_download_links.py`, `tests/api/test_output_headers.py`.
- **Area:** backend. **[sec]**
- **Description:** ADR-0009 "Downloads" with the conditions of review section 5.1, plus C2 fix 1
  and M5. `POST /jobs/{id}/result/link` and `/outputs/{n}/link` (through `require_job`) return
  `{url, expiresAt}`; `GET /dl/{jobId}/{target}?exp=&sig=` with `sig = HMAC-SHA256(key,
  "dl\n{jobId}\n{target}\n{exp}")`, `exp` at most 300 s ahead, `Range` supported. Check order: job
  id regex, `target` (`result` or an integer), `exp` integer with `now < exp <= now + 300`, then
  `compare_digest` on the HMAC, and only then Redis. **Every failure returns the same `404`** and
  counts toward `rl:auth:<ip>`. The key is 32 random bytes in memory; the API stays **one uvicorn
  process** (documented in the Dockerfile `CMD` and a startup check), and if that ever changes the
  key comes from a Docker secret file, never Redis or `.env`. An **access-log filter drops the
  query string on every route**. All three output routes (`/result`, `/outputs/{n}`, `/dl/...`)
  send `Content-Disposition: attachment` with the M5 name rules, `Content-Type` from FileX's
  format table (HTML, XHTML, SVG, XML as `application/octet-stream`), `X-Content-Type-Options:
  nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`, `Cache-Control: no-store`; job
  JSON also gets `no-store`.
- **Acceptance:**
  - [ ] A minted link downloads without a header within 5 minutes and resumes with `Range`; it
        fails after expiry, for another job or target, with a changed signature, an `exp` more than
        300 s ahead, and after the job is deleted, each time with the same `404` body, and each
        failure increments `rl:auth:<ip>`.
  - [ ] After a `/dl/...` download, `sig=` does not appear anywhere in the captured access log.
  - [ ] `/result`, `/outputs/{n}` and `/dl/...` all carry the five headers; an HTML output is
        served as `application/octet-stream`; a filename with `"`, CR/LF or `/` comes out
        sanitised with a UTF-8 `filename*`.
  - [ ] Starting the API with more than one uvicorn worker and no key secret file is refused.
- **Verify:** `pytest tests/api/test_download_links.py tests/api/test_output_headers.py -q`.
- **Depends on:** 6, 13, 62. **Size:** M.

### Task 60: `GET /config`, `GET /status`, minimal `/health` and app-shell headers
- **Status:** **done**: `tests/api/test_config_status.py`, `tests/api/test_shell_headers.py`.
- **Area:** backend. **[sec]**
- **Description:** ADR-0009 (A9), review L2 and C2 fix 2. `GET /config` (no auth, from env at
  request time): `authMode` (sign-in, `TOKEN`, `NONE`), sign-in server URL and anon key, limits, version.
  `GET /status` (authenticated): online groups, engine versions, `isolation: "container"`.
  `GET /health` stays `{"status":"ok"}` only. The app shell gets the strict CSP built at runtime
  with `connect-src 'self' <sign-in server origin>` (origin only), `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`, and `Strict-Transport-Security` only when `FILEX_HSTS=true`.
- **Acceptance:**
  - [ ] Unauthenticated `/health` body is exactly `{"status":"ok"}`; `/status` without a token is
        `401`, with one it lists groups and engine versions.
  - [ ] `/config` reflects `.env` changes after a restart without rebuilding the UI; `authMode` is
        `NONE` with `FILEX_AUTH=none` and `TOKEN` with static tokens only.
  - [ ] The app shell's CSP names only the sign-in server origin; HSTS is absent by default and present with
        `FILEX_HSTS=true`.
- **Verify:** `pytest tests/api/test_config_status.py tests/api/test_shell_headers.py -q`.
- **Depends on:** 6, 13. **Size:** S.

### Task 61: 128-bit job ids, id-derived paths and runner output checks
- **Status:** **done**: `tests/api/test_job_ids.py`, `tests/api/test_output_files.py`.
- **Area:** backend. **[sec]**
- **Description:** ADR-0007 items 4, 5 and 8 (review H1 fix 4, H3 fix 5, H7 fix 3-4). Job ids
  `"j_" + base64url(secrets.token_bytes(16))` without padding, checked against
  `^j_[A-Za-z0-9_-]{22}$` before any Redis or filesystem use; every path in the API and the runner
  is built from that id plus an integer step or output index, never from a path string in Redis or
  a stream entry; output indexes range-checked against the job's output count. The runner, not the
  child, `lstat`s each output (regular file, not a symlink, `st_nlink == 1`) and links it into
  `out/<index>.<ext>`; the API opens outputs with `O_NOFOLLOW` and checks `fstat` (regular,
  `st_nlink <= 2`).
- **Acceptance:**
  - [x] Ids match the regex; a malformed id gives `404` without touching Redis or `/data`.
  - [x] A stream entry carrying a forged path field is ignored; the step still uses its derived
        dirs.
  - [x] A step that leaves a symlink or a hard link to another job's file in its output dir fails
        the job; nothing lands in `out/`.
- **Verify:** `pytest tests/api/test_job_ids.py tests/api/test_output_files.py -q` and
  `pytest tests/integration/test_runner.py -q -k "path_fields or forged or linked"` (the symlink
  case needs a symlink privilege on Windows; it runs on Linux).
- **Depends on:** 5, 6. **Size:** M.

### Task 62: Job ownership: `own:<id>`, `require_job` and per-owner `GET /jobs`
- **Status:** **done**: `tests/api/test_ownership.py`.
- **Area:** backend. **[sec]**
- **Description:** ADR-0009 "Ownership" and the ADR-0008 key table (review H7). `own:<job_id>` →
  principal, set in the same `MULTI` that creates the job, with the job's TTL; `ownidx:<principal>`
  sorted set for `GET /jobs` (`limit` ≤ 100, `before` cursor, expired ids dropped on read). One
  dependency `require_job(job_id, principal)` on every route with `{jobId}` (`GET`, `DELETE`,
  `/result`, `/outputs/{n}`, both `/link` routes) answering `404 JOB_NOT_FOUND` for unknown and
  other principals' jobs. A generic test walks the route table and fails on a `{jobId}` route
  without it. No cross-user list.
- **Acceptance:**
  - [ ] User A gets `404` on every route of user B's job; `GET /jobs` shows only A's jobs; jobs of
        `token:a` and `token:b` stay separate.
  - [ ] Adding a route with `{jobId}` and no `require_job` makes the route-walk test fail.
  - [ ] Overwriting the `owner` field inside `job:<id>` (as the worker user could) changes nothing.
- **Verify:** `pytest tests/api/test_ownership.py -q`.
- **Depends on:** 13, 54, 61. **Size:** M.

### Task 65: Log hygiene
- **Status:** **done**: `filex/logs.py`, `tests/api/test_logs.py`.
- **Area:** backend. **[sec]**
- **Description:** ADR-0008 amendment "Logs" (review M7 fix 2). JSON logs in the API and runner
  carrying job id, tool, principal, sizes, durations and error codes; never filenames, option
  values, tokens, signatures or email addresses. Engine stderr only at debug level, truncated to
  2 KB, never in an API response.
- **Acceptance:**
  - [x] A job with a distinctive filename, option value and password, submitted with an `fxt_`
        token, leaves none of them in the captured INFO-level logs of the API and the runner.
  - [x] A failing engine's stderr appears only at debug level and is cut at 2 KB.
- **Verify:** `pytest tests/api/test_logs.py -q` (API and in-process runner share the fixtures in
  `tests/api`). The format and the never-log list live in `filex/logs.py`.
- **Depends on:** 5, 6. **Size:** S.

### Task 66: Supply chain: weekly rebuilds, digest pins and dependency audits
- **Status:** **partly done**. Locked, hashed Python dependencies (`requirements/`,
  `scripts/lock_deps.sh`), digest-pinned base images, a weekly image rebuild, and `pip-audit` and
  `npm audit` are in `.github/workflows/images.yml`; the first GitHub run of the workflow is still
  pending.
- **Area:** ops. **[sec]**
- **Description:** Review H4 fix 3. Base images pinned by digest with Renovate or Dependabot
  bumps; a scheduled CI job rebuilds every image weekly from current packages and runs the image
  scan; `pip-audit` and `npm audit` against the lockfiles on every PR; Ghostscript, LibreOffice,
  Poppler, libvips and exiftool from the newest available release when Debian stable lags on a
  security fix (noted in the Dockerfile).
- **Acceptance:**
  - [ ] The weekly workflow exists and has run once green; a PR adding a dependency with a known
        CVE is red.
- **Verify:** CI run links in the PR.
- **Depends on:** 11. **Size:** S.

### Checkpoint B: Safe to share
- [ ] An invited user signs in from another device and merges PDFs; another user cannot see the job.
- [ ] Retention, caps and back-pressure tested; security findings closed or planned.
- [ ] Sandbox platform in place: seccomp and escaped-process sweep (53), Redis ACLs (54), heavy-step
      slot (55), secrets via stdin (57), signed links and attachment headers (59), minimal
      `/health` (60), 128-bit ids (61), `require_job` route walk (62).
- [ ] Task 1's Docker checks ticked on a real Docker host.

---

## Phase 2: Core tool batches

Tasks 20-32 and the ebook tasks 49-52 depend only on Checkpoint A (plus the noted tasks) and can
run in parallel. Area is engines unless noted. Each engine's first tool PR
applies that engine's ADR-0007 Appendix A settings and gets a security review.

### Task 20: Organize batch: `pdf.split`, `pdf.rotate`, `pdf.extract-pages`, `pdf.remove-pages`
- **Status:** **done**: `filex/tools/organize/`, `tests/tools/test_page_ranges.py` and one test file
  per tool.
- **Description:** Shared page-range parser (`1-3,5,8-`), four pikepdf tools.
- **Acceptance:** [ ] Page counts and orders match expectations on corpus files; invalid ranges
  give `422` with the bad token named.
- **Verify:** `pytest tests/tools -k "split or rotate or extract or remove" -q`.
- **Depends on:** Checkpoint A. **Size:** M.

### Task 21: `pdf.organize`
- **Status:** **done**: `filex/tools/organize/pdf_organize.py`, `tests/tools/test_pdf_organize.py`.
- **Description:** Takes a page operation list (order, rotate, delete, duplicate) and applies it.
- **Acceptance:** [ ] Reverse + delete + rotate on a 10-page file gives the expected pages.
- **Verify:** `pytest tests/tools/test_pdf_organize.py -q`.
- **Depends on:** 20. **Size:** S.

### Task 22: Page-grid widget
- **Status:** **done**: `web/src/components/PageGrid.tsx`, `web/e2e/organize.e2e.ts`.
- **Area:** frontend (design reviews interaction)
- **Description:** PDF.js (bundled, lazy-loaded) thumbnails; drag to reorder; keyboard
  select-and-move; rotate and delete per page; produces the `pdf.organize` operation list;
  virtualised for 500-page files.
- **Acceptance:** [ ] Keyboard-only reorder works and is announced; a 500-page file scrolls
  smoothly on a mid-range phone. [ ] Thumbnails are PDF.js canvas renders (or `<img>`), never an
  `<iframe>`, `<object>` or `<embed>`.
- **Verify:** Playwright organize flow + axe.
- **Depends on:** 17, 21. **Size:** M.

### Task 23: `pdf.compress` presets
- **Status:** **done**: `filex/tools/optimize/pdf_compress.py`, `tests/tools/test_pdf_compress.py`.
- **Description:** Ghostscript presets LOW/MEDIUM/HIGH/EXTREME, grayscale, DPI cap, pikepdf
  cleanup; keeps the original when the output is larger and adds a warning (`ctx.warn`).
- **Acceptance:** [ ] MEDIUM halves the scanned sample; text-only sample never grows.
- **Verify:** `pytest tests/tools/test_pdf_compress.py -q`.
- **Depends on:** Checkpoint A, 58. **Size:** S.

### Task 24: Compression baseline
- **Status:** **done**: results in
  [benchmarks/compress-baseline.md](benchmarks/compress-baseline.md), script
  `scripts/bench/compress_baseline.py`.
- **Area:** benchmarks
- **Description:** Ghostscript presets vs OCRmyPDF `-O3`-style (pikepdf + pngquant + jbig2) on
  the corpus: size, visual quality (SSIM), time. Recommends preset parameters.
- **Acceptance:** [ ] `docs/benchmarks/compress-baseline.md` with a table and recommended values.
- **Verify:** script in `scripts/bench/` reproduces the table.
- **Depends on:** 23. **Size:** S.

### Task 25: `pdf.compress` target-size mode
- **Status:** **done**: target-size mode in `filex/tools/optimize/pdf_compress.py`; E2E cases in
  `web/e2e/more-tools.e2e.ts`.
- **Description:** Search over DPI/quality settings (bounded number of tries) until under the
  target; otherwise return the smallest result with a `TARGET_NOT_REACHED` warning ("best we could
  do").
- **Acceptance:** [ ] Target 2 MB on the scanned sample is reached or the warning names the
  smallest size; never more than 6 Ghostscript passes.
- **Verify:** `pytest tests/tools/test_pdf_compress.py -k target -q`.
- **Depends on:** 23, 24, 58. **Size:** S.

### Task 26: Before/after preview component
- **Status:** **done**: `web/src/components/BeforeAfter.tsx`, `web/src/components/Outcome.tsx`.
- **Area:** frontend (design for layout)
- **Description:** Side-by-side (slider on mobile) render of the same page from input and output,
  sizes, percentage saved, target-size outcome, text alternative. Reused by `scan.clean`.
- **Acceptance:** [ ] Works for compress results; screen reader reads sizes and percentage.
  [ ] Renders only through PDF.js canvas or `<img>`; the output's bytes come from a signed link or a
  Bearer `fetch`, never an `<iframe>`, `<object>` or `<embed>`.
- **Verify:** Playwright compress flow + axe.
- **Depends on:** 10, 23. **Size:** M.

### Task 27: `pdf.repair` and `pdf.to-pdfa`
- **Status:** **done**: `filex/tools/optimize/pdf_repair.py`, `pdf_to_pdfa.py`,
  `filex/tools/_gs.py`.
- **Description:** qpdf repair with Ghostscript re-write fallback; Ghostscript PDF/A-1b/2b/3b.
  CI test image includes veraPDF to validate outputs (not shipped in `core`). Ghostscript per
  ADR-0007 Appendix A: `-dSAFER -dBATCH -dNOPAUSE -dQUIET`, `--permit-file-read=<input path>`,
  output only into the scratch dir, called only on files starting with `%PDF-`, minimum version
  10.03.1 in the engine probe.
- **Acceptance:** [ ] Broken corpus PDF opens after repair; PDF/A-2b output passes veraPDF.
  [ ] A PostScript file renamed `.pdf` never reaches Ghostscript; the Ghostscript LFI case from
  task 64 passes.
- **Verify:** `pytest tests/tools -k "repair or pdfa" -q`.
- **Depends on:** Checkpoint A, 64. **Size:** M. **[sec]** (Ghostscript flags)

### Task 28: `image.to-pdf`, `pdf.to-image`, `pdf.to-text`
- **Status:** **done**: `filex/tools/convert/`, one test file per tool.
- **Description:** img2pdf (+ Pillow/pillow-heif for HEIC and alpha flattening), page size/fit,
  combine or one-per-image; pypdfium2 render at DPI to PNG/JPEG/WebP/TIFF; text extraction plain
  or layout. All declare conversion edges for `convert`.
- **Acceptance:** [ ] HEIC and PNG-with-alpha convert; 300 dpi render of 10 pages zips correctly;
  text of the text sample matches a golden file.
- **Verify:** `pytest tests/tools -k "image or text" -q`.
- **Depends on:** Checkpoint A. **Size:** M.

### Task 29: `pdf.watermark` and `pdf.page-numbers`
- **Status:** **done**: `filex/tools/edit/pdf_watermark.py`, `pdf_page_numbers.py`.
- **Description:** reportlab overlay merged with pikepdf; text or image watermark (auxiliary
  `stamp` slot, not sensitive), opacity, rotation, position or tiled, pages; page-number
  templates.
- **Acceptance:** [ ] Watermark text is found by text extraction on every selected page; numbers
  respect start and page selection.
- **Verify:** `pytest tests/tools -k "watermark or page_numbers" -q`.
- **Depends on:** 16. **Size:** M.

### Task 30: `pdf.metadata-edit`, `pdf.metadata-strip`, `pdf.encrypt`, `pdf.decrypt`
- **Status:** **done**: `filex/tools/edit/pdf_metadata_edit.py`,
  `filex/tools/security/pdf_metadata_strip.py`, `pdf_encrypt.py`, `pdf_decrypt.py`.
- **Description:** pikepdf Info + XMP edit; `pdf.metadata-strip` per review M8: remove Info, XMP,
  document ID, embedded files, JavaScript, document-level actions and page piece info, save a full
  rewrite (no incremental update), re-open the output and fail the job if any stripped field is
  still present; help text says what is and is not removed. AES-256 encrypt with permissions;
  decrypt with password. Passwords are `Secret` options read from stdin (task 57) and passed
  in-process, never on a command line.
- **Acceptance:** [ ] After strip, exiftool and pikepdf find no author/producer/XMP, embedded file
  or JavaScript on the corpus file with known hidden data; a strip that leaves a field fails the
  job. [ ] Encrypted output needs the password; wrong password gives `WRONG_PASSWORD`; passwords
  never logged or in `/proc/*/cmdline`.
- **Verify:** `pytest tests/tools -k "metadata or crypt" -q`.
- **Depends on:** 16, 57. **Size:** M. **[sec]**

### Task 31: `pdf.sign` and `cert.create`
- **Status:** **done**: `filex/tools/security/pdf_sign.py`, `cert_create.py`; E2E sign flow in
  `web/e2e/more-tools.e2e.ts`.
- **Description:** Review H8 fixes 1-4. pyHanko PAdES B-B in-process with an uploaded PKCS#12 in
  the sensitive `certificate` slot (deleted when the step ends) and a secret passphrase from stdin,
  invisible or visible stamp (position picker, optional `stamp` image), reason, location; no
  timestamp or OCSP fetching. `cert.create` generates the key inside the step and returns it only
  as a `.p12` encrypted with `BestAvailableEncryption` under a passphrase of at least 12 characters
  (name, email, validity). The sign tool's help says a `.p12` is readable by a concurrent
  compromised step while it exists. FileX holds no keys.
- **Acceptance:** [ ] A file signed with a `cert.create` certificate validates with pyHanko as
  intact; a wrong passphrase gives `WRONG_PASSPHRASE`; the passphrase and key never reach Redis or
  logs. [ ] An 11-character `cert.create` passphrase is refused with `422`; the `.p12` is not on
  `/data` after the sign step; the generated `.p12` does not use RC2 or 3DES.
- **Verify:** `pytest tests/tools -k "sign or cert" -q`.
- **Depends on:** 16, 17, 57. **Size:** M. **[sec]**

### Task 32: `pdf.to-docx` fast mode
- **Status:** **done**: `filex/tools/convert/pdf_to_docx.py`, `tests/tools/test_pdf_to_docx.py`.
- **Description:** PyMuPDF (text blocks, images, `find_tables`) written with python-docx, declared
  as a lossy edge; warnings for scans (`NO_TEXT`), columns (`LAYOUT_SIMPLIFIED`) and images left
  out (`IMAGES_SKIPPED`). pdf2docx was measured and dropped: unmaintained, and it pulls OpenCV and
  NumPy into the core image (benchmark not written yet).
- **Acceptance:** [ ] Text sample converts and opens in LibreOffice (checked in CI when the office
  image exists, else by python-docx); a 100-page file finishes within its timeout.
- **Verify:** `pytest tests/tools/test_pdf_to_docx.py -q`.
- **Depends on:** Checkpoint A, 58. **Size:** S.

### Task 49: `worker-ebook` image and profile
- **Status:** **done**: `worker-ebook` target in `docker/Dockerfile`, profile `ebook` in
  `compose.yaml`.
- **Area:** ops
- **Description:** A `worker-ebook` target in `docker/Dockerfile` on the `base` stage: Calibre (`ebook-convert`,
  headless with `QT_QPA_PLATFORM=offscreen`), Pandoc, djvulibre (`ddjvu`), `unar` or `7zip` 23+
  (RAR5), img2pdf, pypdfium2, Noto fonts with Cyrillic coverage for PDF output. Compose service in
  profile `ebook` (and `all`), same hardening as the other workers (no egress, non-root, read-only
  root FS with a writable scratch dir, `cap_drop: ALL`), `mem_limit` from `FILEX_MEM_EBOOK`
  (task 56), engine probes with minimum versions, image size recorded. Add `ebook` to
  `COMPOSE_PROFILES` in `.env.example`.
- **Acceptance:**
  - [ ] `COMPOSE_PROFILES=ebook docker compose up -d` adds the worker and the authenticated
        `/status` lists `ebook` online with Calibre, Pandoc and djvulibre versions.
  - [ ] `ebook-convert --version`, `pandoc --version`, `ddjvu --help` and `unar -v` run as the
        non-root user; the worker cannot reach the internet.
  - [ ] Image size and idle RAM (≤ 150 MB, spec 5.1) noted in the PR.
- **Verify:** `docker compose --profile ebook up -d` and `GET /api/v1/status` with a token; the
  egress check from task 1 against `worker-ebook`.
- **Depends on:** Checkpoint A, 56. **Size:** S. **[sec]**

### Task 50: Ebook corpus samples
- **Status:** **done**: `tests/corpus/ebook/`, `tests/tools/ebook_samples.py`, bombs generated by
  `scripts/make_corpus.py`.
- **Area:** tests
- **Description:** Add licence-clean ebook and comic samples to `tests/corpus/` (public-domain
  texts, own scans): EPUB 2 and EPUB 3, FB2 in Cyrillic, MOBI, AZW3 (DRM-free), a DRM-protected
  stub that is detectable but not real content, DOCX, TXT (UTF-8 and CP1251), single-file HTML
  and HTML zip with an image, Markdown with an image, DJVU with and without a text layer, CBZ, CBR
  as RAR4 and RAR5. Bad files: EPUB zip bomb, EPUB and CBZ with `../` path traversal, CBR with a
  nested archive, truncated DJVU, HTML that references `file:///etc/passwd` and a remote URL.
- **Acceptance:**
  - [ ] Every new file has provenance and licence in the corpus README.
  - [ ] Bad files are generated by `scripts/make_corpus.py` where they are large or dangerous to
        commit (zip bomb).
- **Verify:** `python scripts/make_corpus.py --large && ls tests/corpus/ebook tests/corpus/generated`.
- **Depends on:** 3. **Size:** S.

### Task 63: Archive pre-check, safe unpack, pixel and page limits, `defusedxml`
- **Status:** **done**: `filex/tools/_archive.py`, `filex/tools/_xml.py`,
  `tests/unit/test_archive_checks.py`.
- **Area:** engines. **[sec]**
- **Description:** Review H3 fixes 1-4 and H2 fix 3, as one shared module used by every tool that
  opens an archive-based or image input. Pre-check in the worker step before anything unpacks or an
  engine opens the file (also for DOCX/XLSX/PPTX/ODT/EPUB that engines open themselves): entries ≤
  `FILEX_MAX_ARCHIVE_ENTRIES` (5000), unpacked size ≤ `FILEX_MAX_UNPACKED_MB` (1024) counted from
  bytes read, ratio ≤ 200:1 for entries over 1 MB, depth 1, names without NUL, control characters,
  absolute paths, drive letters or `..` after normalising `\`, no symlink or device entries. Own
  unpack loop with generated names, `O_CREAT | O_EXCL | O_NOFOLLOW`, never `extractall`. RAR and 7z:
  list first (`lsar -j` / `7z l -slt`), same checks, unpack into an empty dir, `lstat`-walk it.
  Image and page limits from the header before decoding: `FILEX_MAX_PIXELS` (100 MP, ≤ 500 frames),
  Pillow `MAX_IMAGE_PIXELS` with `DecompressionBombWarning` as an error, PDF raster DPI ≤ 600,
  pages ≤ `FILEX_MAX_PAGES` (2000). Our own XML parsing goes through `defusedxml` (a generic test
  fails on a direct `xml.etree`/`lxml` parse of input data).
- **Acceptance:**
  - [ ] Corpus bombs, traversal, symlink, nested-archive and oversized-image samples fail with
        `INVALID_INPUT` before any engine runs; a normal DOCX, EPUB, CBZ and CBR pass.
  - [ ] An XML file with an external entity is parsed without resolving it.
- **Verify:** `pytest tests/unit/test_archive_checks.py tests/integration/test_limits.py -q`.
- **Depends on:** 2, 5. **Size:** M.

### Task 64: Local-file-inclusion corpus and per-engine test
- **Status:** **done**: `tests/integration/test_lfi.py`, `tests/integration/lfi_corpus.py`.
- **Area:** tests. **[sec]**
- **Description:** Review H2 fix 4. For each engine (Ghostscript, LibreOffice, Calibre, Pandoc,
  pikepdf/PyMuPDF, OCRmyPDF; WeasyPrint when it lands in R2), one corpus file that references a host
  file (`/etc/hostname`) and one that references another job's path (linked images, external
  references, `WEBSERVICE`, `file:` URLs, CSS `url()`, XML entities). A test runs each through the
  engine's tools and passes only if no output contains either file's content. The test runs on
  every engine upgrade (task 66's weekly rebuild included). Core-engine cases come first; the
  Calibre and Pandoc cases join with the ebook samples of task 50.
- **Acceptance:**
  - [ ] Every R1 engine has its two cases, each with provenance in the corpus README; the test is
        wired into CI per worker image and fails on a planted leak.
- **Verify:** `pytest tests/integration/test_lfi.py -q` in each worker image.
- **Depends on:** 3 (ebook cases also 50). **Size:** M.

### Task 51: `ebook.convert` and `markup.convert`
- **Status:** **done**: `filex/tools/ebook/ebook_convert.py`, `markup_convert.py` and their tests.
- **Area:** engines
- **Description:** Format ids and magic-byte sniffing for epub (zip with `mimetype`), fb2 (XML
  root `FictionBook`, parsed with `defusedxml`), mobi/azw3 (PalmDB `BOOKMOBI`, KF8 detection), md, html, txt (text checks,
  extension as tie-breaker only). `ebook.convert` runs Calibre `ebook-convert` between epub, fb2,
  mobi, azw3, docx, pdf, txt, html with a hard timeout; detects DRM and fails with
  `DRM_PROTECTED`; HTML input only from the job's input dir, no remote fetch. Calibre per ADR-0007
  Appendix A: `ebook-convert` only, input allowlist of the R1 formats (no CHM, LIT, LRF, PDB, RB,
  SNB, TCR readers), `CALIBRE_CONFIG_DIRECTORY` / `_TEMP_DIR` / `_CACHE_DIRECTORY` in the scratch
  dir, `QT_QPA_PLATFORM=offscreen`, the Qt WebEngine flags, `RLIMIT_DATA` about 900 MB, timeout
  300 s; Calibre steps are heavy steps. `markup.convert`
  runs Pandoc with `--sandbox` between md, html, docx, epub, fb2 and to txt. Both declare their
  edges (PDF-source edges flagged lossy) so `convert` routes them; archive inputs pass the task 63
  pre-check before the engine sees them.
- **Acceptance:**
  - [ ] Every listed pair converts on the corpus samples; EPUB outputs pass `epubcheck` in the CI
        test image; the Cyrillic FB2 keeps its text through FB2→EPUB→PDF.
  - [ ] The DRM stub fails with `DRM_PROTECTED`; the zip bomb and traversal EPUBs fail with
        `INVALID_INPUT` before Calibre runs; the `file:///etc/passwd` HTML produces no passwd
        content in any output format.
  - [ ] A hung `ebook-convert` is killed at its timeout and the worker keeps serving.
  - [ ] The task 64 LFI cases for Calibre and Pandoc pass; both run under the seccomp filter.
- **Verify:** `pytest tests/tools -k "ebook_convert or markup_convert" -q` in the ebook image.
- **Depends on:** 49, 50, 63, 64. **Size:** M. **[sec]**

### Task 52: `djvu.to-pdf`, `comic.to-pdf` and `pdf.to-cbz`
- **Status:** **done**: `filex/tools/ebook/djvu_to_pdf.py`, `comic_to_pdf.py`, `pdf_to_cbz.py` and
  their tests.
- **Area:** engines
- **Description:** Sniffing for djvu (`AT&TFORM`), cbz (zip of images), cbr (RAR4/RAR5
  signatures). `djvu.to-pdf`: page count first (`djvused -e n`, reject above `FILEX_MAX_PAGES`),
  `ddjvu` in page batches with the size capped to `FILEX_MAX_PIXELS`; option `ocr` (`auto` default,
  or `never`). The steps are **planned at job creation** (ADR-0005 amendment) through a small
  registry hook for static multi-step tools (with a backend review): with `auto` and the `ocr`
  group online the job is `djvu → pdf` then `ocr.pdf` in skip-text mode; with `ocr` offline it is
  one step with an `OCR_SKIPPED` warning; no step appends steps at run time.
  `comic.to-pdf` unpacks CBZ/CBR with the task 63 pre-check and unpack loop, sorts pages in natural order and builds the PDF losslessly with
  img2pdf. `pdf.to-cbz` renders pages with pypdfium2 at a chosen DPI into a CBZ. All three declare
  edges for `convert`.
- **Acceptance:**
  - [ ] DJVU with a text layer gives a PDF with that text; without one and `ocr` online, the job
        has two steps from creation and the result is searchable (known phrase found); with `ocr`
        offline, the job has one step and an `OCR_SKIPPED` warning; a DJVU over `FILEX_MAX_PAGES`
        fails with `INVALID_INPUT`.
  - [ ] CBZ and CBR (RAR4 and RAR5) give PDFs with pages in the right order and pixel-identical
        images; traversal and nested-archive samples fail with `INVALID_INPUT`.
  - [ ] PDF → CBZ → PDF keeps page count.
- **Verify:** `pytest tests/tools -k "djvu or comic or cbz" -q` in the ebook image (OCR case in
  the integration suite with both workers).
- **Depends on:** 49, 50, 58, 63. **Size:** M. **[sec]**

### Checkpoint C: Core and ebook groups complete
- [ ] All 21 core tools pass their tool tests and are usable from the UI.
- [ ] The 5 ebook tools pass their tool tests in the ebook image and are usable from the UI.
- [ ] A maintainer tries compress (target size), organize, sign and an EPUB → MOBI conversion.
- [ ] Every core and ebook tool's examples pass under the seccomp filter (53); the LFI cases for
      Ghostscript, pikepdf/PyMuPDF, Calibre and Pandoc pass (64).

---

## Phase 3: Office group

### Task 33: `worker-office` image and profile
- **Status:** **done**: `worker-office` target in `docker/Dockerfile`, profile `office` in
  `compose.yaml`.
- **Area:** ops
- **Description:** LibreOffice headless + fonts (Noto, Liberation, Carlito, Caladea; CJK optional
  build arg) as the `worker-office` target in `docker/Dockerfile`, profile `office` (and `all`),
  `mem_limit` from `FILEX_MEM_OFFICE` (task 56), no JRE, extensions or script-provider packages,
  engine probe with minimum version, image size recorded.
- **Acceptance:** [ ] `COMPOSE_PROFILES=office docker compose up` adds the worker; office tools
  become available in the catalog; the worker cannot reach the internet; image size noted in the PR.
- **Verify:** `docker compose --profile office up -d` and `GET /api/v1/status` with a token.
- **Depends on:** Checkpoint A, 56. **Size:** S. **[sec]**

### Task 34: `office.to-pdf`
- **Status:** **done**: `filex/tools/office/office_to_pdf.py`, `filex/tools/office/_lo.py`.
- **Area:** engines
- **Description:** One `soffice --headless ... --convert-to pdf` per step with the profile copied
  from a read-only template (ADR-0007 Appendix A: macro security very high,
  `DisableMacrosExecution`, `BlockUntrustedRefererLinks`, `DisableActiveContent`, link updates
  never; key names checked against the installed version), no `--accept` listener, input forced
  with `--infilter`, hard timeout and kill of hung `soffice.bin`; options PDF/A, tagged PDF, page
  range. A heavy step.
- **Acceptance:** [ ] DOCX, XLSX, PPTX, ODT samples convert; a document with a macro does not run
  it; a hanging conversion is killed at the timeout. [ ] The task 64 LibreOffice LFI cases (linked
  image, `WEBSERVICE`, external reference to `/etc/hostname` and to another job's path) leak
  nothing; LibreOffice runs under the seccomp filter.
- **Verify:** `pytest tests/tools/test_office_to_pdf.py -q` in the office image.
- **Depends on:** 33, 64. **Size:** M. **[sec]**

### Task 35: `office.convert`
- **Status:** **done**: `filex/tools/office/office_convert.py`,
  `tests/tools/test_office_convert.py`.
- **Area:** engines
- **Description:** Office-to-office edges (documents, sheets, slides) via the same LibreOffice
  runner.
- **Acceptance:** [ ] docx→odt, xlsx→ods, xlsx→csv, pptx→odp round-trip samples open.
- **Verify:** `pytest tests/tools/test_office_convert.py -q`.
- **Depends on:** 34. **Size:** S.

---

## Phase 4: Convert

### Task 36: Conversion planner and `convert`
- **Status:** **done**: `filex/planner.py`, `tests/unit/test_planner.py`,
  `tests/api/test_api_plan.py`, `tests/api/test_api_convert.py`.
- **Area:** backend
- **Description:** `filex/planner.py` (Dijkstra per ADR-0005 over available edges, max 4 steps,
  deterministic ties), `GET /convert/plan`, `convert` tool that expands into multi-group steps at
  job creation, a `LOSSY_STEP` warning per lossy hop, `NO_ROUTE` naming the profile that would
  help, golden-route tests. Routes never need aux files or secrets (converters cannot declare
  them).
- **Acceptance:** [ ] Golden routes for docx→pdf, pdf→docx, xlsx→ods, pptx→pdf, jpg→pdf,
  pdf→png, docx→txt and the ebook routes docx→epub, epub→pdf, epub→mobi, fb2→azw3, md→epub,
  md→mobi (two steps: Pandoc then Calibre), html→epub, djvu→pdf, cbr→pdf, pdf→cbz; with `office`
  off, docx→pdf returns `NO_ROUTE` naming `office`; with `ebook` off, epub→mobi returns
  `NO_ROUTE` naming `ebook`; pptx→epub carries a `LOSSY_STEP` warning.
- **Verify:** `pytest tests/unit/test_planner.py tests/integration/test_convert.py -q`.
- **Depends on:** 28, 32, 35, 51, 52, 58. **Size:** M.

### Task 37: Convert UI and generated tiles
- **Status:** **done**: `web/src/pages/Convert.tsx`, popular conversions in the catalog,
  `web/e2e/convert.e2e.ts`.
- **Area:** frontend
- **Description:** From/to pickers (from detected from the dropped file), route preview with lossy
  warnings, step list in progress view, "X to Y" tiles generated from reachable pairs, searchable.
- **Acceptance:** [ ] Searching "word to pdf" finds the tile; the route preview appears before
  upload.
- **Verify:** Playwright convert flow.
- **Depends on:** 36. **Size:** M.

---

## Phase 5: OCR group

### Task 38: `worker-ocr` image and profile
- **Status:** **done**: `worker-ocr` target in `docker/Dockerfile`, profile `ocr` in `compose.yaml`.
- **Area:** ops
- **Description:** OCRmyPDF, Tesseract 5 with the default languages `eng`, `rus`, `deu`, `fra`,
  `spa`, `ita` (O3) baked in, extra languages through `FILEX_OCR_EXTRA_LANGS` in `.env` (build
  arg or a one-off fetch into a tessdata volume; never at job time), unpaper, jbig2enc
  (lossless), pngquant, OpenCV headless, `OMP_THREAD_LIMIT=1`, as the `worker-ocr` target in
  `docker/Dockerfile`, profile `ocr`, `mem_limit` from `FILEX_MEM_OCR` (task 56).
- **Acceptance:** [ ] `COMPOSE_PROFILES=ocr` makes OCR tools available; `tesseract --list-langs`
  shows `eng`, `rus`, `deu`, `fra`, `spa`, `ita`; following the documented steps with
  `FILEX_OCR_EXTRA_LANGS=ukr,bel` adds `ukr` and `bel`.
- **Verify:** `docker compose --profile ocr up -d`; `GET /api/v1/status` shows `ocr` online.
- **Depends on:** Checkpoint A, 56. **Size:** S. **[sec]**

### Task 39: `ocr.pdf`
- **Status:** **done**: `filex/tools/ocr/ocr_pdf.py`, `tests/tools/test_ocr_pdf.py`.
- **Area:** engines
- **Description:** OCRmyPDF with languages, skip/redo/force text, deskew, rotate pages, clean,
  PDF or PDF/A output, optional `.txt` sidecar (a `REPORT` output); accepts images. Appendix A:
  `--max-image-mpixels` from `FILEX_MAX_PIXELS`, `--skip-big`, `--tesseract-timeout`,
  `OMP_THREAD_LIMIT=1`. A heavy step.
- **Acceptance:** [ ] The scanned sample becomes searchable (known phrase found); a PDF that
  already has text is skipped by default with a warning.
- **Verify:** `pytest tests/tools/test_ocr_pdf.py -q` in the ocr image.
- **Depends on:** 38, 58. **Size:** S. **[sec]**

### Task 40: `scan.clean` (basic)
- **Status:** **done**: `filex/tools/ocr/scan_clean.py`, `tests/tools/test_scan_clean.py`.
- **Area:** engines
- **Description:** Deskew (OpenCV), unpaper clean and border removal (conservative presets),
  auto-rotate, blank-page removal, optional OCR; uses the before/after component.
- **Acceptance:** [ ] Skewed corpus scan comes out within 0.5° of straight; blank pages removed;
  no content cropped on the corpus samples.
- **Verify:** `pytest tests/tools/test_scan_clean.py -q`; visual check in the before/after view.
- **Depends on:** 26, 38, 63. **Size:** M. **[sec]** (unpaper, OpenCV, pixel limits)

### Checkpoint D: All 31 R1 tools
- [ ] Every R1 tool runs from the UI and curl with default profiles (`office,ocr,ebook`).
- [ ] Maintainer run-through on a weak home-server box.

---

## Phase 6: Release hardening

### Task 41: Broken-file and limits suite
- **Status:** **partly done**. Every tool test has a bad-file case, and archive, pixel, page and
  reference checks are tested (`tests/unit/test_archive_checks.py`, `tests/integration/test_lfi.py`,
  the E2E repair and encrypted-file flows); the single cross-tool suite
  `tests/integration/test_bad_files.py` is not written yet.
- **Area:** tests
- **Description:** Every R1 tool against broken, encrypted, empty, zero-page, mislabeled, huge
  and zip-bomb inputs (plus the ebook bad files from task 50: traversal, nested RAR, DRM stub,
  truncated DJVU, plus task 63's archive and pixel cases); asserts clean error codes and no worker
  crash loop.
- **Acceptance:** [ ] No case leaves a job stuck in `RUNNING` or a worker restarting in a loop.
- **Verify:** `pytest tests/integration/test_bad_files.py -q`.
- **Depends on:** Checkpoint D. **Size:** M.

### Task 42: E2E coverage for R1
- **Status:** **done**: `web/e2e/`; 64 of 64 browser tests pass against the live stack, with axe
  scans. CI runs them on demand (task 11).
- **Area:** tests
- **Description:** Playwright: sign-in and sign-out (mocked sign-in server, including a not-allowed user and
  `DELETE /session` before `signOut()`), download through a signed link, catalog
  search, organize, compress before/after, sign, convert (one office and one ebook route, e.g.
  epub→mobi), OCR, batch, my jobs, axe on every page.
- **Acceptance:** [ ] All flows green in CI; zero serious/critical axe violations.
- **Verify:** `npx playwright test`.
- **Depends on:** Checkpoint D. **Size:** M.

### Task 43: Weak-hardware benchmark and limit tuning
- **Status:** **in progress**. The weak-hardware benchmark scripts are running; results, the
  measured peaks and the limit tuning are not published yet.
- **Area:** benchmarks (ops applies the limits)
- **Description:** Measure spec 5.1 targets with `--cpus 2 --memory 4g` and default profiles
  `office,ocr,ebook`, and the 2 GB core-only case; replace ADR-0002's estimates with measured
  peaks; tune `FILEX_MEM_*`, the heavy-step slot and timeouts (including Calibre) and the values
  of the `low-memory` preset that task 56 shipped.
- **Acceptance:** [ ] `docs/benchmarks/r1.md` lists every target as met or missed; missed ones
  get a recorded decision. [ ] **Heavy slot:** with `FILEX_HEAVY_SLOTS=1`, an Office conversion, an
  OCR job and an EPUB conversion submitted together never overlap their heavy steps (step
  timestamps); with `0` they do. [ ] **Low-memory preset:** with the preset on `--memory 4g` that
  three-job mix finishes with no OOM kill, and on 2 GB core-only every core tool runs; measured
  peaks recorded next to the ADR-0002 estimates.
- **Verify:** benchmark scripts reproduce the numbers.
- **Depends on:** Checkpoint D, 55, 56. **Size:** M.

### Task 44: R1 security review and sign-off
- **Status:** **done**: the final security review found no open High findings; its Low fixes have
  merged. Its supply-chain finding is covered by task 66 (first scheduled run pending). Its other
  release conditions are deployment checks: Landlock status and the worker gateway check
  (`scripts/check_gateway.sh`) on the target host, and the sign-in server settings of spec O2 when
  sign-in is used.
- **Area:** security
- **Description:** Full review: uploads, sandbox, auth and ownership, signed links, secrets,
  signing keys, encryption, metadata strip, retention, logs; and every row of the
  [gate mapping](#security-review-gates-to-tasks), including the section 5 conditions.
- **Acceptance:** [ ] Written sign-off; every gate row closed; no open high
  findings.
- **Depends on:** 41, and every task in the gate mapping (5, 13-16, 27, 30, 31, 34, 51-66).
  **Size:** S.

### Task 45: Accessibility audit and polish
- **Status:** **partly done**. Axe scans in light and dark themes, 360 px layout checks and
  reduced-motion tokens are in the E2E suite and styles; the written keyboard and screen-reader
  walkthrough has not been done.
- **Area:** design (frontend fixes)
- **Description:** Keyboard and screen-reader walkthrough of the main flows, contrast in both
  themes, reduced motion, mobile at 360 px; polish pass on progress and result states.
- **Acceptance:** [ ] Walkthrough checklist passes; fixes merged.
- **Depends on:** 42. **Size:** M.

### Task 46: Over-engineering review
- **Status:** **not started**. Over-engineering has been checked in per-task code reviews only; the
  whole-repo audit has not run.
- **Area:** review
- **Description:** Whole-repo audit; list what to cut before release.
- **Acceptance:** [ ] Findings fixed or explicitly kept with a reason.
- **Depends on:** Checkpoint D. **Size:** S.

### Task 47: README, install guide and API docs
- **Status:** **partly done**. The README and the install guide ([install.md](install.md)) are
  written, covering local mode, LAN access with `fxt_` tokens, the optional sign-in server and
  profiles; the API guide with curl examples is not written yet.
- **Area:** docs
- **Description:** README (what, quick start, licence, deletion is `unlink` and host disk
  encryption is the answer for more); install guide (local mode; LAN access with `FILEX_BIND` and `fxt_`
  tokens; an HTTPS reverse proxy, then `FILEX_HSTS=true`; the optional sign-in server checklist
  from spec O2 including sign-ups off,
  email confirmation, password length 12, 900 s session lifetime and Ban user; adding users to
  `FILEX_ALLOWED_USER_IDS`; making an `fxt_` token and putting its SHA-256 in `FILEX_API_TOKENS`,
  and rotating it; `FILEX_AUTH=none` and why it is loopback-only; profiles; adding OCR languages such as
  `ukr` and `bel`; `FILEX_HEAVY_SLOTS`, `FILEX_MEM_*`, the low-memory preset and 2 GB core-only;
  disk sizing (20 GB free); excluding `filex-data` from snapshots and backups; upgrades); API
  guide with curl examples, `fxt_` tokens, download with the Bearer header, warnings and output
  roles; link generated `docs/tools.md`.
- **Acceptance:** [ ] A person new to FileX gets from a clean machine to first merged PDF using only
  the guide.
- **Depends on:** 43. **Size:** M.

### Task 48: Release
- **Status:** **partly done**. The `LICENSE` file (AGPL-3.0-or-later), and third-party notices are
  in; versioned images, the pull-only compose file and release and upgrade notes are not.
- **Area:** ops
- **Description:** Versioned images (`ghcr.io/...:1.0.0`), a pull-only compose file, final
  `.env.example` (`COMPOSE_PROFILES=office,ocr,ebook`, `FILEX_BIND=127.0.0.1`,
  `FILEX_API_TOKENS`, `FILEX_HEAVY_SLOTS=1`, `FILEX_MEM_*`, the low-memory block, no secrets),
  Redis secret files created by a documented one-liner, health checks,
  release notes, upgrade notes, `LICENSE` file (AGPL-3.0, O6).
- **Acceptance:** [ ] Fresh install from the release compose file works on a clean Docker host.
- **Depends on:** 44, 45, 46, 47, 66. **Size:** S.

### Checkpoint E: R1 done
- [ ] Every R1 acceptance criterion in [spec section 10](spec.md#r1-core--office--ocr--ebook-31-tools) ticked.

---

## Dependency summary

```
Done: all but 17, 41, 45, 47, 48, 66 (partly done), 43 (in progress), 46 (not started)
1 ─┬─────────────► 6 ──► 8 ──► 9 ──► 10 ──► 12      (skeleton)
2 ─┼──► 5 ────────►┘     ▲                ▲
3 ─┴──► 7 ───────────────┘     4 ──► 9    11 ┘
Checkpoint A ──► 13 ─┬► 60 ──┐
             │       └► 62 ──┴► 59 ──► 14            (auth, ownership, links, config)
             ├─► 5 ──► 53;  5,6 ──► 54 ──► 55;  5,6 ──► 58, 61, 65;  54,61,13 ──► 62
             ├─► 1 ──► 56;  11 ──► 66
             ├─► 15;  16 ──► 57 (also 54);  16 ──► 17, 18;  all of these ──► 19
             ├─► 2,5 ──► 63;  3 ──► 64 (ebook cases + 50)
             ├─► 20 ──► 21 ──► 22;  23* ──► 24 ──► 25*;  26;  27 (64);  28;  29;  30 (57);  31 (57);  32*
             ├─► 56 ──► 49 ─┬► 51 (63, 64), 52* (63) ──┐   (ebook; 50 needs 3)
             │         50 ──┘                           │
             ├─► 56 ──► 33 ──► 34 (64) ──► 35 ──────────┤
             │                         28, 32 ──────────┴► 36* ──► 37
             └─► 56 ──► 38 ──► 39*;  38 + 26 + 63 ──► 40
Checkpoint D ──► 41 ──► 44 (all gate tasks);  42 ──► 45;  43 (55, 56) ──► 47;  46;  66 ──► 48
(* also needs 58)
```

Parallel lanes after Checkpoint A: backend (13, 15, 16, 53, 55, 57, 58, 59, 60, 61, 62, 65,
36), frontend (14, 17, 18, 22, 26, 37), engines (63, then 20-32 tools, 51-52, 34-35,
39-40), ops (54, 56, 66, then 49, 33, 38), tests (50, 64), benchmarks (24). Backend is the
bottleneck in Phase 1: 53, 61 and 58 can start right after 5 and 6, before auth.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Office + OCR + ebook peaks exceed 4 GB | High | Heavy-step slot (55), `FILEX_MEM_*` and the low-memory preset (56); measured in task 43 |
| An engine needs a syscall the seccomp filter denies | Med | Every tool's examples run under the filter in CI (53); a per-tool allowance only after a security review, never a looser global filter |
| Heavy-step slot makes a big OCR job delay an Office conversion | Low | Accepted on 4 GB boxes; big boxes set `FILEX_HEAVY_SLOTS=0`; "waiting for a free slot" shown in the UI |
| `ebook` in the default profiles adds ~500-800 MB of image download | Low | Image size recorded in task 49; install guide shows how to drop `ebook` from `COMPOSE_PROFILES` |
| Calibre and Pandoc parse untrusted ebooks and HTML (local file inclusion, zip bombs) | High | Pandoc `--sandbox`, input dir only, archive checks before the engine, bad-file corpus (50), security review on 49, 51, 52 |
| Valid sign-ins from strangers (the sign-in server is often internet-facing) | High | Allowlist check in task 13 (review C1), sign-ups off and email confirmation on the sign-in server (spec O2), port bound to `127.0.0.1` (task 1) |
| Sign-in server outage blocks all signed-in users | Med | Fail closed by design; static token for scripts; clear UI message |
| pdf2docx is unmaintained | Med | Pinned, labelled lossy, accurate mode in R2; fork if it breaks |
| 1 GB uploads over a VPN drop midway | Med | Clear error and retry; resumable uploads only if users hit it |
| Ghostscript/LibreOffice CVEs on untrusted files | High | Sandbox (ADR-0007), pinned images, CI image scan, prompt rebuilds |
| Signed links or ownership bugs leak files between users | High | `require_job` route walk (62), link tamper and expiry tests and one uvicorn process (59), security review in 19 and 44 |
| Backend load in Phase 1 (13 tasks) | Med | Tasks are S/M and mostly independent; 53, 58, 61, 65 start before auth; 54 and 56 are ops tasks |
| Tool count tempts scope creep in R1 | Med | R1 list frozen at 31; new ideas go to R2 in the spec |

---

## Release 2 outline: differentiators

**Status:** not started. Item 2 (AI profile, ADR-0010) is deferred.

Prerequisites: done for A1 (ADR-0002), A5 (ADR-0003, shape reserved) and the `ai` group
(ADR-0010, now deferred). Still needed: the egress ADR (A6)
before signing timestamps, a remote Ollama or URL to PDF.

1. **Ebook breadth** (the core ebook set is in R1): KEPUB, LIT, PDB, RTF, HTMLZ in
   `ebook.convert`; ODT, RST, LaTeX in `markup.convert`; CB7 input; `comic.to-epub`; new
   `convert` routes.
2. **AI profile, deferred** ([ADR-0010](adr/0010-ai-worker-group.md)); not on the current
   roadmap, kept as the design to follow if it is picked up again: `ollama` + `worker-ai` + `ollama-pull`;
   `ai.summarize`, `ai.chat` (index + follow-ups with citations), `ai.translate`,
   `ai.translate-text` (Argos). Acceptance checks from the security sign-off (review 5.2), across
   backend (relay, planner), ops (ollama) and frontend (rendering), with a security review:
   - [ ] **Unix-socket relay** in the `worker-ai` runner, `ai` steps under the standard seccomp
         filter: tests show only `POST /api/generate`, `/api/chat`, `/api/embed` pass (others
         `403`), request and response size caps hold, the step timeout cuts a slow answer, and
         `num_predict` and context size are forced whatever the step sends; the socket is unlinked
         after the step.
   - [ ] **`ollama` hardening:** non-root, read-only except `filex-models` and a tmpfs,
         `cap_drop: [ALL]`, no `ports:`, only on the `ai` network; minimum version enforced by the
         engine probe; in the weekly rebuild (66); `ollama-pull` only in profile `ai-pull`, never
         started by `--profile all`.
   - [ ] **Planner text-only rule:** AI tools receive only size-capped `text/plain` from a previous
         FileX step (`pdf.to-text`, Docling) or a UTF-8-decoded upload, enforced by the planner; a
         PDF sent straight to an AI tool is re-planned with an extraction step, never parsed in the
         `ai` step.
   - [ ] **Answers are untrusted:** saved as `text/plain` attachments; the UI renders them as text
         or as Markdown with raw HTML off and no images or links built from the output (test with
         an answer containing `<script>` and a Markdown image); the model gets no tools and its
         output never picks tools, options, paths or later steps.
3. **ML profile:** `pdf.to-docx` accurate mode, `pdf.to-markdown` accurate (Docling); fidelity
   benchmark first.
4. **Full scan cleanup:** `scan.enhance` (dewarp, whitening, MRC/JBIG2), tuned on the corpus.
5. **Trust tools:** `pdf.redact` (draw-boxes widget, search/regex, proof report),
   `pdf.sanitize`, `pdf.verify-signatures`, signing timestamps/LTV (if A6), `file.metadata-strip`.
6. **Compare:** `pdf.compare` (text + visual).
7. **Breadth in existing groups:** image tools (`image.convert/resize/compress`),
   `html.to-pdf` (WeasyPrint with the restricted fetcher, plus its LFI cases in task 64's test),
   the Markdown to PDF route (`md → html` in `ebook`, then `html → pdf` in `office`), `pdf.to-xlsx/pptx/html/markdown`,
   `pdf.extract-images`, `pdf.info`, cheap PDF tools (crop, nup, booklet, alternate-mix, halves,
   header-footer, Bates, flatten, linearize, grayscale, rasterize, remove-blank-pages,
   split-size), `pdf.pdfa-validate`.
8. **Per-user data on the sign-in server:** job history and saved presets, RLS by `auth.uid()`, first
   `supabase/migrations/`; "use this file in another tool".

R2 may split into R2a (ebook, trust tools, breadth) and R2b (ML, scan.enhance) if the ML work
runs long.

## Release 3 outline: automation and platform

**Status:** not started.

1. **Recipes** (chained tools with per-step options) from UI and API, stored per user on the sign-in server.
2. **Watch folders** on SMB/NFS mounts (polling) and **webhooks**.
3. **ARM64** images and CI.
4. Remaining R3 catalog: `pdf.redact-pii` (Presidio), `doc.safe-pdf`, `office.compare`,
   `ocr.accurate`, `image.upscale`, `image.remove-background`, `image.trace`, `pdf.einvoice`,
   `pdf.forms-fill`, `pdf.to-djvu`, `email.to-pdf`, `ebook.metadata`, `pdf.bookmarks`,
   `pdf.attachments`, `pdf.split-separator`, `pdf.to-json`, `pdf.adjust-colors`.

## Open questions

See [spec section 16](spec.md#16-open-items). Architecture items A1-A10 are resolved (ADRs 0002, 0003,
0005, 0007-0010). Product items: O2 (sign-in server checklist, including the 900 s session
lifetime) and O5 (open, recommendation: core-only on 2 GB); O1, O3, O4, O7, review C1 and the
review's release gates resolved on 2026-10-10; O6 (AGPL-3.0) on 2026-10-13.
