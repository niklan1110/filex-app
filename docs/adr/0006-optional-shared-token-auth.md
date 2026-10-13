# ADR-0006: Optional single shared secret; cookie for the UI, Bearer token for the API

## Status
Superseded by [ADR-0009](0009-keel-auth.md) (2026-10-10). Kept for history; nothing below
applies any more: no shared password, no cookie, no unauthenticated default.

## Date
2026-10-10

## Context
FileX is reached privately over a VPN by its admin and a few people. Real accounts come
later. Uploaded documents are private, so a shared secret should be available, but forcing
one adds friction where the VPN already limits who can connect.

## Decision
- One env var `FILEX_PASSWORD`. Unset: no auth, and the API logs a warning at startup and
  reports `authEnabled: false` on `/health`.
- Set: every `/api/v1` route except `/health` and `POST /session` requires it.
  - REST clients send `Authorization: Bearer <FILEX_PASSWORD>`.
  - The UI posts the password to `POST /session`, which sets an HMAC-signed, `HttpOnly`,
    `SameSite=Strict` session cookie (`Secure` when served over HTTPS). The signing key is
    derived from the password, so changing it logs everyone out.
- Constant-time comparison; failed logins rate-limited per client IP (Redis counter).
- Job ids are 128-bit random, so job URLs are not enumerable.
- All checks live in one FastAPI dependency; accounts later replace one function.

## Alternatives Considered

### Separate API token and UI password
- Pros: rotate independently.
- Cons: two secrets for a handful of trusted users.
- Deferred; additive later as `FILEX_API_TOKEN`.

### Reverse-proxy basic auth or VPN identity headers only
- Pros: no auth code.
- Cons: ties FileX to one deployment shape; basic auth makes the UI flow clumsy.
- Rejected as the only mechanism; still works in front of FileX if the admin wants it.

### Accounts now (OIDC, local users)
- Rejected: not requested, needs a database.

## Consequences
- Anyone with the password can open any job whose id they know; there is no job list
  endpoint until accounts exist.
- Open question: refuse to start without a password, or start open with a
  warning (current choice).
