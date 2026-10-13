# ADR-0004: REST + OpenAPI generated from the tool registry, committed and diff-checked

## Status
Proposed

## Date
2026-10-10

## Context
The web UI, scripts using the REST API, and the docs all consume the same API. With 100+
tools, each with its own options, a hand-maintained contract would drift. Contract-first
work between the backend and the frontend needs one authoritative, machine-checkable
artifact.

## Decision
- Style: resource-oriented REST under `/api/v1`, JSON in camelCase, enum values
  UPPER_SNAKE, multipart for uploads, one error envelope
  `{"error": {"code", "message", "details"}}`, async jobs (`202` + poll).
- The authoritative source of each tool's contract is its declaration (ADR-0003); the
  source of the job resources is the pydantic models in `filex.api.schemas`.
- FastAPI generates OpenAPI 3.1 from both. The output is committed as
  `docs/api/openapi.json`; CI regenerates it and fails on any diff, so every contract change
  is visible and reviewed in the PR.
- The UI generates its TypeScript types from that file (`openapi-typescript`) and fetches the
  live catalog at runtime for availability.
- Versioning: additive changes stay in `v1`; a breaking change needs a new ADR.

## Alternatives Considered

### Hand-written OpenAPI first, code generated from it
- Pros: purest contract-first.
- Cons: per-tool options would be written twice (OpenAPI + run code) for 100+ tools.
- Rejected: the tool declaration is written before the implementation and plays the
  contract role; the committed file makes it reviewable.

### GraphQL / tRPC / gRPC
- Pros: typed clients.
- Cons: file upload/download and curl-friendliness are worse; no benefit for a small API.
- Rejected.

## Consequences
- Contract changes show up as `openapi.json` diffs; reviewers check them like code.
- One `POST /tools/{toolId}/jobs` route per tool keeps every options schema typed in the
  contract, at the cost of a large but regular OpenAPI file.
- OpenAPI generation must be deterministic (stable ordering) for the diff check to work.
