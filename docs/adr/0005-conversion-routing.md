# ADR-0005: Automatic conversion routing over a format graph

## Status
Proposed. Amended 2026-10-10: ebook routes in R1, static multi-step tools, LOSSY_STEP warnings.

## Date
2026-10-10

## Context
FileX converts between PDF, Office formats, images, HTML/Markdown and ebooks. No single
engine covers every pair; many pairs need an intermediate format (PPTX to EPUB via PDF).
A tool per pair does not scale, and which pairs work depends on which worker groups the
admin enabled.

## Decision
- Converter tools declare `converts=[Edge(src, dst, lossy=False, cost=0)]`.
- Formats are graph nodes; edges come only from tools that are currently available (live
  worker heartbeat).
- A built-in `convert` tool runs Dijkstra: cost 1 per step, +4 for a lossy edge, plus the
  declared extra cost; ties break on fewer steps, then tool id, so plans are deterministic.
  Max 4 steps.
- The route becomes the job's steps (one per hop, possibly in different groups).
- `GET /convert/plan` previews the route. No route returns `422 NO_ROUTE`, naming the
  profiles that would create one (found by re-running the search with every group assumed
  online).
- Popular "X to Y" catalog tiles are generated from reachable pairs.

## Alternatives Considered

### One tool per format pair
- Pros: explicit, tunable per pair.
- Cons: combinatorial; disabled groups break hard-coded chains.
- Rejected; a pair that needs special options can still be an ordinary tool.

### Always go through PDF as a hub
- Pros: trivial planner.
- Cons: lossy for document-to-document and ebook conversions.
- Rejected.

## Consequences
- Cost numbers are heuristics; benchmarks should tune them.
- Intermediate hops use default options in v1; per-hop options are a pipeline feature.
- Each new edge silently creates new routes, so planner tests keep golden routes for the
  popular pairs and a route change shows up as a test diff.

## Amendment (2026-10-10): ebooks in R1, static multi-step tools, warnings

- **Default graph.** With the default profiles `office,ocr,ebook`, the graph holds the `core`,
  `office` and `ebook` edges, including Pandoc's (`markup.convert`) and Calibre's
  (`ebook.convert`). Routes such as `md → epub → mobi` and `docx → epub` stay within the 4-step
  cap. Edges with PDF as the source of an ebook conversion are lossy.
- **One engine, one place.** Pandoc exists only in `ebook` ([ADR-0002](0002-worker-groups-and-compose-profiles.md)).
  R2 Markdown to PDF is the route `md → html` (`ebook`) then `html → pdf` (WeasyPrint, `office`),
  not a tool with both engines in one image.
- **Tools that need more than one step plan statically.** A tool may expand into a fixed list of
  steps at job creation, the same way a route does. `djvu.to-pdf` with `ocr: "auto"` plans
  `djvu → pdf` (`ebook`) followed by `ocr.pdf` in skip-text mode (`ocr`) when the `ocr` group is
  online; OCRmyPDF's skip-text leaves pages that already have text alone, so no step ever has to
  append steps at run time. With `ocr` offline the plan is one step and the job carries a
  `OCR_SKIPPED` warning.
- **The planning hook** (task 52; `filex/registry.py`). `@tool(plan=fn)` makes a tool static
  multi-step. `POST /tools/{id}/jobs` calls `fn(validated options, ids of the tools online now)`
  once, after the upload and target checks, and gets a `Plan`: `steps`, the `FollowUp(tool,
  options)` that run after the tool's own step, and `warnings`, `(code, message)` pairs from the
  tool's declared `warnings`. Follow-up options are validated against the follow-up tool's
  options model and stored like any step's; follow-ups take no target, secrets or auxiliary
  files. Plan warnings go into the job's `warnings` at creation with `step: 0`; the runner
  appends its own after them. A plan naming an unknown tool, invalid options or an undeclared
  code is a bug in the tool and fails the request with 500, never a job later. The hook is pure
  (no I/O, no Redis): `online` is the heartbeat set the API already read for the
  `TOOL_UNAVAILABLE` check. If a follow-up's group goes offline after creation, its step waits
  in its queue like any other step. Only direct tool jobs are planned; a `convert` route uses
  each hop's tool as a single step.
- **Warnings.** Every lossy hop in a plan adds a `LOSSY_STEP` warning to the job
  ([ADR-0003](0003-tool-plugin-model.md) amendment), so the API tells scripts what
  `GET /convert/plan` shows the UI.
- **Routes carry no secrets or auxiliary files.** Converter tools cannot declare either
  (ADR-0003 amendment), so any route can be built from defaults.
- The heavy-step slot (ADR-0002) affects when a step runs, never which route is chosen.

## Amendment (2026-10-10): the target of a single converter job

- `POST /tools/<converter>/jobs` takes an optional multipart `target` part: a format id. It is
  stored in the step (`steps[0].target`) and reaches the tool as `ctx.target`.
- Omitted: the tool's `target_option` value if it declares one (`pdf.to-image`: `format`), else
  the only format every input converts to (`office.to-pdf`, `image.to-pdf` → `pdf`); several
  candidates → `422 TARGET_REQUIRED` with `details.targets`.
- A target without an edge from every sniffed input format, inputs with no common target, or a
  target sent to a tool that is not a converter → `422 INVALID_TARGET`. A given target wins over
  `target_option`.
- `GET /tools/{id}` lists `converts` (`src`, `dst`, `lossy`) and `targetOption`, so the UI can
  offer only reachable targets.
