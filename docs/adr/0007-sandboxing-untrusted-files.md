# ADR-0007: Sandboxing untrusted files: hardened no-egress workers, one limited child per step

## Status
Accepted (2026-10-10; security sign-off in review section 5).
Amended 2026-10-10 after the security architecture review:
see "Required before release 1", the rewritten Consequences, and Appendix A.
Amended 2026-10-11: Landlock per-step confinement moved from "Later" to item 11 (accepted, on by
default where the kernel has it).

## Date
2026-10-10

## Context
Every uploaded file is untrusted and is parsed by large C/C++ codebases (Ghostscript,
LibreOffice, Calibre, Tesseract, image codecs) with a history of memory-safety bugs and
features such as macros and external references. A malicious or broken file must not reach
the network or the host, and must not exhaust the machine. The deployment target (a home server or
appliance-style OS) may not support extra container runtimes.

## Decision
Defense in depth with what every Docker host has:

1. **Network:** workers attach only to the compose network `internal` (`internal: true`, no
   route out). Only the API publishes a port. Redis requires a password.
2. **Container:** non-root user, `read_only: true`, `cap_drop: [ALL]`,
   `no-new-privileges`, `pids_limit`, `mem_limit`, `cpus`, small `tmpfs` `/tmp`; only
   `/data` is writable.
3. **Per step:** the runner spawns a fresh child (`python -m filex.worker.step`) per step in
   its own process group, with a wall-clock timeout (from the tool's `limits`),
   `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, a private scratch dir, and an
   environment without the Redis password. Timeout or cancel kills the whole group.
4. **Engine flags:** Ghostscript `-dSAFER`; LibreOffice macros off, fresh user profile per
   run, no remote resource loading; Pillow decompression-bomb limit kept; zip-based inputs
   (DOCX, EPUB, CBZ) checked for entry count, uncompressed size and path traversal before
   extraction.
5. **API:** size and count caps, magic-byte sniffing, constant-time auth, engine stderr never
   returned to clients.
6. **Lifecycle:** inputs deleted when a job ends, outputs after retention, janitor sweeps
   orphans (ADR-0008).

Opt-in stronger isolation: a compose override setting `runtime: runsc` (gVisor) on worker
services, for hosts that have it. Not required.

## Alternatives Considered

### gVisor / Kata / Firecracker as a requirement
- Pros: much stronger kernel isolation.
- Cons: not available on appliance-style Docker hosts or Docker Desktop by default.
- Rejected as a requirement; supported as opt-in.

### nsjail / bubblewrap per step inside the container
- Pros: per-step namespaces (no network even to Redis, per-job filesystem view).
- Cons: needs extra privileges (user namespaces or `CAP_SYS_ADMIN`) that weaken the
  container hardening above, and behaves differently across hosts.
- Deferred; revisit with `security` once the runner exists.

### Engines in-process in the long-lived worker
- Pros: faster (no ~150 ms child start).
- Cons: one crash or leak takes down the worker and affects other jobs; limits can't be set
  per step.
- Rejected.

## Required before release 1 (amendment, 2026-10-10)

From review section 4. Each item is small and needs no extra container, privilege or RAM.

1. **Per-step seccomp filter (H1 fix 1).** The step child installs a libseccomp filter
   (`python3-seccomp`) before it runs the engine; allowed because `no-new-privileges` is set.
   It denies `socket()` and `socketpair()` for every family except `AF_UNIX` (which LibreOffice
   and Qt WebEngine use internally), and denies `ptrace`, `process_vm_readv`,
   `process_vm_writev`, `keyctl`, `bpf`, `userfaultfd`, `perf_event_open`, `mount`, `io_uring_*`,
   `pidfd_send_signal`, `pidfd_getfd`, and the stop signals (19-22) in `kill`, `tkill`, `tgkill`,
   `rt_sigqueueinfo` and `rt_tgsigqueueinfo` (task 5 security review H-1, L-1). A step then
   cannot reach Redis, the API, the gateway or anything else. Each engine is tested under the
   filter in CI. **One filter for every group, with no exceptions:** `ai` steps reach the
   inference server through an `AF_UNIX` relay run by their runner
   ([ADR-0010](0010-ai-worker-group.md)), and no tool or group loosens the filter.
   Docker's default seccomp profile stays on the container (never `seccomp:unconfined`).
2. **Kill escaped processes (H1 fix 2).** The runner sets `PR_SET_CHILD_SUBREAPER` and keeps
   `PR_SET_DUMPABLE=0`. After every step, and every 5 s, it kills any process in the container
   whose session id is neither its own nor that of a step still running. `setsid()` or a double
   fork therefore no longer outlives the step.

   **Worker containers run without `init` (deviation from A.1, task 5 security review H-1).**
   A step runs as the runner's uid in its PID namespace, so with `init: true` it could
   `kill(getppid(), SIGSTOP)` and freeze the runner: no timeout, cancel, sweep or heartbeat,
   while the step keeps `/data`. Without `init`, the exec-form `CMD` makes the runner PID 1, and
   the kernel drops every signal sent to PID 1 from inside its namespace that has no handler,
   SIGSTOP and SIGKILL included; SIGTERM and SIGINT start the graceful stop. As PID 1 the runner
   reaps zombies in its 5 s sweep. The seccomp stop-signal rule (item 1) is the second layer.
   The step child also makes itself non-dumpable and sets `oom_score_adj` 1000 before the runner
   sends it any secret (review M-3, L-5); stdin and stdout are one `AF_UNIX` socket, which a
   sibling step cannot open through `/proc/<pid>/fd`.
3. **Redis ACLs instead of one `requirepass` (H1 fix 3, L3).** Default user off. User `api`:
   all FileX prefixes. User `worker`: `~q:* ~job:* ~worker:* ~slot:* &* -@all +@read +@write
   +@stream +@connection +@transaction -@admin -@dangerous` (`+@transaction` is only MULTI, EXEC,
   DISCARD, WATCH and UNWATCH, which the runner's job writes use; commands inside EXEC are still
   key-checked), plus the selector `(~sec:* +getdel)` for step
   secrets ([ADR-0003](0003-tool-plugin-model.md)). Ownership, owner indexes and rate limits
   (`own:*`, `ownidx:*`, `rl:*`) are not writable by `worker`. The ACL file is mounted read-only;
   passwords come from Docker secret files. Valkey 8, like Redis 7, defaults `enable-protected-configs no`,
   `enable-debug-command no`, `enable-module-command no` stay.
4. **Paths from job id and step index only (H1 fix 4, H3 fix 5).** Workers and the API build
   every path as `/data/jobs/<job_id>/{in,steps/<n>,out}` from a job id that matched
   `^j_[A-Za-z0-9_-]{22}$` and an integer step or output index; no path string read from Redis
   or a stream entry is ever opened. Stream entries are untrusted input.
5. **128-bit random job ids (H7).** `"j_" + base64url(secrets.token_bytes(16))` (22 characters,
   no padding). Not ULIDs: a ULID spends 48 bits on a timestamp.
6. **No network in any worker (H6).** Workers attach only to `internal` (`internal: true`,
   `enable_ipv6: false`). No group gets egress in R1, including `office` and `ebook`. Chromium
   is not shipped; HTML to PDF uses WeasyPrint with a restricted fetcher (Appendix A).
   Allow-listed egress is a later option with its own ADR (see below). M6: verify from inside
   `worker-core` that the bridge gateway does not expose host services; pin the subnet and add a
   host rule if it does.
7. **Input-only scratch dir (H2 fix 1).** The step's scratch dir holds a hard link of its input
   and nothing else; `cwd`, `HOME`, `TMPDIR` and every engine config and cache dir point inside
   it; the engine gets the input by its scratch path. The child's environment is an allowlist
   (`PATH`, `LANG`, `HOME`, `TMPDIR`, `OMP_THREAD_LIMIT=1`, engine variables from Appendix A).
8. **Runner checks outputs (H3 fix 5).** The runner, not the child, `lstat`s every output
   (regular file, not a symlink, `st_nlink == 1`) and links it into `out/<index>.<ext>`. The API
   opens results with `O_NOFOLLOW` and checks `fstat` (regular, `st_nlink <= 2`).
9. **Archive, pixel and page limits (H3 fixes 1-4)**, `defusedxml` for our own XML parsing
   (H2 fix 3), the LFI corpus test per engine (H2 fix 4), format allowlists, weekly image
   rebuilds and minimum engine versions enforced by the engine probe (H4).
10. **Engine settings:** Appendix A, checked by `security` for every new engine before its
    first tool merges.
11. **Landlock per-step filesystem view (accepted 2026-10-11, review section 3).** The LFI corpus
    found two engines reading another job's files through references in a document (LibreOffice
    RTF `INCLUDEPICTURE`, Calibre DOCX external image relationships). Per-format pre-checks
    refuse those, but every new format or engine feature can open another hole; Landlock closes
    the class. After rlimits and before the seccomp filter, the step child
    (`filex/worker/sandbox.py`, ctypes, no new dependency) restricts itself and its children to:
    - read and execute: `/usr`, `/lib`, `/lib64`, `/lib32`, `/bin`, `/sbin`, `/etc`, `/opt`,
      `/proc`, `/sys`, `/var/cache/fontconfig` and Python's import path (`sys.path`); one list
      for every group, tested with each group's whole tool suite in its image;
    - read and write (every handled right): its scratch dir `/data/tmp/<job>.<n>` (which holds
      the hard-linked inputs, `HOME`, `TMPDIR`, engine profiles and caches) and its output dirs
      `steps/<n>/w<k>`;
    - read and write, files only: `/dev/null`, `/dev/zero`, `/dev/full`, `/dev/random`,
      `/dev/urandom`;
    - `/tmp`: create and remove sockets only (LibreOffice binds its `OSL_PIPE_*` there whatever
      `TMPDIR` says); not listable, readable or writable. `/dev/shm` is not granted.
    - ABI 4+: TCP bind and connect denied (no port rule), a second barrier next to seccomp;
      ABI 6+: signals and abstract UNIX sockets scoped to the step.
    Everything else is denied: `/data/jobs`, other scratch dirs, `/run/secrets`. The handled
    rights are everything the kernel's ABI knows (REFER, TRUNCATE, IOCTL_DEV as available). ABI 1
    is treated as unavailable: it refuses every rename across directories.

    **Policy `FILEX_LANDLOCK` = `auto` (default) | `required` | `off`.** The runner probes once at
    start with `landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)` (the
    `/sys/kernel/security/lsm` file is not readable in a container) and passes the ABI to each
    step; a child that fails to apply a ruleset the runner found fails the step with
    `SANDBOX_FAILED` and never runs the engine unrestricted. `auto` enforces when the kernel can
    and otherwise runs with the H1 baseline; `required` makes a worker without Landlock offer no
    tools; `off` is for debugging an engine. The default is `auto`, not `required`, because many
    home-server kernels are not known to have Landlock and FileX must still run there; the state is
    visible, so the choice is never silent: the worker heartbeat and `GET /api/v1/status` report
    `isolation: {seccomp, landlock: "abi<N>" | "unavailable" | "off"}` per group (the weakest
    live worker), next to the top-level `isolation: "container"` baseline. Measured on Docker
    Desktop (WSL2 kernel 6.18): ABI 7 as uid 10001 with `cap_drop: ALL`, `no-new-privileges` and
    Docker's default seccomp profile.

### Later
- **Allow-listed egress** (spec A6: TSA/OCSP for signing timestamps, an Ollama on another
  machine on the network, URL to PDF): only through a filtering egress proxy with a per-group host
  allowlist, and only in a design that does not re-open Redis or the API to a step. Needs its
  own ADR before any of those features is built.
- gVisor (`runtime: runsc`) override, tested on hosts that support it.

## Consequences
- After items 1-4, a compromised step can read and write `/data` **only while it is alive**.
  It cannot persist past its step (item 2), cannot open a network socket and so cannot touch
  Redis, the API or the host (item 1), and could not rewrite ownership even through a
  compromised runner (item 3). **While it runs, a compromised step can read every file on
  `/data`**: other users' inputs and uploaded `.p12` files, and up to 24 h of every user's
  retained outputs, and it can return any of them as its own result. With Landlock (item 11)
  enforced this shrinks to "a compromised step can damage only its own job"; on a kernel without
  it (`landlock: "unavailable"` in the status) the residual above still holds, and the sign
  tool's help says so.
- Landlock's `/tmp` socket right lets a step create and unlink sockets (and unlink files whose
  names it knows) in the container's `/tmp`; it cannot list it. Pathname UNIX sockets can be
  connected to without a filesystem right (Landlock up to ABI 7 has no right for that). The
  LibreOffice pipe names hash the step's scratch path, but that path is not secret:
  `/proc/<pid>/cmdline` is readable, so a step can read a sibling `soffice`'s
  `-env:UserInstallation=`, derive its `OSL_PIPE` name, then `connect()` to or `unlink()` it
  (security review L-2). The defence is that there is no sibling: `worker-office` always runs one
  step at a time (`FILEX_CONCURRENCY: "1"` in `compose.yaml`, not overridable from `.env`). Never
  raise it; any new engine that listens on a socket named from its arguments needs the same pin.
- The Redis secret no longer matters to a step: it has no socket to use it with.
  `PR_SET_DUMPABLE=0` stays as hygiene.
- Calibre's Qt WebEngine runs with `--no-sandbox`. Accepted only together with no network, the
  seccomp filter and the container baseline; revisit if any of those is weakened.
- ~150 ms overhead per step from process start, plus a few ms for the filter.
- `security` reviews every new engine's flags (Appendix A) before its first tool merges, and CI
  runs every engine under the seccomp filter and the LFI corpus.

## Appendix A: configuration baseline

Adopted from review section 2 (H4 fix 1). The real `compose.yaml` follows it (its
`x-hardening` block is this baseline), and so do the engine wrappers. Memory values
follow [ADR-0002](0002-worker-groups-and-compose-profiles.md).

### A.1 Containers

```yaml
x-hardening: &hardening
  user: "10001:10001"                        # non-root; owns nothing inside the image
  read_only: true
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]   # keep Docker's default seccomp; never "seccomp:unconfined"
  init: true
  ipc: private
  pids_limit: 256
  ulimits: { nofile: 1024, core: 0 }
  tmpfs: ["/tmp:size=64m,mode=1777,nosuid,nodev,noexec"]
  logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }

services:
  worker-core:
    <<: *hardening
    networks: [internal]                     # the only network: no egress
    volumes: ["filex-data:/data"]
    mem_limit: 1g
    memswap_limit: 1g                        # no swap beyond the limit
    cpus: 1.5
networks:
  internal: { internal: true, enable_ipv6: false }
```

- Applies to every service, `api` and `redis` included (review L3), each with its own non-root
  uid. Never `privileged`, never the Docker socket, no host bind mounts, no `ports:` on workers
  or Redis. The API publishes `"${FILEX_BIND:-127.0.0.1}:8080:8080"` only.
- Per-step limits stay (timeout, `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`).
  The runner clamps a tool's `memory_mb` to its container's limit. Qt WebEngine and Chromium
  steps use `RLIMIT_DATA` instead of `RLIMIT_AS` (their JS engines reserve huge virtual ranges).

### A.2 Engines

| Engine | Settings |
|---|---|
| **Ghostscript** | `-dSAFER -dBATCH -dNOPAUSE -dQUIET`, `--permit-file-read=<input path>`, output only into the scratch dir. Never `-dNOSAFER` or `-dDELAYSAFER`. Called only on files that start with `%PDF-` (no PostScript or EPS input in R1). Minimum version 10.03.1 (H4). Prefer qpdf, pikepdf or pypdfium2 where a tool can. |
| **libvips** (preferred for images) | `VIPS_BLOCK_UNTRUSTED=1`. Open with `access="sequential"`, check the pixel count first. |
| **ImageMagick** | Not installed in R1 unless a tool needs a format libvips lacks. If installed: the `policy.xml` below, inputs always with an explicit format prefix (`png:file`). |
| **Pillow** | `Image.MAX_IMAGE_PIXELS = FILEX_MAX_PIXELS` (default 100 MP), `DecompressionBombWarning` as error, `LOAD_TRUNCATED_IMAGES = False`. |
| **LibreOffice** (`office`) | One `soffice --headless --invisible --norestore --nologo --nodefault --nolockcheck -env:UserInstallation=file://<scratch>/lo-profile --convert-to ... --outdir <scratch>/out <scratch>/input.<ext>` per step, killed with the step; no `--accept` listener. Profile copied from a read-only template with macro security "very high", `DisableMacrosExecution=true`, `BlockUntrustedRefererLinks=true`, `DisableActiveContent=true` (24.8+), link updates never in Writer and Calc. Key names checked against the installed version by the LFI corpus test. No JRE, no extensions, no script-provider packages. |
| **Pandoc** (`ebook`) | `--sandbox` on every run. User options never reach `--filter`, `--lua-filter`, `--template` or `--resource-path`. |
| **WeasyPrint** (`office`, R2) | Python API only: `HTML(filename=..., base_url=<scratch>, url_fetcher=restricted_fetcher)`; the fetcher allows `data:` and files resolved inside the scratch dir, and raises for everything else. |
| **Calibre** (`ebook`) | `ebook-convert` only. `CALIBRE_CONFIG_DIRECTORY`, `CALIBRE_TEMP_DIR`, `CALIBRE_CACHE_DIRECTORY` inside the scratch dir; `QT_QPA_PLATFORM=offscreen`; `QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"`. Input allowlist: the spec's R1 ebook formats only (no CHM, LIT, LRF, PDB, RB, SNB, TCR in R1). Timeout 300 s, `RLIMIT_DATA` about 900 MB (not `RLIMIT_AS`: Qt WebEngine reserves far more address space than it uses). DRM-protected input refused. PDF output refused (`TOO_MANY_PAGES`) when the book's estimated length exceeds what the smaller of `RLIMIT_DATA` and the container's memory limit allows (memory grows with page count; over `RLIMIT_DATA` Calibre hangs until the timeout instead of failing; about 230 estimated pages with the defaults). |
| **djvulibre** (`ebook`) | Page count first (`djvused -e n`), reject above `FILEX_MAX_PAGES` (default 2000). `ddjvu -format=tiff -page=<batch>` in batches with `-size` capped to `FILEX_MAX_PIXELS` per page, timeout per batch. |
| **OCRmyPDF / Tesseract** (`ocr`) | `--max-image-mpixels` from `FILEX_MAX_PIXELS`, `--skip-big`, `--tesseract-timeout`, `OMP_THREAD_LIMIT=1`. Languages baked into the image (eng, rus, deu, fra, spa, ita); never downloaded at job time. |
| **exiftool** | Only on copies in the scratch dir. For PDFs always followed by a pikepdf full rewrite (M8). Minimum version 12.24. |
| **Archive tools** | Own Python loop for ZIP-based formats (entry count, unpacked size counted from bytes read, ratio, depth 1, name and type checks, generated names, `O_EXCL` + `O_NOFOLLOW`); the same loop for RAR4/RAR5 (CBR) reads entries through libarchive (`libarchive-c` on Debian's `libarchive13t64`, minimum 3.7.4, probed by the worker), every header checked before any data is written; no `unar`, `lsar` or 7-Zip in the image. |
| **pyHanko** (`core`) | In-process in the step. Key material only from the job's uploaded `.p12` and the secret path ([ADR-0003](0003-tool-plugin-model.md)). PAdES B-B only: no timestamp or OCSP fetching in R1. |
| **cryptography** (`cert.create`) | Key generated in the step; returned only as a `.p12` encrypted with `BestAvailableEncryption` under a user passphrase of at least 12 characters. |

ImageMagick `policy.xml`, only if ImageMagick is installed:

```xml
<policymap>
  <policy domain="delegate" rights="none" pattern="*"/>
  <policy domain="coder" rights="none" pattern="*"/>
  <policy domain="coder" rights="read|write" pattern="{PNG,JPEG,GIF,WEBP,TIFF,BMP}"/>
  <policy domain="path" rights="none" pattern="@*"/>
  <policy domain="resource" name="memory" value="256MiB"/>
  <policy domain="resource" name="map" value="512MiB"/>
  <policy domain="resource" name="area" value="100MP"/>
  <policy domain="resource" name="width" value="16KP"/>
  <policy domain="resource" name="height" value="16KP"/>
  <policy domain="resource" name="list-length" value="500"/>
  <policy domain="resource" name="disk" value="1GiB"/>
  <policy domain="resource" name="time" value="120"/>
</policymap>
```
