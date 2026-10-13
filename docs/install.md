# Installing FileX

FileX runs in Docker on your own computer. Out of the box it is in **local mode**: no account,
no sign-in, and only the computer it runs on can open it.

- [1. What you need](#1-what-you-need)
- [2. Install](#2-install)
- [3. Choose the worker groups](#3-choose-the-worker-groups)
- [4. Use it from other devices on your home network](#4-use-it-from-other-devices-on-your-home-network)
- [5. Settings in `.env`](#5-settings-in-env)
- [6. Update](#6-update)
- [7. Uninstall](#7-uninstall)
- [8. Troubleshooting](#8-troubleshooting)

## 1. What you need

- An x86-64 computer with **Docker and Docker Compose**:
  - **Linux:** Docker Engine 28 or later with the Compose plugin
    ([install guide](https://docs.docker.com/engine/install/)). Older engines let other machines
    on the same network segment reach ports published on `127.0.0.1`.
  - **Windows 10/11 or macOS (Intel):** [Docker Desktop](https://docs.docker.com/desktop/).
    On Windows, use the WSL 2 backend (the default).
- **Git** (or download the source as a ZIP) and **Python 3** to create the internal passwords.
  Without Python, `sh scripts/make_secrets.sh` does the same on Linux and macOS.
- **RAM:** 2 GB for the core tools only, 4 GB for everything (see [section 3](#3-choose-the-worker-groups)).
- **Disk:** at least 20 GB free for the images, the build cache and 24 hours of results.
- Internet access while the images build. Once running, FileX needs none.

## 2. Install

In a terminal (on Windows: PowerShell or a WSL shell):

```sh
git clone https://github.com/niklan1110/filex-app.git
cd filex-app
cp .env.example .env                 # Windows PowerShell: copy .env.example .env
python3 scripts/make_secrets.py      # Windows: python scripts/make_secrets.py
docker compose up -d
```

- `make_secrets.py` creates the passwords FileX's internal job queue uses, as files in `secrets/`.
  Run it once. Compose refuses to start without them. The queue is the `redis` service, which
  runs [Valkey](https://valkey.io/) 8 (BSD-3-Clause), the open-source fork of Redis.
- The first `docker compose up -d` builds the images from source. With all worker groups this
  takes a while (tens of minutes on a slow machine); later starts take seconds.

Open **<http://127.0.0.1:8080>**. Check that everything is up:

```sh
docker compose ps                                 # every service "running" (redis "healthy")
curl -s http://127.0.0.1:8080/api/v1/health       # {"status":"ok"}
```

`curl -s http://127.0.0.1:8080/api/v1/status` lists which worker groups are online and whether
per-step file isolation (Landlock) is active.

## 3. Choose the worker groups

`COMPOSE_PROFILES` in `.env` decides which optional engines run. `core` (PDF, images, signing)
always runs.

| Group | Adds | Memory limit (default) |
|---|---|---|
| `core` | PDF tools, images, signing, encryption | `FILEX_MEM_CORE=1g` |
| `office` | LibreOffice: Word, Excel, PowerPoint, OpenDocument | `FILEX_MEM_OFFICE=1536m` |
| `ocr` | OCRmyPDF, Tesseract, scan cleanup | `FILEX_MEM_OCR=1536m` |
| `ebook` | Calibre, Pandoc, DjVu, comic books | `FILEX_MEM_EBOOK=1g` |

```sh
COMPOSE_PROFILES=office,ocr,ebook   # everything (the default)
COMPOSE_PROFILES=ocr                # core + OCR only
COMPOSE_PROFILES=                   # core only (2 GB machines)
```

On a 2 to 4 GB machine, also uncomment the **low-memory preset** at the end of `.env`. After a
change: `docker compose up -d`. A group you turn off later keeps running until you stop it:
`docker compose --profile all down` and then `docker compose up -d`.

More OCR languages are baked into the `ocr` image: set `FILEX_OCR_EXTRA_LANGS=ukr,bel` (Tesseract
codes), then `docker compose build worker-ocr && docker compose up -d`.

## 4. Use it from other devices on your home network

Local mode only works on the computer FileX runs on. For your phone or another PC, give FileX
**access tokens** and publish it on this computer's LAN address.

1. **Find this computer's LAN address**, e.g. `192.168.1.20` (Windows: `ipconfig`; macOS:
   System Settings, Network; Linux: `ip -4 addr`). Give the computer a fixed address in your
   router so it does not change.
2. **Make a token for each device.** The first line printed is the token for the device; the
   second is its hash for `.env`:

   ```sh
   python3 -c "import secrets,hashlib; t='fxt_'+secrets.token_urlsafe(32); print(t); print(hashlib.sha256(t.encode()).hexdigest())"
   ```

3. **Edit `.env`:**

   ```sh
   FILEX_BIND=192.168.1.20
   FILEX_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.20
   FILEX_API_TOKENS=phone:<hash 1>,laptop:<hash 2>
   FILEX_AUTH=
   ```

   `FILEX_AUTH=` (empty) turns local mode off.
4. `docker compose up -d`. On the device, open `http://192.168.1.20:8080`, choose
   **Access token** and paste its `fxt_` token. The browser remembers it.

Notes:

- **The guard.** With `FILEX_AUTH=none`, FileX refuses to start unless `FILEX_BIND` is
  `127.0.0.1` (or another loopback address). If it stops after you changed `FILEX_BIND`, you
  forgot step 3; `docker compose logs api` shows the message. The guard only sees `FILEX_BIND`:
  change the address there, never in the `ports:` line of `compose.yaml` or in an override file.
- **Revoke a device:** remove its `name:<hash>` entry and run `docker compose up -d`.
- **Firewall:** allow incoming TCP on the port (8080) from your LAN if the device cannot connect.
  Docker Desktop may ask on first start.
- **Plain HTTP.** Tokens and files cross your LAN unencrypted. Keep FileX on a network you trust,
  never forward the port to the internet and never set `FILEX_BIND=0.0.0.0`. For HTTPS, put a
  reverse proxy on this computer in front of `127.0.0.1:8080` and keep `FILEX_BIND=127.0.0.1`
  (then sign-in is still needed if the proxy is reachable from other devices).
- **Scripts** send `Authorization: Bearer fxt_...`; in local mode, scripts on the same computer
  need nothing.

## 5. Settings in `.env`

`.env.example` explains every setting next to its value. The ones you are most likely to touch:

| Setting | Default | What it does |
|---|---|---|
| `COMPOSE_PROFILES` | `office,ocr,ebook` | Optional worker groups ([section 3](#3-choose-the-worker-groups)). |
| `FILEX_BIND` | `127.0.0.1` | Address the UI and API are published on. |
| `FILEX_PORT` | `8080` | Port on that address. |
| `FILEX_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Addresses the browser may use for FileX; others get `Invalid host header`. |
| `FILEX_AUTH` | `none` | `none` = local mode (loopback only). Empty = sign-in required. |
| `FILEX_API_TOKENS` | empty | `name:<sha256>` per device or script ([section 4](#4-use-it-from-other-devices-on-your-home-network)). |
| `FILEX_MAX_UPLOAD_MB` | `1024` | Largest upload per job. |
| `FILEX_MAX_FILES` | `50` | Files per job. |
| `FILEX_RETENTION_MIN` | `1440` | Minutes results are kept (at most 1440 = 24 h). |
| `FILEX_MIN_FREE_MB` | `4096` | Refuse uploads that would leave less free disk (`507`). Keep it at least 2x `FILEX_MAX_UPLOAD_MB`. |
| `FILEX_MAX_QUEUED` | `20` | Refuse new jobs while a group has this many waiting (`429`). |
| `FILEX_MAX_PAGES` | `2000` | Largest document the tools open. |
| `FILEX_CONCURRENCY` | `1` | Steps each worker runs at once. |
| `FILEX_HEAVY_SLOTS` | `1` | Heavy steps (LibreOffice, OCR, Calibre) at once across groups; `0` = no limit. |
| `FILEX_MEM_CORE`, `_OFFICE`, `_OCR`, `_EBOOK` | see section 3 | Memory cap per worker group. |
| `FILEX_LANDLOCK` | `auto` | Per-step file isolation: `auto`, `required` or `off` (debugging only). |

After editing `.env`, run `docker compose up -d`; compose recreates only what changed.

## 6. Update

Pick a moment when no job is running (check **My jobs**).

```sh
cd filex-app
git pull
git diff HEAD@{1} --stat -- .env.example   # new settings? copy them from .env.example into .env
docker compose build --pull
docker compose up -d
docker image prune -f
```

Running jobs may fail during an update; results are temporary anyway, so run them again.

When an update changes the `redis` service's image (for example the move from Redis 7.4 to
Valkey 8), `docker compose up -d` recreates that container, and every queued and running job is
lost, because the queue keeps nothing on disk. Update when no jobs are queued.

The engines come from Debian packages at build time and the build caches them. To pick up their
security fixes, rebuild without the cache now and then (monthly is a good habit):

```sh
git pull && docker compose build --no-cache && docker compose up -d && docker image prune -f
```

## 7. Uninstall

```sh
cd filex-app
docker compose --profile all down -v --rmi all   # containers, the data volume, the images
docker builder prune -f                          # build cache
cd .. && rm -rf FileX                            # Windows PowerShell: Remove-Item -Recurse -Force FileX
```

`down -v` deletes every job and result. If you set a firewall rule for the port, remove it too.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose up` says a secret file is missing | Run `python3 scripts/make_secrets.py` once. |
| The API keeps restarting | `docker compose logs api` shows why: usually the guard ([section 4](#4-use-it-from-other-devices-on-your-home-network)) or a malformed `FILEX_API_TOKENS` entry. |
| `Invalid host header` | Add the address you typed to `FILEX_ALLOWED_HOSTS` (no `http://`, no port), then `docker compose up -d`. |
| `CROSS_SITE_REQUEST` (403) | In local mode FileX only answers its own page and scripts on this computer, never another website. Open FileX at an address listed in `FILEX_ALLOWED_HOSTS`. Wildcards (`*`) are refused in local mode. |
| Port 8080 is in use | Set `FILEX_PORT` to a free port. |
| `Pool overlaps with other one on this address space` | Another network (a second FileX, a VPN or your LAN) uses `10.213.83.0/24`. Set `FILEX_INTERNAL_SUBNET` to a free private `/24`, e.g. `10.213.84.0/24`. |
| A tool says "Enable the `ocr` profile" | Add the group to `COMPOSE_PROFILES`, then `docker compose up -d`. If it is there, see `docker compose ps` and `docker compose logs worker-ocr`. |
| A job fails with `ENGINE_MEMORY` | Raise that group's `FILEX_MEM_*`, or split the document. |
| Uploads fail with `413` / `507` | Over `FILEX_MAX_UPLOAD_MB` / not enough free disk for `FILEX_MIN_FREE_MB`. Free space or adjust the limits. |
| `/api/v1/status` shows Landlock unavailable | The kernel lacks it (older than 5.19). FileX still runs with its other limits; `FILEX_LANDLOCK=required` would turn the tools off. |
