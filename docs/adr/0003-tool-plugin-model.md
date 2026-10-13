# ADR-0003: Tools are declared once in Python; the registry generates everything else

## Status
Proposed. Amended 2026-10-10: auxiliary files, secret options, reserved job-output inputs, warnings
and output roles, see the Amendment.

## Date
2026-10-10

## Context
FileX will grow to 100+ tools. Each tool shows up in many places: an API route, an OpenAPI
schema, a UI tile and options form, docs, and a worker that knows how to run it. Written by
hand, these drift, and adding a tool becomes a multi-file change across backend, frontend
and docs.

## Decision
A tool is one Python module under `filex/tools/<category>/` with a `@tool(...)` decorator on
its `run` function, declaring: `id`, `title`, `summary`, `category`, `group`, `inputs` /
`outputs` format ids (or `converts=[Edge(...)]` for converters), `files` cardinality,
`options` (a pydantic model), `engines`, `limits`, `examples`. See
[architecture.md section 6](../architecture.md#6-tool-plugin-model) for the full sketch.

- The registry is built by scanning the `filex.tools` package at startup.
- From it the API generates one route per tool, the OpenAPI schema (ADR-0004), the
  `GET /tools` catalog the UI renders (tiles, file-picker filters, options forms from JSON
  Schema), and `docs/tools.md`.
- Workers use it for routing, limits and engine probes.
- Generic tests run over every registered tool: module imports without engines installed,
  unique stable ids, known groups/categories/formats, options schema within the
  UI-supported subset, examples validate and run as smoke tests in the group's image.

Adding a tool = one module + one test file (+ a corpus sample if needed) + regenerated
contract and docs.

## Alternatives Considered

### YAML/JSON manifests plus separate code
- Pros: language-neutral, readable by the UI directly.
- Cons: two files per tool that must agree; options validation needs a second schema
  language.
- Rejected: pydantic already produces JSON Schema.

### Hand-written routes and UI forms per tool
- Pros: full control per tool.
- Cons: drift, boilerplate times 100.
- Rejected.

### Entry-point plugins (installable third-party packages)
- Pros: external contributors can ship tools.
- Cons: not needed for a self-hosted app built from one repo; adds versioning questions.
- Deferred; discovery is isolated in one function so this can be added.

## Consequences
- Tool ids are public API; renaming one is a breaking change.
- The UI form renderer supports only a small schema subset; tools needing richer input
  (page picker, signature placement, redaction boxes) need a named UI widget hint in the
  schema, added deliberately one at a time.
- Engine imports must live inside `run()`; the import test enforces it.

## Amendment (2026-10-10): auxiliary files, secret options, warnings and output roles

Resolves spec open items A4, A5 and A7, and review H5 and H8. Every addition is optional in the
declaration, so the tools already sketched keep working.

### Auxiliary files (A4)
`Files` gains named slots next to the main `files[]` list:

```python
files=Files(min=1, max=20, each=True,
            aux={"certificate": Aux(formats=["p12"], required=True, sensitive=True),
                 "stamp":       Aux(formats=["png", "jpg"], required=False)}),
```

- In the multipart request each slot is a part with the slot's name (`certificate`, `stamp`),
  one file per slot. The API sniffs it like any input and stores it as `in/aux/<slot>.<ext>`.
  Unknown part names are rejected with `422`, which keeps later additions additive.
- The step gets `ctx.aux["certificate"]` as a path inside its scratch dir (hard link), the same
  rule as main inputs ([ADR-0007](0007-sandboxing-untrusted-files.md) item 7).
- `sensitive=True` (the `.p12`, review H8): the runner deletes the file from `in/aux/` as soon as
  the step ends, whatever the outcome; it is never logged, returned or reused.
- `Files(min=0, max=0)` is allowed for tools with no input (`cert.create`).
- Converter tools (those with `converts=`) may not declare auxiliary slots or secret options, so
  routes built by `convert` never need them ([ADR-0005](0005-conversion-routing.md)).
- The UI renders each slot as a labelled file field; the slot's title comes from `Aux(title=...)`.

### Secret options (A4, review H5)
- A secret field is `SecretStr` declared with the registry helper `Secret(title=..., min_length=...)`,
  which adds `writeOnly: true` and `format: password` to the JSON Schema. The UI shows a password
  field; OpenAPI documents it as write-only and never carries an example value for it.
- The API splits secrets out before anything is stored: non-secret options go into the job hash
  as before; secret values go to `sec:<job_id>:<step>` (one JSON string, TTL = the 24 h queued-job
  cap). They never enter the job hash, a stream entry, a log line, an error message or
  `GET /jobs/{id}`.
- Only the `api` ACL user can write `sec:*`; the `worker` user can only `GETDEL` it. The runner
  `GETDEL`s the secrets just before spawning the step and writes them to the child's **stdin**.
  The child passes them to the engine in-process (pikepdf, pyHanko, cryptography) or through a
  pipe (`qpdf --password-file=-`), never in argv or the environment. Redis persistence is off,
  so a secret is never written to disk.
- If a step is redelivered after its secret was consumed (the worker died mid-step), the step
  fails with `SECRET_UNAVAILABLE` and the user resubmits. A second chance is not worth keeping
  the secret around.
- When a job ends for any reason the API deletes any remaining `sec:<job_id>:*` keys.
- Generic tests: every `SecretStr` field is `writeOnly`; a job submitted with a known password
  leaves no trace of it in Redis (other than the consumed key), logs, the job JSON or errors.

Why Redis and not a file in the job dir (the spec's first proposal): a file on `/data` is
readable by any concurrent compromised step until Landlock exists, while `sec:*` is reachable
only with the worker's Redis credentials, which no step has and no step has a socket to use.

### Job output as input: shape reserved, not built (A5)
Needed in R2 (`ai.chat` follow-ups, "use this file in another tool") and R3 (recipes). Reserved
now so R1 clients never collide with it:

- In `POST /tools/{toolId}/jobs`, the multipart part name `inputs` is reserved for a JSON array
  `[{ "fromJob": "<jobId>", "output": <index> }]`, taking the place of (or adding to) `files[]`.
  R1 rejects it with `422 NOT_SUPPORTED`.
- When built: each entry goes through `require_job` ([ADR-0009](0009-keel-auth.md)), so only the
  caller's own jobs resolve; the output index is range-checked; the file is hard-linked into the
  new job's `in/` (same volume) and sniffed again like an upload. The new job then owns its copy
  and its own retention clock.

### Warnings and output roles (A7)
Tools can tell the user something besides the file:

```python
ctx.warn("TARGET_NOT_REACHED", "Best we could do: 2.4 MB (target 2 MB).")
return [out_pdf, Report(proof_json)]       # Report() marks a report, a bare Path is a result
```

- The job JSON gains `warnings: [{ "code": "TARGET_NOT_REACHED", "message": "...", "step": 0 }]`
  and each output gains `role`: `RESULT` or `REPORT`.
- Codes are UPPER_SNAKE and part of the contract. Messages are written by tool code in plain
  language, never copied from engine output. The runner caps them (20 warnings per job,
  300 characters each) when it copies them from the child's result into the job hash.
- The planner adds `LOSSY_STEP` for every lossy hop it plans ([ADR-0005](0005-conversion-routing.md)).
- `GET /jobs/{id}/result` returns the single `RESULT` output, or a zip of all `RESULT` outputs;
  reports are downloaded individually through `/outputs/{n}`.
- Output names (2026-10-10): `Output(path, "doc-part-001.pdf")` names a result, and
  `Report(path, name)` a report. The runner cleans the name (NFC, no path parts, control or bidi
  characters, at most 200 characters, the sniffed format's extension) into the job record's
  `outputs[].name`; the API saves the download and the zip entry under it, else under the
  input's name as before. A bare `Path` keeps the input-based name.
- At most `MAX_OUTPUTS` (1000, `filex.registry`) outputs per step; more fails the step with
  `OUTPUT_TOO_LARGE`. A tool that can tell in advance (e.g. `pdf.split`) refuses with
  `INVALID_OPTIONS` before doing the work.

### Consequences of the amendment
- The decorator surface grows by `Aux`, `Secret`, `ctx.warn` and `Report`; each is covered by a
  generic registry test.
- New error codes: `NOT_SUPPORTED`, `SECRET_UNAVAILABLE`, plus per-tool warning codes.
- The options form renderer supports two more field kinds: password and auxiliary file.
