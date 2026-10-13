# ADR-0010: `ai` as a sixth, optional worker group: Ollama server + `worker-ai` with a Unix-socket relay

## Status
**Deferred** (2026-10-13). The AI profile is not on the current roadmap. This design stays
as the reference if it is picked up again; nothing in R1 depends on it beyond the reserved
`inputs` part name and the planner's ability to prepend steps.

Previously: accepted with the conditions of security review section 5.2 (2026-10-10), planned
for R2. The first draft's
seccomp exception for `ai` steps was rejected and is replaced by the relay below.

## Date
2026-10-10

## Context
The spec (section 8) wants private local AI in R2: summarize, chat with a document, translate.
Unlike every other engine, an LLM is a long-lived server that keeps a model of several GB in
RAM; starting it per step would reload the model every job. A step must therefore reach that
server, but the step sandbox ([ADR-0007](0007-sandboxing-untrusted-files.md)) denies network
sockets, and security rejected an exception: a compromised step with `AF_INET` could read the
`worker` Redis password (secret file and `/proc/1/environ` are readable by the same uid), log in
to Redis and bring back review finding H1. `ml` (ONNX per-step models) has the per-step shape
and stays separate.

## Decision

### Services
- New group `ai`, profile `ai` (and `all`), off by default.
- `ollama`: the inference server. ADR-0007 Appendix A.1 hardening: non-root `user`, `read_only`
  with only the `filex-models` volume and a tmpfs writable, `cap_drop: [ALL]`, no `ports:`. On
  the `ai` network only (`internal: true`), never on `internal`; it never sees `/data` or Redis.
  A minimum version is enforced by the engine probe and the image is part of the weekly rebuild.
- `worker-ai`: the usual runner (`FROM filex-base` + client libraries) on `internal` (Redis)
  and `ai` (Ollama). `FILEX_CONCURRENCY=1`, heavy step ([ADR-0002](0002-worker-groups-and-compose-profiles.md)).
- `ollama-pull`: the only service with egress that can write `filex-models`. Profile `ai-pull`
  only, so `--profile all` never starts it. The admin runs it by hand with official model names:
  `docker compose --profile ai-pull run --rm ollama-pull <model>`. Translation models are baked
  into the `worker-ai` image. Models never download at job time.

### The relay: one seccomp filter for every group
`ai` steps get the same filter as every other step (no `AF_INET`). The trusted `worker-ai`
runner, which has network access anyway, opens an `AF_UNIX` socket inside the step's scratch dir
before spawning the child and relays requests to `FILEX_OLLAMA_URL` (default
`http://ollama:11434`):
- forwards only `POST /api/generate`, `POST /api/chat`, `POST /api/embed`; everything else gets
  `403` from the relay;
- caps request and response body size, and enforces the step's wall-clock timeout;
- sets `num_predict` and the context size on every request (from the tool's limits), whatever
  the step asked for;
- is closed and unlinked when the step ends.

The step talks to it with httpx's UDS transport. About 40 lines in the runner; nothing else
changes in the sandbox.

### Inputs and outputs
- **Inputs are text FileX produced or decoded.** The planner, not the client, enforces that AI
  tools receive only `text/plain` from a previous FileX step (`pdf.to-text` in `core`, Docling in
  `ml`) or a UTF-8-decoded upload, capped in size. Layout-preserving translation parses the PDF in
  a preceding sandboxed step and renders the translated text with FileX's own PDF code.
- **LLM output is untrusted.** Answers are saved as `text/plain` attachments
  ([ADR-0009](0009-keel-auth.md) download rules). The UI shows them as text, or as Markdown with
  raw HTML off and no images or links built from the output, never through
  `dangerouslySetInnerHTML`. The model gets no tools, and its output never picks tools, options,
  paths or later steps.
- Chat follow-ups reuse a job's output as an input (ADR-0003 amendment, A5); answers are job
  outputs and expire with the job.
- `FILEX_OLLAMA_URL` pointing at another machine on the network needs egress and waits for the egress
  ADR (ADR-0007 "Later").

## Alternatives Considered

### Allow `AF_INET` for `ai` steps (first draft)
- Pros: no relay code.
- Cons: the step can reach Redis and, as the same uid, read the worker's Redis password.
- Rejected by security (review 5.2).

### Fold AI into `ml`
- Pros: one fewer group.
- Cons: a resident LLM server has a different lifecycle and memory profile (8 GB+) than per-step
  ONNX models; enabling one would drag in the other.
- Rejected.

### Run the model in-process in each step (llama.cpp bindings)
- Pros: no server, no relay.
- Cons: reloads GBs of weights every step; minutes of overhead on small home-server CPUs.
- Rejected.

## Consequences
- Six worker groups, one seccomp filter. Documented minimum for `ai`: 8 GB RAM for a 3-4B Q4
  model; not offered on the 2-4 GB reference boxes.
- R2 work: the relay (with tests for the path allowlist, size caps, timeout and forced
  `num_predict`), `ollama` hardening and version probe, the planner's text-only rule, and the UI's
  safe rendering of answers.
- R1 needs nothing from this ADR except that the planner can prepend steps (already true) and
  the `inputs` multipart part name stays reserved (ADR-0003).
