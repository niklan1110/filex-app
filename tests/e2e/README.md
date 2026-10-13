# E2E tests (Playwright + axe)

The browser tests live in `web/e2e/` (so they resolve `@playwright/test` from `web/node_modules`);
the config is `web/playwright.config.ts`. They drive the real web UI in headless Chromium against
a running FileX, plan task 12 plus the tools that exist now.

| File | What | Needs |
|---|---|---|
| `catalog.e2e.ts` | Catalog loads, search ("combine", "docx", "word to pdf"), `/` shortcut, no-results, category chips, light/dark theme, unknown tool, 360 px reflow | live stack |
| `tools.e2e.ts` | Keyboard-only merge → 13-page PDF; wrong file type (client and server); encrypted PDF; compress; compress outcome (−N %, sizes, bars) and PDF.js before/after with the slider and no CSP violations; split (zip + per-file names); `office.convert` docx → odt target picker; images → PDF; OCR text layer; Cyrillic watermark; My jobs | live stack with `core`, `office`, `ocr` profiles |
| `organize.e2e.ts` | `pdf.organize` page grid: keyboard reorder (announced, focus kept), turn, duplicate, delete, drag; the download's page order and rotations checked with pdf-lib; axe light and dark; 360 px reflow | live stack with `pdf.organize` (core) |
| `convert.e2e.ts` | Convert page against the live planner and runners: docx → pdf (office), md → epub (one markup.convert step, EPUB zip checked), md → pdf (two steps across the ebook worker) and My jobs, docx → txt (lossy step, text checked), live NO_ROUTE (epub → xlsx), popular conversions, axe light and dark, 360 px reflow, no CSP violations. **One case is intercepted**: NO_ROUTE naming an add-on (needs a profile that is off) | live stack with `core`, `office`, `ebook` |
| `more-tools.e2e.ts` | One short real job per remaining tool, download checked: rotate, extract-pages, remove-pages, page-numbers, metadata-edit, to-image (zip of PNGs), to-text, repair (broken xref), PDF/A (pdfaid XMP), encrypt → decrypt round trip, compress `targetMb` (Target reached / Best we could do), scan.clean, ebook.convert epub → mobi, djvu → pdf, cbz → pdf, pdf → cbz, office.to-pdf xlsx and pptx, cert.create → pdf.sign (aux certificate slot, `/ByteRange` + `/Sig` checked) | live stack with every profile |
| `a11y.e2e.ts` | axe (WCAG 2.0/2.1/2.2 A + AA) on the catalog (light and dark), two tool pages, a split result card, a compress result card (light and dark) and My jobs; fails on serious or critical violations | live stack |
| `auth-screens.e2e.ts` | KEEL and TOKEN sign-in screens, 401 → sign-in, 403 `USER_NOT_ALLOWED`, 503 `AUTH_UNAVAILABLE` + Try again, token accepted / rejected | only the UI being served; `/api/v1/*` and Keel are faked by route interception, no real Keel |
| `helpers.ts` | Corpus paths, job clean-up fixture, download helper, PDF page count and text-layer check (pdf-lib), reflow check, screenshots | |

Files are named `*.e2e.ts`, not `*.spec.ts`, so `npm test` (Vitest) never picks them up.

## Run

```bash
# 1. FileX running with sign-in off (the suite does not start or stop it):
#    .env: FILEX_AUTH=none, COMPOSE_PROFILES=core,office,ocr   then   docker compose up -d
# 2. Once per machine:
cd web
npm ci
npx playwright install chromium
# 3. The suite (one worker, headless, about 3-5 minutes; OCR and office jobs dominate):
npm run e2e
# Parts of it:
npx playwright test catalog
npx playwright test tools -g "merge"
npx playwright test auth-screens
```

Settings, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `FILEX_URL` | `http://127.0.0.1:8080` | Where FileX answers (UI and `/api/v1`) |
| `E2E_SCREENSHOTS` | unset | `1` writes the owner's screenshots to `docs/design/screenshots/` (viewport PNGs). Off by default so a normal run does not rewrite committed images |
| `CI` | unset | Adds a JUnit report at `web/test-results/junit.xml` and forbids `test.only` |

Failures keep a trace and a screenshot in `web/test-results/` (git-ignored): open a trace with
`npx playwright show-trace web/test-results/<test>/trace.zip`.

## Rules the suite keeps

- **Own jobs only, always deleted.** Flows with a result click **Delete now**; a fixture also
  deletes, through the API, every job the test created (seen as a 2xx answer to
  `POST /api/v1/tools/{id}/jobs`), even when the test fails halfway. In open mode every visitor is
  the same `anonymous` principal, so My jobs also shows other jobs on that server; the My jobs test
  checks the newest row, which is its own as long as nothing else runs jobs at the same time.
- **No sleeps.** Everything waits on locators and `expect`; job waits allow 120 s (OCR 180 s).
- **Light on the machine.** One worker, headless Chromium only, no video, no watch mode, no dev
  server started by the suite.
- **No third-party requests.** The Keel tests point `/config` at `https://keel.e2e.invalid` and
  abort every request to it.

## Known gaps (marked in the code)

- The compress outcome and before/after test needs a UI built after plan task 26. Until the image is
  rebuilt, build `web/dist` locally and serve it with the API's CSP, proxying `/api` to the stack,
  then run with `FILEX_URL` pointing at that server.
- Not covered yet (spec 14): sign-in against a real Keel,
  batch `N each` jobs, drag and drop (Playwright cannot drop OS files; the keyboard and "Choose
  files" paths are covered).
