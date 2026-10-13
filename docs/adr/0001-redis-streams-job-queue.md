# ADR-0001: Redis Streams as the job queue, with a thin in-house runner

## Status
Accepted. Amended 2026-10-13: the server is Valkey 8, a BSD-3-Clause fork of Redis that keeps the same Streams, consumer groups and ACLs; Redis 7.4 is not open source (RSALv2/SSPL).

## Date
2026-10-10

## Context
FileX runs document jobs on separate worker containers, one per engine group (ADR-0002).
The queue must:
- route a step to the right group, and let a multi-step job hop between groups
  (DOCX in `office`, then EPUB in `ebook`);
- survive a worker container dying mid-step (redeliver, but not forever: a file that
  crashes a worker must not loop);
- carry progress and status cheaply for polling clients;
- stay tiny on a 2-4 GB machine and run on x86-64 and ARM64.

Every step already runs in its own child process with timeouts and rlimits for sandboxing
(ADR-0007), so the queue library does not need to provide timeouts or process isolation.
Redis is already in the proposed stack.

## Decision
Use Redis 7 Streams directly:
- one stream per worker group (`q:core`, `q:office`, ...), one consumer group per stream;
- workers `XREADGROUP`, run the step, then in one `MULTI`: update `job:<id>`, `XADD` the next
  step to its group's stream (or mark the job finished), `XACK`;
- a worker periodically `XAUTOCLAIM`s entries idle longer than the step timeout + grace;
  the stream's delivery counter caps redelivery at 2, after which the job fails with
  `WORKER_LOST`;
- job state is a Redis hash `job:<id>` with a TTL; workers heartbeat `worker:<group>:<id>`
  (TTL 30 s) so the API knows which groups are online.

The runner (`filex.worker`) is expected to stay around 200 lines. Redis runs with
persistence off: a Redis restart loses in-flight jobs, which clients then see as `404`.

## Alternatives Considered

### Celery
- Pros: mature, chains, retries, many brokers.
- Cons: heavy dependency tree and per-worker memory, prefork model duplicates our own
  sandboxed child process, configuration surface far larger than our needs.
- Rejected: too much machinery for weak hardware and five small queues.

### RQ
- Pros: simple, fork-per-job, timeouts, result TTL, `depends_on` across queues.
- Cons: pickled job payloads, fork-per-job on top of our own child process; cross-group
  chains and our progress model still need custom code.
- Rejected narrowly. Runner-up: revisit if our runner grows past ~300 lines.

### arq / Dramatiq / Huey
- Pros: lighter than Celery.
- Cons: same picture as RQ; each adds a framework without removing our custom parts.
- Rejected.

### No Redis (SQLite-backed queue)
- Pros: one less container.
- Cons: SQLite on a volume shared by several containers is fragile with concurrent
  writers; Redis costs ~10 MB.
- Rejected.

## Consequences
- We own a small amount of queue code; it needs tests for redelivery, the delivery cap and
  cancellation.
- At-least-once delivery: steps must be idempotent (write only to their own step dir,
  cleared before start).
- Adding a group is adding a stream name; no broker configuration.
- Acked entries are `XDEL`eted, so `XLEN` equals waiting + in-flight steps and backs the
  API's `429 QUEUE_FULL` check.
- With persistence off, a Redis restart also drops the streams and consumer groups: the
  runner creates them on startup (`XGROUP CREATE ... MKSTREAM`) and again on a `NOGROUP`
  error.
- Turning on Redis AOF later makes queued jobs survive restarts without code changes.

## Amendment (2026-10-10, task 5): the runner's real size

The "about 200 lines, revisit past ~300" budget is crossed: `filex/worker/runner.py` is about
1000 lines, plus `step.py` and `sandbox.py` (about 290 together). Almost all of the growth
came with later requirements, not queue code: the per-step sandbox and escaped-process sweep
(ADR-0007), engine version probes (review H4), secrets over stdin (H5), the heavy-step slot
(ADR-0002), output checks, and the security review's fixes (ready handshake, output and disk
caps, cancel cleanup). The Streams part (claim, touch, hand-off, ack) is still about 150 lines.
RQ and its peers still lose: none removes the sandbox, probe, secret or slot code, and each adds
pickled payloads and its own fork per job. New budget: the queue part stays under ~200 lines;
revisit this choice if it grows past that, not when sandbox code does.
