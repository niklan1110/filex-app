# ADR-0008: Shared data volume, per-job directories, TTL retention, no database

## Status
Proposed. Amended 2026-10-10: retention 24 h, 1 GB uploads, no result cache, Redis key table,
per-owner job index.

## Date
2026-10-10

## Context
Files move between the API and workers in different containers, and between workers of
different groups in multi-step jobs. Weak hardware means files must stream to disk, not sit
in RAM or in Redis. Privacy means files must disappear on their own.

## Decision
- One named volume `filex-data` mounted at `/data` in the API and every worker.
- Layout: `/data/jobs/<id>/{in,steps/<n>,out}` and `/data/tmp/<step-id>` for scratch.
- Redis carries only ids and indexes; every path is derived from job id + step or output index
  (ADR-0007 amendment item 4).
- Inputs and intermediates are deleted when the job reaches a terminal state; outputs at
  `expiresAt` (default 24 h after finishing since the 2026-10-10 amendment; queued or running jobs capped
  at 24 h).
- Redis keys have TTLs; a janitor in the API (every 60 s and at startup) deletes job dirs
  without a live Redis key and stale scratch dirs.
- No SQL database. (Accounts, when used, live on the sign-in server; FileX still needs none, see the amendment.)

## Alternatives Considered

### Pass file bytes through Redis
- Rejected: RAM-bound, size-limited, defeats streaming.

### Object storage (MinIO/S3)
- Pros: multi-host ready.
- Cons: another always-on container and RAM on weak hosts; a single host doesn't need it.
- Deferred.

### SQLite/Postgres for job metadata now
- Pros: queryable history.
- Cons: history is exactly what we want to *not* keep; adds migrations.
- Deferred to accounts.

## Consequences
- Single-host only; scaling out needs shared storage.
- A Redis restart loses job metadata; the janitor then removes the orphaned files.
- Disk-full is handled by the API refusing uploads below `FILEX_MIN_FREE_MB` (`507`).

## Amendment (2026-10-10): defaults, Redis keys, owner index

Resolves spec open item A2 and the storage side of [ADR-0009](0009-keel-auth.md) and review
H5, H7, M4, M7.

- **Retention 24 h.** Outputs are kept `FILEX_RETENTION_MIN=1440` minutes after a job finishes
  (config validation caps it at 1440; the admin can lower it). Queued or running jobs are still
  capped at 24 h. The UI shows "expires in 23 h" and offers "Delete now".
- **Uploads up to 1 GB per job, streamed to disk.** `FILEX_MAX_UPLOAD_MB=1024` per job,
  `FILEX_MAX_FILE_MB` per file (default the same), `FILEX_MAX_FILES=50`. Bytes are counted while
  streaming; crossing a limit answers `413` at once and deletes the partial file. Uploads land on
  `/data` (`TMPDIR=/data/tmp`), never in RAM.
- **Free space.** The API refuses uploads with `507` when free space on `/data` would drop below
  `FILEX_MIN_FREE_MB` (default 4096) after the declared `Content-Length`. With 24 h retention,
  disk use is roughly 24 h of results plus up to a few in-flight jobs at about 3× their input
  size (input, intermediate, output); the install guide asks for at least 20 GB free on the data
  volume and says how to estimate more.
- **No result cache.** Decision, for privacy: every job runs fresh. If one is ever added it is keyed
  per principal and expires with the same retention (review H7, M7).
- **Stored names.** Inputs are `in/<n>.<ext of the sniffed format>`, auxiliary files
  `in/aux/<slot>.<ext>`, outputs `out/<index>.<ext>`. The original filename is kept for display
  only (NFC, no path parts or control characters, at most 200 characters).
- **Redis keys and who may write them** (ACL users from [ADR-0007](0007-sandboxing-untrusted-files.md)):

  | Key | Content | TTL | Written by |
  |---|---|---|---|
  | `job:<id>` | status, steps, progress, non-secret options, warnings, outputs | job expiry | api, worker |
  | `q:<group>` | stream of step entries (job id + step index only) | entries deleted on ack | api, worker |
  | `own:<id>` | owning principal | job expiry | api only |
  | `ownidx:<principal>` | sorted set of the principal's job ids by creation time | trimmed on read | api only |
  | `sec:<id>:<step>` | secret options for one step | 24 h, deleted on read | api (worker: `GETDEL` only) |
  | `worker:<group>:<id>` | heartbeat, tools whose probes passed, engine versions | 30 s | worker |
  | `slot:heavy:<i>` | heavy-step lease ([ADR-0002](0002-worker-groups-and-compose-profiles.md)) | 30 s, renewed | worker |
  | `rl:*` | rate-limit counters | 1-10 min | api only |

  Workers write with WATCH/MULTI/EXEC, so the `worker` ACL user needs `+@transaction`.

- **`GET /jobs` (per owner).** Reads `ownidx:<principal>` newest first (`?limit=` up to 100,
  default 20), drops ids whose `job:` hash has expired and removes them from the index. No other
  user's job can appear, and there is no list across users. The response is
  `{"items": [Job], "nextBefore"}`; `nextBefore` is the creation time in milliseconds of the
  page's last job, passed back as `?before=` (task 62: millisecond scores instead of the
  second-precision `createdAt`, so jobs created in the same second do not fall between pages).
- **Owner key lifetimes (task 62).** `own:<id>` is set with a 48 h TTL (24 h queued or running
  plus the 24 h retention cap), so it never expires before the job hash, which the runner can
  extend but `own:` it cannot write. A leftover `own:` grants nothing without the hash. The
  index is trimmed on every job creation to entries younger than 48 h and the newest 1000,
  and gets the same 48 h TTL. "Delete now" removes `own:<id>` and the index entry too.
- **Logs** carry job id, tool, principal, sizes, durations and error codes; never filenames,
  option values, tokens or email addresses. Engine stderr only at debug level, truncated to 2 KB.
  Docker log rotation on every service. The install guide asks the admin to exclude `filex-data`
  from snapshots and backups, and says deletion is an `unlink`, not shredding.
