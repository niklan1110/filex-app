# ADR-0009: Keel sign-in, Bearer-only API, allowlist, hashed static tokens, signed download links

## Status
Accepted (decisions of 2026-10-10; security sign-off with conditions in review section 5,
all folded in below). Supersedes [ADR-0006](0006-optional-shared-token-auth.md). Amended by
[ADR-0011](0011-public-local-mode.md): sign-in through Keel is the optional, advanced setup;
the public build runs in local mode or with access tokens.

## Date
2026-10-10

## Context
The shared password is replaced with sign-in through a self-hosted **Keel** server
(Supabase-compatible Auth) with a project for FileX, reached at `KEEL_URL` and typically
reachable from the public internet. FileX holds no JWT secret and no service-role key, so it
cannot verify tokens locally; Keel's own advice is to ask `GET /auth/v1/user`, which also rejects
signed-out sessions at once.

The security review (findings C1, C2, H7, M1-M3) adds hard
requirements: a valid Keel login is not enough (anyone who signs up gets one), there is no
unauthenticated mode by accident, no cookie session, no token in a URL, and every job route checks
the job's owner. Browsers cannot attach a Bearer header to a plain link, and a 1 GB result must not be
downloaded into a JS `Blob` (spec A8). The UI build must not bake in a deployment's Keel URL (A9).

## Decision

### Who can call the API
Every `/api/v1` route needs a principal, except `GET /health`, `GET /config` and `GET /dl/...`
(which carries its own signature, below). One FastAPI dependency `require_principal` resolves the
`Authorization: Bearer <value>` header, and nothing else (no cookie, no query string, no form
field):

| Bearer value | Checked by | Principal |
|---|---|---|
| starts with `fxt_` | locally, against `FILEX_API_TOKENS` | `token:<name>` |
| anything else | Keel `GET {KEEL_URL}/auth/v1/user` | `keel:<user id>` |

A FileX token never leaves the box; a Keel token is never checked locally.

**Keel tokens.** The API calls Keel with `apikey: <KEEL_ANON_KEY>` and the user's token, over one
shared `httpx.AsyncClient` (TLS verification on, no switch to turn it off; 3 s connect, 3 s read).
The response is untrusted: its shape is validated (UUID `id`, boolean `is_anonymous`, object
`app_metadata`) before use. Then:
- `200` with a valid body is the only response that can lead to a pass.
- Timeout, connection or TLS error, `5xx`, or `429` from Keel: **fail closed**,
  `503 AUTH_UNAVAILABLE` with `Retry-After: 10`. Never a pass, never cached.
- Any other status (`401`, GoTrue's `403` for `session_not_found` / `bad_jwt`, any other 4xx,
  an unexpected 2xx or 3xx) or a malformed `200` body: `401 UNAUTHENTICATED`. No status code
  falls through to a pass.
- `is_anonymous: true`: `403 USER_NOT_ALLOWED`.
- **Allowlist:** the user passes only if `id` is in `FILEX_ALLOWED_USER_IDS` (comma-separated
  UUIDs) or `app_metadata.filex` is `"user"` or `"admin"`. Never by email, never by
  `user_metadata` (users can edit it). Otherwise `403 USER_NOT_ALLOWED`. The decision uses the
  user record Keel just returned, which is current, not the claims inside the token.

**Cache.** Only successes are cached: key `sha256(token)` (never the raw token), value the
principal and the allow decision, TTL `min(60 s, exp - now)` (the unverified `exp` claim is read
only to shorten the TTL), bounded LRU of about 1000 entries, in process memory.

**Cache vs sign-out.** The UI calls `DELETE /api/v1/session` with its token, which evicts that
cache entry, and then `supabase.auth.signOut()` (default `global` scope). The up-to-60 s lag
remains only for sessions ended elsewhere (a ban, sign-out on another device). Keel's session
token lifetime is set to 900 s (checklist below), so a stolen access token alone dies
within 15 minutes.

**Static API tokens** for scripts. `FILEX_API_TOKENS=name1:<sha256 hex>,name2:<sha256 hex>` holds
hashes, not tokens. A token is `fxt_` + `secrets.token_urlsafe(32)`; the check hashes the
presented value and `hmac.compare_digest`s it against each configured hash. Unset means off.
- At startup every entry must be `<name>:<64 lowercase hex>`, names match `^[a-z0-9_-]{1,32}$`
  and are unique; otherwise the API refuses to start.
- A presented `fxt_` value shorter than 47 characters (`fxt_` + 43) is rejected with `401`
  before hashing.
- Tokens are never logged, and a failure is a plain `401`.

Rotation is: add the new hash, restart the API, move scripts, remove the old hash. Tokens keep
working while Keel is down.

**Startup rule (fail closed).** The API refuses to start unless at least one of these is true:
Keel is configured (`KEEL_URL` and `KEEL_ANON_KEY`), `FILEX_API_TOKENS` is set, or
`FILEX_AUTH=none` is set explicitly. `FILEX_AUTH=none` logs a warning on every start, makes every
request the principal `anonymous`, and `/config` reports `authMode: "NONE"`. ADR-0011 accepts `none` only on a loopback bind
(local mode), where the UI shows no banner. With Keel configured and `FILEX_ALLOWED_USER_IDS` empty, the API starts and logs
that only users with `app_metadata.filex` can sign in.

**Abuse limit.** More than 20 `401`s per client IP per minute gives `429 TOO_MANY_REQUESTS`
(Redis counter `rl:auth:<ip>`). Behind a reverse proxy on the same host every request comes from `127.0.0.1`, so
the client IP is taken from `X-Forwarded-For` only when the peer is in `FILEX_TRUSTED_PROXIES`
(default `127.0.0.1`).

### Ownership
Every route with `{jobId}` uses `require_job(job_id, principal)`, which answers
`404 JOB_NOT_FOUND` both for unknown jobs and for other principals' jobs. The owner lives in
`own:<job_id>`, a Redis prefix only the API's ACL user can write ([ADR-0007](0007-sandboxing-untrusted-files.md)),
set in the same `MULTI` that creates the job. A generic test walks the route table and fails if a
route with `{jobId}` lacks `require_job`. `GET /jobs` lists only the caller's own jobs
([ADR-0008](0008-storage-and-retention.md) has the index); there is no cross-user view in R1.
With `FILEX_AUTH=none` every caller is the single principal `anonymous`, so all jobs share one
owner and one list; that mode is meant for one trusted user. Another principal's `DELETE` answers
`204` and changes nothing, like a job that does not exist.

### Browser concerns
- **CORS:** none. The UI is served from the API's origin. If another origin is ever needed,
  `FILEX_CORS_ORIGINS` is an explicit list, never `*`, with `allow_credentials=False`.
- **CSRF:** nothing to ride on. There is no cookie, and credentials come only from the header.
- **Host header:** `TrustedHostMiddleware` with `FILEX_ALLOWED_HOSTS` (for example the host's DNS
  name and `localhost`) blocks DNS-rebinding requests, which matters most for the token-less
  `/health`, `/config` and `/dl/...`.
- **Headers on the app shell:** the strict CSP from review C2 fix 2, built at runtime with
  `connect-src 'self' <KEEL_URL origin>`, plus `Referrer-Policy: no-referrer` and
  `X-Frame-Options: DENY`. `KEEL_URL` is validated at startup as an `https` URL (the API refuses
  to start otherwise), and only its origin goes into `connect-src`. `Strict-Transport-Security`
  is sent once the UI is served only over HTTPS (`FILEX_HSTS=true`).
- **Outputs never render on the FileX origin.** Every route that serves output bytes
  (`/jobs/{id}/result`, `/jobs/{id}/outputs/{n}`, `/dl/...`) sends `Content-Disposition:
  attachment` (sanitised name, review M5), a `Content-Type` from FileX's format table (HTML,
  XHTML, SVG and XML as `application/octet-stream`; never sniffed, never from an engine),
  `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'` and
  `Cache-Control: no-store`. UI previews are PDF.js canvas renders of PDFs or worker-rendered
  PNG/JPEG shown in `<img>`; outputs are never put in an `iframe`, `object` or `embed`.
- **React rules** (lint-enforced where possible): `react/no-danger` (no
  `dangerouslySetInnerHTML`), no `innerHTML`, no `href`/`src` built from job data other than
  FileX API paths, and filenames, progress messages, warnings and errors rendered as text.
- **Token storage:** R1 accepts supabase-js's default `localStorage` on purpose. It would not stop
  an XSS from copying the token either way; the three rules above are the real defence, and the
  backend-for-frontend with an `HttpOnly` cookie stays under "Later".

### Downloads: short-lived signed links (spec A8)
Scripts download with their Bearer header (`GET /jobs/{id}/result`, `/outputs/{n}`). The browser
asks for a link when the user clicks Download:

```
POST /api/v1/jobs/{jobId}/result/link            -> 200 { "url": "/api/v1/dl/...", "expiresAt": "..." }
POST /api/v1/jobs/{jobId}/outputs/{index}/link   -> same
GET  /api/v1/dl/{jobId}/{target}?exp=<unix>&sig=<base64url>   (no Authorization header)
```

- `target` is `result` or an output index. `sig = HMAC-SHA256(key, "dl\n{jobId}\n{target}\n{exp}")`.
  `exp` is at most 5 minutes ahead. The UI sets `window.location` (or an `<a download>`) to the
  URL, so the browser streams straight to disk; `Range` works, so a resumed download within the
  window succeeds.
- Minting goes through `require_job`, so the owner check happens there.
- **Redeeming, in this order:** (1) job id matches the regex, `target` is `result` or an
  integer, `exp` is an integer with `now < exp <= now + 300`; (2) the HMAC, with
  `compare_digest`; (3) only then Redis: the job exists and the index is below its output count.
  Every failure returns the same `404 JOB_NOT_FOUND` and counts toward `rl:auth:<ip>`. A deleted
  or expired job makes every link dead at once.
- **The key** is 32 random bytes generated at API start and kept only in memory, which works
  because the API runs as **one uvicorn process**. If it ever runs more, the key comes from a
  Docker secret file instead; it never goes in Redis or `.env`. An API restart invalidates
  outstanding links, which costs the user one more click.
- The link is a capability for one output for 5 minutes, not a credential. **`sig` is never
  logged:** the access log drops the query string on every route, and a test downloads through
  `/dl/...` and asserts `sig=` is absent from the captured log. `Referrer-Policy: no-referrer`
  keeps it out of `Referer`.
- Responses use the output rules under "Browser concerns" above.

### Runtime config for the UI (spec A9)
`GET /api/v1/config`, no auth, built from env at request time:

```json
{ "authMode": "KEEL", "keelUrl": "https://keel.example.com/filex", "keelAnonKey": "eyJ...",
  "limits": { "maxUploadMb": 1024, "maxFiles": 50, "retentionMin": 1440 }, "version": "1.0.0" }
```

`authMode` is `KEEL` (Keel configured, static tokens may also be on), `TOKEN` (static tokens
only: the UI asks for a token instead of an email) or `NONE`. The UI fetches it before calling
`createClient`. `VITE_KEEL_URL` / `VITE_KEEL_ANON_KEY` remain a dev-server fallback only, so a
published image carries no deployment-specific value. The anon key is public by design.

### Admin checklist in Keel Studio (FileX's project)
Sign-ups off (invite users instead), email confirmation on, minimum password length 12,
anonymous sign-ins off, session token lifetime 900 s, Site URL set to FileX's address.
Give each invited user `app_metadata.filex = "user"` or add their id to
`FILEX_ALLOWED_USER_IDS`. **Ban user** ends every session of a compromised account.

## Alternatives Considered

### Verify Keel JWTs locally with the project's JWT secret
- Pros: no network call per minute per user; works while Keel is down.
- Cons: the JWT secret is Keel's master key (it can mint service-role tokens); local
  verification cannot see sign-outs or bans.
- Rejected: decision, and the review's asset list puts that key out of scope for FileX.

### Keep a cookie session (ADR-0006) next to the Bearer header
- Pros: plain `<a href>` downloads work with no link endpoint.
- Cons: brings back CSRF and `SameSite` subtleties; two auth paths to test.
- Rejected; signed links cover downloads with less surface.

### One-time Redis-stored download tokens (review M3 fix 3)
- Pros: a leaked link is useless after the first request.
- Cons: Redis state per click; a one-time link breaks `Range` resume of a 1 GB download.
- Rejected in favour of a 5-minute stateless HMAC link bound to one job and target. Accepted in the
  security review with conditions (review 5.1), all listed under Downloads.

### Download URLs minted inside every `GET /jobs/{id}` (spec A8 as proposed)
- Pros: no extra request before a download.
- Cons: a fresh signature on every poll changes the body and defeats `ETag` / `304`, and mints
  links nobody uses.
- Rejected; minting on click is one small request.

### Backend-for-frontend with the refresh token in an `HttpOnly` cookie
- Pros: an XSS cannot copy a refresh token.
- Cons: FileX becomes a session store and needs CSRF defences again.
- Deferred ("Later" in the review) until FileX is reachable from outside a private network.

## Consequences
- Auth is one dependency plus one small Keel client; accounts already exist, so `GET /jobs`
  (per owner) ships in R1.
- Keel availability matters for UI users: when Keel is down the UI shows "Sign-in server
  unreachable" and scripts with `fxt_` tokens keep working.
- New error codes in the contract: `401 UNAUTHENTICATED`, `403 USER_NOT_ALLOWED`,
  `503 AUTH_UNAVAILABLE`, `429 TOO_MANY_REQUESTS`.
- Endpoints added: `GET /config`, `DELETE /session`, `GET /jobs`, the two `.../link` routes and
  `GET /dl/...`. `POST /session` (password login) is gone.
- Tests: Keel mocked for each branch above (allowed, not allowed, anonymous, 401, 403, 429, timeout, 5xx,
  malformed body), static token match and mismatch, startup refusal with no auth, cache eviction
  on `DELETE /session`, link signature tamper and expiry, and the `require_job` route walk.
  From review section 5: startup refusal for a malformed or duplicate `FILEX_API_TOKENS` entry
  and for a non-`https` `KEEL_URL`; a short `fxt_` value rejected before hashing; `/dl/...`
  failures all return the same `404` and count toward `rl:auth`; `sig=` absent from the captured
  access log; the attachment headers on all three output routes, with an HTML output served as
  `application/octet-stream`; a lint rule `react/no-danger` in the UI build.
