# FileX visual direction and design tokens v1: "Broadsheet"

Status: v1, replaces the v0 "calm workbench" direction (2026-10-10). Built into the web app from
plan Task 9 onwards.

- **Source:** design concept B, "Broadsheet", chosen from several visual concepts. Its HTML
  prototype is not published (it loaded React, Babel and Google Fonts from CDNs, so it was a
  picture only); its icon sprite ships as `web/src/assets/icons.svg` and its two sample pages as
  `docs/design/preview/page-before.svg` and `page-after.svg`, all without provenance metadata.
- **Tokens:** [`web/src/styles/tokens.css`](../web/src/styles/tokens.css) is the single source of
  truth. There is no JSON copy.
- **Living preview:** [`docs/design-preview.html`](design-preview.html). Open it from the repo; it
  links the real `tokens.css`, the self-hosted font and an inlined copy of the icon sprite. It shows
  the catalog, the tool page with every schema control, the drop overlay, My jobs (running, queued,
  list), the compression result, sign-in and the error states, and it computes contrast live for the
  theme on screen. `?theme=light` or `?theme=dark` forces a theme. It makes no network requests.
- **Spec inputs:** [spec 5.2](spec.md#52-security-basics) (CSP, safe rendering),
  [spec 5.3](spec.md#53-accessibility), [spec 9](spec.md#9-ui-requirements).

## 1. Direction

The catalog reads like a newspaper index. Each tool family is a full-width colour field with a
large, wide heading, so you find the section first and the tool second. Ink is the only action
colour, and category colour lives only in the fields and their folded corners. Type does the work
of hierarchy: Archivo at 125% width and weight 800 for headings, and outcomes like **−62%** set at
poster size.

**Signature detail: the folded corner.** Fields, the tool header, the drop card, the sign-in panel,
the logo mark and the drop icon are all "sheets" with their top-right corner cut and folded. It is
pure CSS (`clip-path` plus one gradient triangle), so it costs nothing to paint.

### Rules that follow from the direction

- **Search first.** The catalog opens with the big search box (`/` focuses it) and the category
  chips, then the fields. No hero.
- **Ink means action.** `--color-accent` is the text colour (near-black in light, near-white in
  dark). Primary buttons, selected options, pressed chips, the current theme, progress fills and the
  running badge all invert to ink. There is no brand hue.
- **Category colour only in fields.** It appears on the field background, its fold, the 12px swatch
  in the filter chip, and the tool-page header. Never on buttons, text or icons. Text on a field is
  always `--color-on-cat`; muted text never sits on a field.
- **Status colour only means status.** Green, amber and red always come with an icon and words.
- **Square.** 2px radius on controls, 0 on surfaces. The spinner is the only round thing.
- **No shadows.** 1px rules separate; a 2px ink rule opens a section (file list, options, each job,
  result actions). Depth comes from fields and ink, not elevation.
- **One primary action per view** ("Compress 3 files", then "Download (3.2 MB)").
- **No nested cards.** Tiles sit directly on a field; the tool page is two plain columns.

## 2. Tokens

### Colour

Light is the default. Dark applies when the OS asks for it and can be forced with
`<html data-theme="dark">` (or `"light"`). The theme switch has three states (System / Light / Dark)
and stores the choice in `localStorage` under `filex-theme` (wrapped in try/catch). Apply it from a
tiny inline script before first paint, or (with the strict CSP) from the first module before render.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--color-bg` | #f3f4f5 | #0e0f11 | Canvas |
| `--color-surface` | #ffffff | #17191c | Paper: tiles, inputs, drop zone |
| `--color-surface-2` | #e9ebee | #1f2226 | Hover, unavailable tile, unit suffix |
| `--color-surface-3` | #dcdfe3 | #2a2e33 | Progress track, queued badge, switch track |
| `--color-border` | #d3d6db | #2c3036 | Decorative 1px rules only |
| `--color-border-strong` | #6c737e | #757d89 | Control edges, dashed drop zone (≥ 3:1) |
| `--color-tile-border` | ink 16% | white 12% | Tile edge on a field (decorative) |
| `--color-text` | #111316 | #f1f2f4 | Ink |
| `--color-text-muted` | #484e57 | #a9b0ba | Help, summaries, meta |
| `--color-accent` / `-hover` / `-pressed` | #111316 / #2b2f35 / #3a3f46 | #f1f2f4 / #d9dce0 / #c4c8ce | Ink as action |
| `--color-on-accent` / `-muted` | #ffffff / #c9cdd3 | #0e0f11 / #3d424a | Text on ink; second line in a selected card |
| `--color-link`, `--color-focus` | ink | ink | Links are ink plus underline; focus ring is ink |
| `--color-{success,warning,danger}-{fg,bg}` | as v0 | as v0 | Status, always with icon and text |
| `--color-on-cat` | #111316 | #f1f2f4 | Text on every field |
| `--cat-organize` | #a8d5f2 | #123a57 | Sky |
| `--cat-optimize` | #a6dfb8 | #14412a | Green |
| `--cat-convert` | #f5c98a | #4d3210 | Amber |
| `--cat-ocr` | #9ddcd3 | #0f403b | Teal |
| `--cat-edit` | #f3b3d6 | #4b1b3b | Pink |
| `--cat-security` | #f5abae | #511d23 | Red |
| `--cat-ebook` | #c9bdf2 | #2e2752 | Violet (added, see section 5) |
| `--cat-compare` | #c5ccd6 | #2b3442 | Slate (added, R2) |
| `--cat-ai` | #d3e68f | #313d14 | Lime (added, R2) |

Fields are light tints with ink text in light mode and deep tones with paper text in dark mode. The
fold is `color-mix(in srgb, var(--cat-x) var(--fold-mix), #000)` with `--fold-mix: 70%`.

Values are Concept B's own, unchanged: the contrast script found **no failures** on them, so no
shade had to move. Values added for the app (not in the concept): `--color-accent-pressed`,
`--color-text-disabled`, `--color-danger` / `--color-on-danger` (destructive fill for confirm
dialogs; the concept has none), and the three extra category fields.

### Typography

**Archivo**, variable, one self-hosted Latin file with both axes kept: width 100–125 and weight
400–900. Body text is 16/24 at normal width, headings are 125% width at weight 800, and the outcome
numeral and sign-in statement are weight 900. The system monospace stack is used for sizes, counts,
codes, breadcrumbs and eyebrows.

| Token | Mobile | Desktop ≥ 64rem | Use |
|---|---|---|---|
| `--text-2xs` | 12/16 | same | Mono caps eyebrow, format badge |
| `--text-xs` | 13/18 | same | Mono meta, badges, breadcrumb |
| `--text-sm` | 14/20 | same | Help, tile summary, sizes |
| `--text-ui` | 15/22 | same | Chips, alerts, banner, conversion links |
| `--text-md` | 16/24 | same | Body, inputs |
| `--text-lg` | 17/24 | same | Tile title, primary button |
| `--text-lead` | 20/28 | same | Drop-zone line (112% width) |
| `--text-xl` | 22/28 | same | Section headings (wide, 800), job titles (700) |
| `--display-brand` | 24 | 24 | Header wordmark |
| `--display-statement` | 26 | 36 | "Queued, 2 jobs ahead", "No tools match …" |
| `--display-figure` | 26 | 36 | "8.4 MB → 3.2 MB" (112% width) |
| `--display-band` | 30 | 44 | Field heading |
| `--display-page` | 36 | 52 | "My jobs", "Sign in" |
| `--display-drop` | 36 | 48 | "Drop to add" |
| `--display-tool` | 38 | 64 | Tool title |
| `--display-wordmark` | 48 | 88 | "Your paper, your server." (900) |
| `--display-percent` | 72 | 104 | Job progress % |
| `--display-outcome` | 96 | 168 | Result "−62%" (900, line-height 0.85) |

- Tracking tightens as size grows: `-0.02em` section, `-0.025em` band, `-0.03em` titles, `-0.04em`
  percent, `-0.045em` outcome and wordmark. Uppercase mono eyebrows get `+0.06em`.
- Every number that updates or compares uses `font-variant-numeric: tabular-nums` (the `tnum`
  feature is kept in the subset). The outcome uses U+2212 "−", which is in the subset.
- Headings get `text-wrap: balance`, short body text `text-wrap: pretty`.
- Display sizes switch at one breakpoint (64rem). They are in rem, so they follow the user's text
  size, and at 200% zoom the viewport drops below 64rem and the mobile sizes apply.

### Space, size, shape, borders

- **Space:** 4px grid (`--space-1` 4 … `--space-16` 64) plus Concept B's own steps: `--space-1-5` 6,
  `--space-2-5` 10, `--space-3-5` 14, `--space-7` 28, `--space-14` 56.
- **Responsive layout tokens** (mobile / desktop): `--page-gutter` 16/32, `--page-top` 20/32,
  `--band-pad` 20·16·16 / 28, `--band-gap` 16/24, `--tool-head-pad`, `--tool-gap` 40/48,
  `--dropzone-min` 12rem/15rem.
- **Controls:** inputs, selects and nav items 44px (`--control-md`); secondary buttons 48px
  (`--control-lg`); the primary action 56px (`--control-xl`); search 60/68. Chips, the theme switch
  and file-row icon buttons are 44px, dropping to 40/36/40 only on desktop **with a fine pointer**.
  Nothing goes below 24px (`--hit-min`).
- **Widths:** catalog and tool page 1302px (`--content-max`), result 1100, My jobs 1000, options
  column 380, field heading column 280, tile min 15rem in an `auto-fill` grid.
- **Shape:** `--radius-sm` 2px on controls, `--radius-xs` 1px on the switch thumb, 0 elsewhere.
  Folds: `--fold-xs` 9 (logo), `-sm` 14 (drop icon), `-md` 36 (field), `-lg` 44 (tool header, drop
  card), `-xl` 72 (sign-in panel).
- **Borders:** `--border-hair` 1px (rules, inputs, tiles), `--border-control` 1.5px (secondary
  buttons, dashed unavailable tile, switch edge), `--border-strong` 2px (section rule, search box,
  selected option, dashed drop zone, invalid field), `--border-heavy` 3px (current nav item, step
  rule, kbd bottom).
- **Shadows:** none. The v0 `--shadow-*` tokens are gone.

### Motion

CSS transitions first (they can be interrupted); keyframes only for the two loading loops and the
one-shot route enter on the Convert page (`fx-enter`, opacity plus `--motion-distance-sm`). Animate
`transform` and `opacity` only; never `transition: all`. No JS animation library is needed. If
`motion/react` is ever added it must read these values.

| Token | Value | Use |
|---|---|---|
| `--duration-instant` | 80ms | Focus, badge |
| `--duration-fast` | 150ms | Tile lift, hover, press, switch thumb, overlay exit |
| `--duration-normal` | 220ms | Drop overlay fade-in, drop card settle |
| `--duration-slow` | 320ms | Progress fill steps, result reveal |
| `--duration-spin` | 900ms | Running-step ring, one turn |
| `--duration-indeterminate` | 1400ms | Queued bar sweep |
| `--ease-out` / `--ease-in-out` / `--ease-in` | | Enters and hovers / progress / exits |
| `--motion-lift` | 2px | Tile hover lift (only on `hover: hover`) |
| `--motion-distance-sm` / `-md` | 4px / 8px | Drop icon rise, drop card settle / result enter |
| `--motion-scale-press` | 0.97 | Primary button `:active` |

- **Tiles** lift 2px and their border turns ink. **Drag:** the overlay fades in over 220ms and the
  drop card settles 4px; it leaves in 150ms. **Progress** is `transform: scaleX()`, never `width`,
  eased over 320ms so once-a-second polling looks continuous. Progress digits are tabular, so they
  never shift.
- **Reduced motion** is built into the tokens: lift, distances and press scale collapse to 0 / 1 and
  durations are capped at 150ms. The loops stop themselves: the spinner becomes a static ring and
  the indeterminate bar a static half-opacity fill (preview CSS, "Reduced motion").

## 3. Fonts: self-hosted, offline, CSP-safe

- **File:** `web/public/fonts/archivo-latin-wdth-wght.woff2`, 74 KB, served at `/fonts/…` and
  declared in `tokens.css` (`font-weight: 400 900; font-stretch: 100% 125%; font-display: swap`).
  The licence is next to it as `web/public/fonts/Archivo-OFL.txt` (SIL OFL 1.1, no Reserved Font
  Name, so a subset may keep the name).
- **Provenance:** built from `fonts/variable/Archivo[wdth,wght].ttf` in the official
  [Omnibus-Type/Archivo](https://github.com/Omnibus-Type/Archivo) repo (master, v2.001,
  SHA-256 `664bbeb10522dac35c174a3860aaecad7b1ad3a0fc8b0d26888e26c824ec556d`). The repo only ships
  the variable font as a 658 KB TTF, so it was subset and compressed with fontTools 4.54.1: Unicode
  20–7E, A0–17F (Latin-1 and Latin Extended-A), 2000–206F, 2190–2193, 20AC, 2122, 2212, 2215, FFFD
  and a few spacing marks; features kern, liga, calt, tnum, lnum, pnum, case, ccmp, locl, mark, mkmk;
  no hinting; axes limited to wdth 100–125 and wght 400–900; woff2. Output SHA-256
  `7f171d5a9ef0e1e60b7f9512a3b20ffa5922f27f5f20465b7b94f42091657c5b`. To rebuild: run
  `fontTools.subset` with those unicodes and features, then
  `fontTools.varLib.instancer` with `wdth=100:125 wght=400:900`, then save with the woff2 flavor.
  (Subset first, then instance; fontTools 4.54 fails the other way round.)
- **Loading:** add `<link rel="preload" href="/fonts/archivo-latin-wdth-wght.woff2" as="font"
  type="font/woff2" crossorigin>` to `index.html`. CSP needs nothing extra (`font-src` falls back to
  `default-src 'self'`). No request ever leaves the box.
- **Fallback:** `--font-sans` falls back to `ui-sans-serif, system-ui, Segoe UI, Roboto…`. Anything
  outside the Latin subset (Cyrillic or Greek file names, for example) renders in the system font.
  Wide headings look narrower in the fallback, but nothing depends on exact widths: long lines
  wrap.
- **Budget:** 74 KB, fetched once and cached; it is not JS, so it does not count toward the 250 KB
  initial-JS budget.

## 4. Component notes for the web app

The preview's `<style>` block is the reference CSS. Port the rules, not the markup shortcuts. The
concept prototype uses inline `style=""` attributes everywhere; the app must not (strict CSP
`style-src` blocks them). Use CSS files and classes; React `style={}` for dynamic custom properties
such as `--p` and `--pos` is fine (it goes through CSSOM).

### Icons

- **Sprite:** `web/src/assets/icons.svg`, 31 symbols (`i-search`, `i-merge`, `i-split`, `i-rotate`,
  `i-grid`, `i-compress`, `i-wrench`, `i-convert`, `i-image`, `i-text`, `i-scan`, `i-sparkle`,
  `i-drop`, `i-stamp`, `i-hash`, `i-lock`, `i-pen`, `i-eraser`, `i-check`, `i-x`, `i-grip`, `i-up`,
  `i-down`, `i-file`, `i-download`, `i-trash`, `i-clock`, `i-alert`, `i-eye`, `i-arrow`, `i-logo`).
  It is the concept's original artwork: 24px grid, 1.75 stroke (2.25 for check and logo), round caps,
  `currentColor`. Checked: no scripts, event handlers, `foreignObject`, images, `url()` or
  external references; the only URI is the SVG namespace. The concept's C2PA provenance metadata
  (about 7 KB) is stripped in this copy.
- **Use:** `import spriteUrl from "./assets/icons.svg?url"` and render
  `<svg aria-hidden="true" width="24" height="24"><use href={`${spriteUrl}#i-merge`} /></svg>`. Keep
  it a same-origin file: set `build.assetsInlineLimit` so the sprite is never turned into a `data:`
  URI (browsers refuse `<use>` with `data:` URLs), or put a copy in `web/public/`. No icon library is
  needed. Sizes: 24 in tiles and search, 20 in buttons, 18 in rows and alerts, 16 inline, 14 in
  badges.
- Decorative icons get `aria-hidden="true"`; icon-only buttons get an `aria-label` that names the
  file ("Remove scan-0042"). Missing glyphs needed later (sign out, menu, compare, ebook) follow the
  same grid and stroke.

### Header

64px, bottom rule in ink. Brand (folded ink mark plus "FileX" wide 800), nav (Tools, My jobs; the
current item has a 3px ink underline drawn as a **border**, not a box-shadow, so it survives forced
colours), theme switch, "Signed in as <email>", Sign out. Add a skip link. Under 64rem the nav wraps
to its own row, "Signed in as" gets its own row beneath it (always visible, so a swapped session
shows: security review L-1), and Sign out moves to the right end of the nav row. The theme switch is a
`<fieldset>` of three radios with a visually hidden legend; the checked one inverts to ink. The
open-mode banner sits under the header in warning colours with an icon and a 1px ink rule.

### Catalog

- **Search:** `role="search"`, 2px ink border, icon left, `/` kbd hint right (hidden on
  `pointer: coarse`). The result count goes to a polite live region. Focus is the standard 2px ring
  (the concept's box-shadow ring is replaced by `outline`, which survives forced colours).
- **Chips:** toggle buttons with `aria-pressed`; each has a 12px swatch of its field colour (the
  label carries the meaning). Pressed inverts to ink.
- **Fields:** one `<section>` per group with an `<h2>` and a `<ul>` of tiles. Desktop: 280px heading
  column plus tile grid; mobile: heading on top. **Pad the heading's right side by the fold size on
  mobile** so a long name never runs under the cut corner (the preview does this).
- **Tiles:** a link on paper with a faint edge: icon, name (17/24 bold), one-line summary, optional
  mono format badges. Hover lifts 2px and the edge turns ink.
- **Unavailable tools** stay in the grid and in search: grey paper (`surface-2`), 1.5px dashed ink
  border, muted icon and an amber "Enable the `ocr` profile" badge tied in with `aria-describedby`.
  Never faded with opacity. Keep it a normal link to the tool page, which explains how to enable the
  profile in place of the drop zone. Do not use the concept's `aria-disabled` on it.
- **Popular conversions:** compact 44px "Word → PDF" links under the Convert tiles, from
  `GET /convert/targets`: a fixed priority list of common pairs (`POPULAR` in `web/src/convert.ts`),
  only those a running worker can do now, at most 8, then a dashed "Any format →" link to `/convert`.
  Each links to `/convert?from=docx&to=pdf`. Mono caps "Popular conversions" eyebrow in `on-cat`
  (muted text never sits on a field). The arrow is `aria-hidden` with a visually hidden "to".
  Search filters the pairs by the same words as tools; an older server without the planner just
  hides the block.

### Tool page

- **Header field:** the tool's category field with a 44px fold, mono breadcrumb, wide title and the
  accepted input in words ("PDF only, up to 1 GB per file, many files at once").
- **Layout:** files column plus a 380px options column on desktop; one column under 64rem.
- **Drop zone:** 2px dashed `border-strong` on paper, the folded ink "sheet" icon, "Drop PDFs
  anywhere on this page", and a real "Choose files" button (a `<label>` around a hidden
  `<input type="file">`, focus ring via `:has(:focus-visible)`). During a drag the border turns ink
  and the icon rises 4px.
- **Drop overlay:** while files are dragged over the window, a scrim plus a centred card in the
  tool's field colour with a dashed inner outline: "Drop to add", accepted formats, "Esc to cancel".
  Announce it in a polite live region. Esc or leaving the window closes it.
- **File list:** `<ol>` under a 2px ink rule. Row: grip, name (ellipsis), mono size, then Move up /
  Move down / Remove buttons with file-specific labels, so reordering works without dragging
  (WCAG 2.5.7). The first row's Move up and last row's Move down are disabled.
- **Upload progress (not in the concept):** while a file uploads, its size slot says
  "Uploading 42%", a 4px ink progress bar runs under the name (`role="progressbar"`), and Remove
  becomes "Cancel upload of …".
- **Size and count limits** are checked before upload; refusals use the alert pattern below.

### Options form: JSON Schema field → control

Every field gets a real `<label>` from its `title`; `description` becomes help text through
`aria-describedby`. Each field sits under a 1px rule with 16px above it.

| Schema | Control | Notes |
|---|---|---|
| `enum`, ≤ 4 values | Radio cards, 2 per row | `<fieldset>` + `<legend>`. Title (bold) + one-line description from `x-enum-descriptions`. Selected: inverts to ink, 2px border, check icon (so state is not colour alone) |
| `enum`, > 4 values | Native `<select>` | 44px, 1px `border-strong` |
| `boolean` | Switch | `<input type="checkbox" role="switch">` in a `<label>`; label and help left, square 48×26 track right. On: ink track, paper thumb moved right |
| `number` / `integer` | Number input, mono | `min`, `max`, `step` from the schema. Unit as a grey suffix box (`x-unit`), `aria-hidden` with the unit also in the help text |
| `string` | Text input | `maxLength` and `pattern` from the schema; `pattern` errors in words, not regex |
| `string`, `format: page-range` | Text input | Help gives an example ("1-3, 5, 8-"); validate against the real page count when known |
| `string`, `format: password` / `writeOnly` | Password input + show/hide button | `autocomplete="off"`, toggle with `aria-pressed` and a label that flips (Show / Hide password). Help says it is never stored |
| `x-widget: position` | 3×3 position picker | A radio group of 44px squares, each with an `aria-label` ("Bottom centre"); selected square inverts to ink |
| `x-widget: page-grid` | Page thumbnails grid | PDF.js renders in a grid of paper sheets with 1px image outline; selection inverts the caption to ink; keyboard: arrows move focus, Space selects, "Move to position" for reorder (spec 5.3) |
| `x-widget: aux-file` | Secondary "Choose file" button + mono file name | Accept list from the schema; help says it is used for this job only |
| Invalid value | Any of the above | `aria-invalid="true"`, 2px danger border, and an error line with alert icon and words ("Page 9 does not exist; this file has 8 pages."), tied in with `aria-describedby` before the help |

The submit bar sticks to the bottom of the options column: mono summary ("3 files, 34.2 MB"), then
the 56px ink button that says what will happen ("Compress 3 files →").

### Progress and My jobs

- **Job:** a section under a 2px ink rule. Title plus status badge (Running = ink, Queued = grey with
  clock, Done = green with check, Failed = red with x). The % is set at poster size in tabular
  figures next to the step message, then a 12px track with an ink fill (`role="progressbar"` with
  `aria-valuenow`; the big % is `aria-hidden` because the bar carries the value).
- **Steps:** done (green square with check), running (ring spinner), waiting (empty square, muted,
  1px rule) with visually hidden "Done:", "Running:", "Waiting:" prefixes. The running step has a
  3px ink top rule. Three columns on desktop, stacked on mobile.
- **Queued / waiting for a slot:** "Queued, 2 jobs ahead" (or "Waiting for a free slot") as a wide
  statement plus the indeterminate bar.
- One polite live region announces milestones (started, step changed, finished), not every percent.
  Cancel is a secondary button.
- **Job list (not in the concept, spec 9.10):** "Earlier" under the running jobs, a list under a 2px
  ink rule. Row: tool name as a link, file name muted, status badge, mono time, mono expiry. Stacks
  under 64rem.

### Result

- Head: green check square, "<file> is ready", badge "Target reached" (green) or "Best we could do"
  (amber with alert icon) for target-size mode.
- **Outcome:** "−62%" at `--display-outcome`, weight 900, beside "8.4 MB → 3.2 MB" (112% width),
  "5.2 MB saved, under the 4 MB target", and two size bars (original `border-strong`, now
  `success-fg`) in a `role="img"` with the full sentence as its label (spec 5.3).
- **Before/after:** two page renders side by side at the same zoom from 40rem; under 40rem one frame
  with a clip divider and a labelled `<input type="range">`. Renders get a 1px neutral inset outline
  (`--outline-image`).
- Actions under a 2px rule: Download (primary, with size), Delete now (secondary), "Expires in 23 h"
  in mono on the right. Focus moves to the result section when the job ends.
- **Batch and reports (not in the concept, spec 9.6 and 9.9):** a file-list-style `<ol>` of outputs
  (name, mono size, Download button per row) above a primary "Download all (zip)"; job warnings as
  amber notes; `REPORT` outputs listed as separate secondary downloads.

### Convert page (spec 9.11)

Route `/convert`, linked from the header nav (Tools, **Convert**, My jobs) and from the popular
conversions. It is the tool page's layout with its own content: `web/src/pages/Convert.tsx`,
`components/TargetPicker.tsx`, `components/RoutePreview.tsx`, helpers in `web/src/convert.ts`.
Screenshots in `docs/design/screenshots/`: `convert-light.png`, `convert-dark.png`,
`convert-mobile.png`, `convert-route-preview.png`, `convert-no-route.png`,
`catalog-popular-conversions.png`.

- **Header field:** the amber Convert field, folded; crumb "Tools / Convert", title "Convert", "Any
  format FileX can reach, up to 50 files in one job", then one sentence saying FileX chains
  converters and shows the route first.
- **Left column, files first:** the shared drop zone and file list. Accepted: every format the
  server knows, described as "Documents, ebooks, images and PDFs" (`FilePicker`'s `kinds` prop), so
  a Word file is still taken when the office add-on is off and the page can say why it cannot
  convert it. Files are independent, so there are no reorder controls.
- **"Convert to"** under a 2px ink rule:
  - **From.** With files: the detected formats as mono badges with title and count ("DOCX Word
    document · 2 files"), read from the extension (`sourceFormat`), and "FileX checks the content
    when it uploads". Without files: a native `<select>` of the formats that convert now, to preview
    a route before uploading (`?from=` preselects it). Once there are files it is not a picker,
    because the server sniffs the content and a picker would disagree with it.
  - **Targets.** One radio group (`name="convert-to"`, sr-only legend) of radio cards split into
    families, each with a mono caps heading: PDF, Documents (office, text, markup), Ebooks & comics,
    Images, Other. From 40rem the family name sits in an 8.5rem left column like a field heading,
    so a one-card family is one row tall. Card: format label (17px bold), its title ("Plain text"),
    mono "2 steps", and when lossy an alert icon plus "May lose formatting". Selected inverts to ink
    with the check (the options form's `.opt` card). Only targets every detected format reaches are
    listed (intersection of `GET /convert/targets`; the planner has the last word for mixed files).
    `?to=` preselects one.
- **Right column, "Route":** sticky on desktop together with the run button, so the route stays in
  view while the targets scroll; on mobile it follows the targets and the run bar sticks as on tool
  pages.
  - Summary in wide heavy 22px, "DOCX → PDF → TXT" (arrows `aria-hidden`, sr-only "to"), then mono
    "2 steps · office add-on, core worker · 1 lossy step".
  - **The rail** (signature detail): a vertical 2px ink line with a format "sheet" at every stop
    (mono label in a 1.5px ink box, title beside it) and the step on the line between two stops:
    mono caps "STEP 1", converter title bold, mono worker ("core worker" or "office add-on"; a
    worker group is a compose profile). A lossy step draws its line **dashed** and carries the amber
    badge "Some formatting may be lost", so lossiness is never colour alone. The last stop is the
    folded ink sheet, like the logo and the drop icon. Screen readers get one sentence per step
    ("Step 1: Word document to PDF with Office to PDF, on the office add-on.") and a polite status
    "Route ready: 2 steps, some formatting may be lost."
  - Enter: the route fades in and rises 4px over `--duration-slow` each time a new plan arrives (a
    one-shot keyframe; under reduced motion the distance is 0 and the fade 150ms).
- **Run bar:** mono summary ("2 files, 3.4 MB" plus "· choose a format", "· planning the route" or
  "· no route"), then the 56px ink button "Convert 2 files to PDF →", enabled only with files and a
  plan. Upload shows "Uploading 42% of 3.4 MB", per-row bars and Cancel upload, as on tool pages.
- **States** (all in words, never colour alone):
  - Nothing yet: "Add files, or choose a format above, to see what it can become." / "The route
    shows here once FileX knows what your files are." / "Choose a format to see how FileX gets there."
  - **Mixed formats, no common target:** danger alert "These files have no format in common. DOCX
    and PNG files cannot be converted to the same format. Remove some files or convert them in
    separate jobs."
  - **Mixed formats, no shared first step** (`NO_ROUTE` with no profiles and several sources): the
    route alert says they share no first step and suggests separate jobs.
  - **Nothing reachable for a format:** warning note "Nothing on this server converts EPUB ebook
    files right now." plus the add-on to enable, found from `GET /tools` (unavailable convert or
    ebook tools that take the format). No converter at all: statement "Nothing converts on this
    server right now".
  - **`NO_ROUTE`:** alert in the route column, "No route from DOCX to EPUB right now.", then "Enable
    the ebook add-on: add `ebook` to `COMPOSE_PROFILES` in `.env`, then run `docker compose up -d`."
    from `details.profiles` ("Start the core worker." when `core` is listed), and `[NO_ROUTE]`.
  - **`TOO_MANY_FILES`:** "Too many files for this route. One step takes at most 2 files. Remove 1 or
    convert them in separate jobs." from `details.max`.
  - Anything else, including a server without the planner: the alert pattern with Try again.
- **Run:** `POST /convert/jobs` with the files and `target`, no options. Then the shared JobView:
  "Convert to PDF, 2 files", steps labelled "Step 1: DOCX to PDF (Office to PDF)" (from the plan,
  because job steps carry only tool ids), the running step's spinner and 3px rule, "Running step 1
  of 2" in the live region, then the result card and the signed-link download.
- **My jobs:** a `convert` job reads "Convert → PDF" (screen readers: "Convert to PDF"), from its
  first result's format, else the single output of its last step's tool, else "Convert"; it links
  to `/convert`.

### Errors and empty states

- **Alert:** 2px danger border on the danger tint, alert icon, a bold first line that says what
  happened, then what to do, with the error code in brackets and a link to the tool that fixes it.
  `role="alert"`.
- **Wrong file refused:** the alert under the drop zone names the file and suggests a tool.
- **No search results:** wide statement "No tools match "…"", a hint, and Clear search.

### Sign-in

Two columns on desktop, stacked on mobile. Left: an ink panel with a 72px fold, the inverted brand
and "Your paper, your server." at `--display-wordmark` weight 900. Right: "Sign in", an alert slot,
email (`autocomplete="username"`), password (`current-password`) with show toggle and "Forgot
password?", and the full-width primary button. Missing states (spec 9.1), all in the same form
column:

- **Set password (invite link):** title "Set your password", new and confirm fields
  (`autocomplete="new-password"`), rules in help text.
- **Not allowed (`403 USER_NOT_ALLOWED`):** danger alert "This account is not on the FileX list. Ask
  your admin to add it."
- **Token mode:** one password-type "Access token" field replaces email and password.
- **Signed out:** a neutral note above the form.

### Focus, forced colours, safety

- **Focus:** `:focus-visible` is a 2px ink outline with 2px offset everywhere (on fields too).
  Composite controls (radio cards, theme switch, file button, position picker) show it on the
  wrapper via `:has(:focus-visible)`.
- **Forced colours:** fills vanish, so everything that shows state by inverting to ink opts out with
  `forced-color-adjust: none` and `Highlight` / `HighlightText` (pressed chips, theme switch, radio
  cards, switch, position picker, progress fills). Fields get a `CanvasText` border via
  `--field-border`; badges and the primary button carry a transparent 1px border that becomes
  visible. The fold triangles are hidden.
- **Safe rendering (spec 5.2, 9.14):** file names, step messages, warnings and error text are
  rendered as React text, never HTML. No `dangerouslySetInnerHTML` or `innerHTML`. Previews are
  PDF.js canvas renders or `<img>` only; outputs never go in `<iframe>`, `<object>` or `<embed>`. No
  `href` or `src` built from job data except FileX API paths.

## 5. Concept B against the spec: conflicts and how they are resolved

| Concept B | Spec | Resolution |
|---|---|---|
| Loads Archivo from Google Fonts, React/Babel from unpkg | 5.2: no third-party requests, all assets self-hosted | Archivo subset self-hosted (section 3); prototype is reference only |
| Inline `style=""` on every element | Strict CSP | CSS files and classes; only custom properties set from React |
| Six category fields, 17 tools | R1 has 31 tools including the `ebook` group; R2 adds compare and ai | Added `--cat-ebook` (violet), `--cat-compare` (slate), `--cat-ai` (lime), same contrast rules |
| Header shows the email but no sign-out; email hidden on mobile | 9.1: sign-out | "Sign out" next to the email on desktop, at the end of the nav row on mobile |
| Theme switch drawn as three spans | Accessible, keyboard operable | Fieldset with three radios |
| Search focus is a box-shadow ring; current nav item a box-shadow underline | Visible focus in forced colours | `outline` ring; underline is a border |
| Unavailable tile is `<a aria-disabled>` | 9.2: greyed but findable | Normal link to the tool page, which explains how to enable the profile |
| No upload progress or cancel | 9.3 | Per-row 4px bar, "Uploading 42%", Cancel upload button |
| Only radio cards, select, number, switch, page range and password shown | 9.4: string, aux file, page-grid, position picker | Mapping table above; position picker shown in the preview |
| No "best we could do" result | 9.8 | Amber badge variant, shown in the preview |
| No batch result, warnings or report outputs | 9.6, 9.9 | File-list pattern with per-row downloads and "Download all (zip)" |
| "My jobs" shows only live job cards | 9.10: list with status, tool, time, expiry | "Earlier" list under the live jobs |
| No convert page | 9.11: from/to pickers, route preview, lossy warnings, `NO_ROUTE` | Done (section 4, "Convert page"): from is detected from the files (a select only to preview before files), targets as family-grouped radio cards, the route as a vertical rail with dashed lossy steps and an amber badge, `NO_ROUTE` naming the add-on to enable |
| No set-password, not-allowed or token sign-in states | 9.1 | Same form column (section 4, Sign-in) |
| "Waiting for a free slot" not shown | 9.5 | Same as queued: statement plus indeterminate bar |
| Progress bar fill and inverted states disappear in forced colours | 5.3 | Forced-colours rules in section 4 |

Small normalisations: the concept's search padding (60px) is expressed as gutter + icon + gap; its
`margin:1px` trick on unselected radio cards is kept so selecting does not shift the layout; the
waiting step gets 2px extra top padding for the same reason.

## 6. Accessibility checks

- **Contrast:** a script parses `tokens.css`, checks that the two dark blocks are identical and that
  every light colour has a dark value, and computes WCAG 2.x ratios. Result: **0 failures** in both
  themes, with Concept B's colours unchanged. The preview recomputes the main pairs live (22 pairs,
  0 failures in light and dark).
- **Not colour alone:** status has icon plus text; the selected radio card has a check; invalid
  fields have icon plus message plus border; categories have headings; the unavailable tile has a
  text badge; switch state is thumb position.
- **Targets:** 44px controls (40/36 only for chips, theme switch and row buttons on desktop with a
  fine pointer); nothing under 24px.
- **Keyboard:** everything in the preview is reachable with Tab; `/` focuses search (ignored while
  typing); Esc closes the drop overlay.
- **Reflow:** checked headless at 360px CSS width: `scrollWidth == clientWidth` (no horizontal
  scroll; only the contrast table scrolls inside its own wrapper). Sizes are in rem.
- **Still to do (Task 12/45):** axe in Playwright on the real React pages, and NVDA + Firefox and
  VoiceOver + Safari passes on the live job flow.

### Contrast table

`text-disabled` is not listed (disabled controls are exempt). 3:1 rows are UI parts: control edges,
focus rings (including on every field, because tiles get focus there), progress fills and the done
check.

| Foreground | Background | Need | Light | Dark |
|---|---|---|---|---|
| `text` | `bg` | 4.5:1 | 16.90 | 17.12 |
| `text-muted` | `bg` | 4.5:1 | 7.62 | 8.77 |
| `text` | `surface` | 4.5:1 | 18.61 | 15.72 |
| `text-muted` | `surface` | 4.5:1 | 8.39 | 8.06 |
| `text` | `surface-2` | 4.5:1 | 15.58 | 14.26 |
| `text-muted` | `surface-2` | 4.5:1 | 7.02 | 7.30 |
| `text` | `surface-3` | 4.5:1 | 13.92 | 12.20 |
| `text-muted` | `surface-3` | 4.5:1 | 6.28 | 6.25 |
| `link` | `bg` | 4.5:1 | 16.90 | 17.12 |
| `link` | `surface` | 4.5:1 | 18.61 | 15.72 |
| `on-cat` | `cat-organize` | 4.5:1 | 11.93 | 10.60 |
| `on-cat` | `cat-optimize` | 4.5:1 | 12.29 | 10.30 |
| `on-cat` | `cat-convert` | 4.5:1 | 12.05 | 10.54 |
| `on-cat` | `cat-ocr` | 4.5:1 | 12.06 | 10.32 |
| `on-cat` | `cat-edit` | 4.5:1 | 10.84 | 12.34 |
| `on-cat` | `cat-security` | 4.5:1 | 10.03 | 12.10 |
| `on-cat` | `cat-ebook` | 4.5:1 | 10.67 | 12.27 |
| `on-cat` | `cat-compare` | 4.5:1 | 11.50 | 11.21 |
| `on-cat` | `cat-ai` | 4.5:1 | 13.72 | 10.37 |
| `on-accent` | `accent` | 4.5:1 | 18.61 | 17.12 |
| `on-accent` | `accent-hover` | 4.5:1 | 13.46 | 13.94 |
| `on-accent` | `accent-pressed` | 4.5:1 | 10.61 | 11.41 |
| `on-accent-muted` | `accent` | 4.5:1 | 11.66 | 9.03 |
| `on-danger` | `danger` | 4.5:1 | 6.39 | 8.80 |
| `success-fg` | `success-bg` | 4.5:1 | 5.38 | 8.44 |
| `success-fg` | `bg` | 4.5:1 | 5.58 | 10.47 |
| `success-fg` | `surface` | 4.5:1 | 6.15 | 9.61 |
| `warning-fg` | `warning-bg` | 4.5:1 | 5.61 | 9.27 |
| `warning-fg` | `bg` | 4.5:1 | 5.75 | 11.41 |
| `warning-fg` | `surface` | 4.5:1 | 6.33 | 10.48 |
| `danger-fg` | `danger-bg` | 4.5:1 | 5.45 | 7.58 |
| `danger-fg` | `bg` | 4.5:1 | 5.80 | 8.80 |
| `danger-fg` | `surface` | 4.5:1 | 6.39 | 8.09 |
| `border-strong` | `bg` | 3.0:1 | 4.34 | 4.61 |
| `focus` | `bg` | 3.0:1 | 16.90 | 17.12 |
| `border-strong` | `surface` | 3.0:1 | 4.78 | 4.24 |
| `focus` | `surface` | 3.0:1 | 18.61 | 15.72 |
| `border-strong` | `surface-2` | 3.0:1 | 4.00 | 3.84 |
| `focus` | `surface-2` | 3.0:1 | 15.58 | 14.26 |
| `border-strong` | `surface-3` | 3.0:1 | 3.58 | 3.29 |
| `focus` | `surface-3` | 3.0:1 | 13.92 | 12.20 |
| `focus` | `cat-organize` | 3.0:1 | 11.93 | 10.60 |
| `focus` | `cat-optimize` | 3.0:1 | 12.29 | 10.30 |
| `focus` | `cat-convert` | 3.0:1 | 12.05 | 10.54 |
| `focus` | `cat-ocr` | 3.0:1 | 12.06 | 10.32 |
| `focus` | `cat-edit` | 3.0:1 | 10.84 | 12.34 |
| `focus` | `cat-security` | 3.0:1 | 10.03 | 12.10 |
| `focus` | `cat-ebook` | 3.0:1 | 10.67 | 12.27 |
| `focus` | `cat-compare` | 3.0:1 | 11.50 | 11.21 |
| `focus` | `cat-ai` | 3.0:1 | 13.72 | 10.37 |
| `bg` | `text` | 4.5:1 | 16.90 | 17.12 |
| `bg` | `success-fg` | 3.0:1 | 5.58 | 10.47 |
| `text` | `warning-bg` | 4.5:1 | 16.48 | 13.91 |
| `accent` | `surface` | 3.0:1 | 18.61 | 15.72 |
| `accent` | `surface-3` | 3.0:1 | 13.92 | 12.20 |
| `success-fg` | `surface` | 3.0:1 | 6.15 | 9.61 |
