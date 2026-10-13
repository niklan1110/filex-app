# ADR-0011: Public build runs in local mode; LAN access through access tokens

## Status
Accepted (decision of 2026-10-13). Amends [ADR-0009](0009-keel-auth.md): sign-in through a Supabase-compatible server stays, but as
an optional, advanced setup instead of the expected one.

## Date
2026-10-13

## Context
FileX is published as open source for people who run it on their own computer for personal
use. Most of them have no Supabase-compatible sign-in server and should not need one to
merge two PDFs. Today the API refuses to start unless a sign-in server or `FILEX_API_TOKENS` is set, or
`FILEX_AUTH=none`; compose publishes the port on `${FILEX_BIND:-127.0.0.1}`. Nothing stops
`FILEX_AUTH=none` together with a LAN or `0.0.0.0` bind, which would hand an open FileX (every
job visible to every caller, ADR-0009 "Ownership") to the whole network.

Deployments that use server sign-in (ADR-0009) must not get weaker. Both kinds of deployment
share one codebase.

## Decision
1. **Local mode is the public default.** `FILEX_AUTH=none` with a loopback bind: no sign-in, only
   the computer FileX runs on can reach it. The UI shows no sign-in screen and no warning banner
   (the API only allows this mode on loopback, item 2).
2. **Guard.** Compose passes `FILEX_BIND` into the api container (the same value it publishes the
   port on). With `FILEX_AUTH=none` the API refuses to start unless `FILEX_BIND` is a loopback
   address (`127.0.0.0/8`, `::1`, `localhost`); the message says to set `FILEX_API_TOKENS` and
   leave `FILEX_AUTH` empty. With a sign-in server or tokens any bind is accepted, as before. The existing
   "refuse to start without any sign-in" rule is unchanged.
3. **Other devices on the home network use static access tokens.** `FILEX_BIND=<LAN IP>`, that IP
   in `FILEX_ALLOWED_HOSTS`, `FILEX_API_TOKENS=phone:<sha256>`, `FILEX_AUTH=` empty. The UI's
   existing token sign-in takes the `fxt_` token once per browser. No new auth code.
4. **Server sign-in stays in the code** as the optional advanced setup; it is simply not configured in
   the public build.
5. **How the public build gets local mode by default:** the export script
   (`scripts/export_oss.py`) copies a public `.env.example` (kept in `oss/.env.example`) over the
   repository's one. It has `FILEX_AUTH=none`, `FILEX_BIND=127.0.0.1`, the access-token steps, and
   no sign-in server block. The repository's own `.env.example` keeps `FILEX_AUTH=` empty, so the private
   setup still refuses to start until sign-in is configured. Compose defaults are not changed.

## Consequences
- A public user runs `cp .env.example .env`, creates the Redis secrets and `docker compose up -d`,
  and gets a working FileX on `http://127.0.0.1:8080` with no account.
- Changing `FILEX_BIND` alone can no longer expose an open FileX: the API stops with a clear
  message instead.
- `FILEX_BIND` is what the API is told, not what it observes. Running uvicorn by hand on
  `0.0.0.0` without compose is not caught; that is a developer path, not the documented one.
- With no credential to forge, other websites in the user's browser are the remaining way in. In
  local mode the API refuses `/api` requests the browser marks `Sec-Fetch-Site: cross-site` or
  whose `Origin` is not in `FILEX_ALLOWED_HOSTS`, and refuses wildcards in `FILEX_ALLOWED_HOSTS`
  (DNS rebinding). Added by the public release security review.
- LAN access is plain HTTP; tokens cross the network in the clear. The install guide says to
  keep FileX on the home network and never forward the port. HTTPS needs a reverse proxy.
- Two `.env.example` files must be kept in step when a setting is added; the export fails if
  the public one is missing.
- The private deployment is unaffected: its `.env` sets the sign-in server, and a non-loopback bind with it
  passes the guard.

## Alternatives considered
- **Strip server sign-in from the public code.** Rejected: two codebases (or a patching step) to keep in
  sync, for no security gain, because unconfigured sign-in code does nothing.
- **Default `FILEX_AUTH` to `none` in compose (`${FILEX_AUTH:-none}`).** Rejected: a private
  install with a missing or mistyped `.env` would start open instead of refusing.
- **Have the export rewrite `FILEX_AUTH=` to `none` in the existing `.env.example`.** Simpler,
  but the private file's comments describe one deployment's network and sign-in setup, which the
  public file must not carry.
- **Local accounts in FileX.** Rejected for now: a user database, password hashing and resets
  for a single-person tool; access tokens already cover the LAN case.
