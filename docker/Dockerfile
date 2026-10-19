# syntax=docker/dockerfile:1
# One file, one stage per image: `docker compose build` needs no pre-built base image.
# All bases are multi-arch (amd64 now, arm64 later); add engines per stage, not to `base`.
# Supply chain (plan task 66): base images are pinned by the digest of their multi-arch index
# (the tag stays for humans and Dependabot, which bumps both); Python packages come only from the
# hashed lockfiles in requirements/ (refresh: sh scripts/lock_deps.sh); Debian packages are the
# current ones at build time, so the weekly rebuild (images.yml) picks up Debian security fixes.

# python:3.12-slim-trixie
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# Same uid in every image so the API and workers share files on the filex-data volume.
# /data is created here so a fresh named volume inherits filex ownership.
RUN groupadd --gid 10001 filex \
 && useradd --uid 10001 --gid filex --no-create-home --shell /usr/sbin/nologin filex \
 && mkdir -p /data/jobs /data/tmp /usr/share/doc/filex \
 && chown -R filex:filex /data
# FileX's licence and the third-party notices, in every image (the dir exists first, so COPY
# keeps it searchable). Debian's own notices are /usr/share/doc/<package>/copyright.
COPY --chmod=0644 LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/filex/
WORKDIR /app
# Python packages, in every stage below: first the stage's lockfile (--require-hashes; before the
# code, so a code change does not reinstall them), then filex itself, built with the locked
# setuptools, --no-build-isolation and --no-index (nothing unpinned is fetched). setuptools is
# removed again in the same layer, and `pip check` fails the build if the lock misses a dependency.

# The web UI (plan task 9), built on the build host's arch: the output is static files.
# npm audit runs in CI (ci.yml, images.yml), not here: the build must not depend on it.
# node:24-trixie-slim
FROM --platform=$BUILDPLATFORM node:24-trixie-slim@sha256:7f486cabe892bff8fef04c0f7da8dba84a560be3e4e2740b7a86afc57ebec315 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM base AS api
COPY requirements/build.txt requirements/api.txt /tmp/req/
RUN pip install --require-hashes -r /tmp/req/api.txt
COPY pyproject.toml ./
COPY filex ./filex
RUN pip install --require-hashes -r /tmp/req/build.txt \
 && pip install --no-index --no-deps --no-build-isolation . \
 && pip uninstall -y setuptools && rm -rf build /tmp/req && pip check
# Served at / by the API (FILEX_WEB_DIR defaults to web/dist, relative to /app). The minified
# bundle drops the JavaScript licence headers, so the notices are served next to it, at
# /LICENSE and /THIRD_PARTY_NOTICES.md.
COPY --from=web /web/dist ./web/dist
COPY --chmod=0644 LICENSE THIRD_PARTY_NOTICES.md ./web/dist/
USER filex
# Starlette spools uploads to TMPDIR: keep it on the volume, not in RAM.
ENV TMPDIR=/data/tmp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=2)"]
# --no-proxy-headers: the API itself decides which peers may set X-Forwarded-For
# (FILEX_TRUSTED_PROXIES, ADR-0009), so uvicorn must not rewrite the client address first.
# --workers 1: ONE process. The download-link key lives in memory (ADR-0009); the API refuses
# to start with more workers unless FILEX_DL_KEY_FILE names a secret file with the key.
CMD ["uvicorn", "filex.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers", "--workers", "1"]

FROM base AS worker-core
RUN apt-get update \
 && apt-get install -y --no-install-recommends ghostscript fonts-dejavu-core libseccomp2 \
 && rm -rf /var/lib/apt/lists/*
COPY requirements/build.txt requirements/core.txt /tmp/req/
# The reportlab wheel bundles Adobe/Monotype Type 1 fonts (Arial, Times, Courier, Symbol,
# ZapfDingbats, Adobe Sans/Serif MM), all "All Rights Reserved": not free and never used by
# FileX, so they are deleted in the same layer that installs them (a later layer would still
# ship them). Vera*.ttf stays (filex/tools/edit/_overlay.py falls back to it), and so do the
# free DarkGarden (GPL-2.0+) and Calligrapher fonts. A reportlab bump that renames them fails here.
RUN pip install --require-hashes -r /tmp/req/core.txt \
 && cd /usr/local/lib/python3.12/site-packages/reportlab/fonts \
 && rm _*.pfb cob_*.pfb cobo_*.pfb com_*.pfb coo_*.pfb sy_*.pfb zd_*.pfb zx_*.pfb zy_*.pfb \
 && test -f Vera.ttf && ! grep -il 'all rights reserved' *.pfb
COPY pyproject.toml ./
COPY filex ./filex
RUN pip install --require-hashes -r /tmp/req/build.txt \
 && pip install --no-index --no-deps --no-build-isolation . \
 && pip uninstall -y setuptools && rm -rf build /tmp/req && pip check
# Engine settings are per call, in the tool wrappers, not here (review section 2.2): Ghostscript
# flags and input checks in filex/tools/_gs.py, Pillow limits in the image tools.
# No ImageMagick in this image (if ever added, ship the review's policy.xml with it).
USER filex
# Every worker stage needs libseccomp2: the step child installs its seccomp filter through it
# and refuses to run without it (ADR-0007 item 1).
# Exec form, and compose sets `init: false`: the runner must be PID 1 (ADR-0007, review H-1).
CMD ["python", "-m", "filex.worker", "core"]

# LibreOffice headless (ADR-0002 `office` group). No Pandoc (ebook, A1); WeasyPrint is R2.
# LibreOffice 25.2 from Debian 13 (trixie) main, with its security updates: the profile's
# DisableActiveContent needs 24.8+ (the runner's probe minimum). The -nogui packages with
# --no-install-recommends bring no JRE, extensions or script providers; the build fails if any
# of them sneaks in (ADR-0007 Appendix A). Fonts: Noto (Latin, Cyrillic, Greek...), DejaVu,
# Liberation 2 (on trixie the package is fonts-liberation; fonts-liberation2 is a transitional
# dummy) and Carlito/Caladea (metric-compatible with the MS core and Calibri/Cambria fonts,
# so layout and page count match Word). CJK adds ~200 MB: --build-arg CJK_FONTS=true.
FROM base AS worker-office
ARG CJK_FONTS=false
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui libseccomp2 \
      fonts-noto-core fonts-dejavu-core fonts-liberation \
      fonts-crosextra-carlito fonts-crosextra-caladea \
      $(if [ "$CJK_FONTS" = true ]; then echo fonts-noto-cjk; fi) \
 && rm -rf /var/lib/apt/lists/* \
 && if dpkg-query -W -f '${Package}\n' | grep -E \
      '^(openjdk|default-jre|libreoffice-(java|script-provider|sdbc-hsqldb|report-builder|librelogo|nlpsolver|wiki))'; \
    then echo "unwanted LibreOffice package installed" >&2; exit 1; fi
COPY requirements/build.txt requirements/office.txt /tmp/req/
RUN pip install --require-hashes -r /tmp/req/office.txt
COPY pyproject.toml ./
COPY filex ./filex
RUN pip install --require-hashes -r /tmp/req/build.txt \
 && pip install --no-index --no-deps --no-build-isolation . \
 && pip uninstall -y setuptools && rm -rf build /tmp/req && pip check
# Read-only profile template. A step never points LibreOffice at it: it copies it to
# <scratch>/lo-profile and runs soffice with -env:UserInstallation=file://<scratch>/lo-profile
# (one profile per process, deleted with the scratch dir). Settings per ADR-0007 Appendix A and
# review section 2; every key below exists in the installed version (checked at build time):
# macro security "very high" and macro execution off, untrusted referer links blocked, active
# content (OLE, DDE, links) off, Python runtime and extension installs off, Writer link updates
# never (0, SwLinkUpdateMode) and Calc never (1, ScLkUpdMode), no OpenCL probing.
COPY --chmod=0644 <<'EOF' /opt/filex/lo-profile/user/registrymodifications.xcu
<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisablePythonRuntime" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.ExtensionManager/ExtensionSecurity"><prop oor:name="DisableExtensionInstallation" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="UseOpenCL" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
EOF
# COPY creates the parent dirs without search permission for others: open them, read-only.
# Then check every key's full path against the installed schema: LibreOffice silently drops a
# setting it does not know (a typo, a moved or renamed key), so a bad template fails the build.
RUN chmod -R a+rX,go-w /opt/filex && python - <<'EOF'
import xml.etree.ElementTree as ET
O = "{http://openoffice.org/2001/registry}"
known = set()
def walk(node, path):
    for c in node:
        p = f"{path}/{c.get(O + 'name')}" if c.get(O + "name") else path
        if c.tag == "prop":
            known.add(p)
        walk(c, p)
for cs in ET.parse("/usr/lib/libreoffice/share/registry/main.xcd").iter(O + "component-schema"):
    walk(cs, f"/{cs.get(O + 'package')}.{cs.get(O + 'name')}")
items = ET.parse("/opt/filex/lo-profile/user/registrymodifications.xcu").getroot()
keys = [f"{i.get(O + 'path')}/{p.get(O + 'name')}" for i in items for p in i]
missing = [k for k in keys if k not in known]
assert keys and not missing, f"LibreOffice has no setting {missing}"
print(f"LibreOffice profile template: {len(keys)} keys checked")
EOF
USER filex
CMD ["python", "-m", "filex.worker", "office"]

# OCR (ADR-0002 `ocr` group): Tesseract 5.5, unpaper 7, Ghostscript 10.05 (OCRmyPDF's PDF/A
# path; probe minimum 10.03.1, review H4), pngquant and jbig2 (jbig2enc; OCRmyPDF -O2/-O3, which
# only ever runs it lossless) from Debian 13. OCRmyPDF 17 and OpenCV-headless (scan.clean
# deskew) from PyPI, for this image's Python: Debian's ocrmypdf is 16.7 for its own Python 3.13.
# Languages (spec O3): tessdata_fast eng rus deu fra spa ita, plus osd for auto-rotate.
# More: FILEX_OCR_EXTRA_LANGS=ukr,bel in .env, then `docker compose build worker-ocr`. Codes are
# Tesseract's (chi_sim -> tesseract-ocr-chi-sim); nothing is ever fetched at job time.
FROM base AS worker-ocr
ARG FILEX_OCR_EXTRA_LANGS=
RUN set -eu; langs="eng rus deu fra spa ita"; \
    for l in $(echo "$FILEX_OCR_EXTRA_LANGS" | tr ',' ' '); do \
      case "$l" in *[!a-z_]*) echo "FILEX_OCR_EXTRA_LANGS: bad language code '$l'" >&2; exit 1;; esac; \
      langs="$langs $l"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      tesseract-ocr unpaper ghostscript pngquant jbig2 libseccomp2 \
      $(for l in $langs; do echo "tesseract-ocr-$l" | tr _ -; done); \
    rm -rf /var/lib/apt/lists/*; \
    have=$(tesseract --list-langs 2>/dev/null); \
    for l in $langs osd; do echo "$have" | grep -qx "$l" || { echo "missing tessdata: $l" >&2; exit 1; }; done
COPY requirements/build.txt requirements/ocr.txt /tmp/req/
RUN pip install --require-hashes -r /tmp/req/ocr.txt
COPY pyproject.toml ./
COPY filex ./filex
RUN pip install --require-hashes -r /tmp/req/build.txt \
 && pip install --no-index --no-deps --no-build-isolation . \
 && pip uninstall -y setuptools && rm -rf build /tmp/req && pip check && ocrmypdf --version
# One Tesseract thread per step: the heavy slot and cpus limit, not OpenMP, decide CPU use.
ENV OMP_THREAD_LIMIT=1
USER filex
CMD ["python", "-m", "filex.worker", "ocr"]

# Upstream Calibre and Pandoc for worker-ebook, checked against pinned SHA-256 digests (the
# digests GitHub publishes for the release assets). Why not Debian's packages (checked
# 2026-10-10): Debian's pandoc is built without embedded data files, so `pandoc --sandbox`
# (mandatory, ADR-0007 Appendix A) cannot write EPUB or DOCX at all; Debian's Calibre 8.5 pulls
# Mesa/LLVM, SciPy and Perl (image 2.5 GB) and its Qt WebEngine has no security support in
# Debian stable. Calibre's own build bundles a current Qt WebEngine. To bump a version, change
# the ARG and both digests; a version without a digest here fails the build.
# python:3.12-slim-trixie
FROM python:3.12-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS ebook-fetch
ARG TARGETARCH
ARG CALIBRE_VERSION=9.16.0
ARG PANDOC_VERSION=3.11
RUN python - <<'EOF'
import hashlib, os, tarfile, urllib.request
SHA256 = {
    "calibre-9.16.0-x86_64.txz": "03cd4fda43de23d65405a3c90b924bc4a5c84753e388589d4ca343d9ba561a1d",
    "calibre-9.16.0-arm64.txz": "82bf5de819a655fc0c8b789d388fef53c4583f9c2f9b83a14df42724519a197d",
    "pandoc-3.11-linux-amd64.tar.gz": "37edb3bbcf722f921a009941bf5874e2e0c09263226c9b4a2d980788cb062ab6",
    "pandoc-3.11-linux-arm64.tar.gz": "56ed5566ec41d22ec9ee0704e6ac0b98ba102e92384efd5306173a22d314c79a",
    # Pandoc's licence files from its source at the same tag (the binary tarball has none).
    "3.11/COPYRIGHT": "842e33ef01625e93f85bebb8bac83aa570186b7aa77a09971257cc29f8f60740",
    "3.11/COPYING.md": "3ebfbf9235b049d384cdba5a66a1a3434bb544391e720b76faa8e1b3ee61a4e5",
}
arch, cv, pv = os.environ["TARGETARCH"], os.environ["CALIBRE_VERSION"], os.environ["PANDOC_VERSION"]
def fetch(url, key=None, to=None):
    key = key or url.rsplit("/", 1)[1]
    name = to or key
    assert key in SHA256, f"no pinned SHA-256 for {key}"
    urllib.request.urlretrieve(url, name)
    h = hashlib.sha256()
    with open(name, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    assert h.hexdigest() == SHA256[key], f"SHA-256 mismatch for {key}"
    return name
c = fetch(f"https://download.calibre-ebook.com/{cv}/calibre-{cv}-{ {'amd64': 'x86_64'}.get(arch, arch) }.txz")
with tarfile.open(c) as t:
    t.extractall("/opt/calibre", filter="data")
p = fetch(f"https://github.com/jgm/pandoc/releases/download/{pv}/pandoc-{pv}-linux-{arch}.tar.gz")
with tarfile.open(p) as t:
    m = t.getmember(f"pandoc-{pv}/bin/pandoc")
    m.name = "pandoc"
    t.extract(m, "/opt/pandoc", filter="data")
os.makedirs("/opt/pandoc/doc")
for f in ("COPYRIGHT", "COPYING.md"):
    fetch(f"https://raw.githubusercontent.com/jgm/pandoc/{pv}/{f}", f"{pv}/{f}", f"/opt/pandoc/doc/{f}")
    os.chmod(f"/opt/pandoc/doc/{f}", 0o644)
EOF

# Ebooks (ADR-0002 `ebook` group, heavy steps): Calibre ebook-convert, Pandoc (markup.convert;
# only here, A1), djvulibre (ddjvu, djvused), libarchive for CBR (read through libarchive-c in
# filex/tools/_archive.py; Debian's 7zip has no RAR codec), img2pdf + pypdfium2 for comics.
# djvulibre and libarchive from Debian 13. The libs are what Calibre's
# bundle needs from the system (it ships its own Python, Qt and Qt WebEngine).
# Fonts: Noto core and DejaVu cover Latin, Cyrillic and Greek for Calibre's PDF output.
FROM base AS worker-ebook
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      djvulibre-bin libarchive13t64 libseccomp2 \
      libegl1 libopengl0 libglx0 libxcb-cursor0 libxkbcommon0 libfontconfig1 libnss3 libasound2t64 \
      fonts-noto-core fonts-dejavu-core fonts-liberation \
 && rm -rf /var/lib/apt/lists/*
COPY requirements/build.txt requirements/ebook.txt /tmp/req/
RUN pip install --require-hashes -r /tmp/req/ebook.txt
COPY pyproject.toml ./
COPY filex ./filex
RUN pip install --require-hashes -r /tmp/req/build.txt \
 && pip install --no-index --no-deps --no-build-isolation . \
 && pip uninstall -y setuptools && rm -rf build /tmp/req && pip check
COPY --from=ebook-fetch /opt/calibre /opt/calibre
COPY --from=ebook-fetch /opt/pandoc/pandoc /usr/local/bin/pandoc
COPY --from=ebook-fetch /opt/pandoc/doc /usr/share/doc/pandoc
# ebook-convert only (ADR-0007 Appendix A): no other Calibre tool on PATH.
RUN ln -s /opt/calibre/ebook-convert /usr/local/bin/ebook-convert
# Headless Qt (ADR-0007 Appendix A): no display, Chromium's own sandbox off (no user namespaces
# under cap_drop ALL; the step's seccomp filter and no-network stand in), no GPU, no /dev/shm.
# The CALIBRE_* and XDG_CACHE_HOME dirs are /tmp defaults for the engine probe only: a step points
# them at its own scratch dir, so no config, plugin or cache outlives a step.
ENV QT_QPA_PLATFORM=offscreen \
    QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage" \
    QTWEBENGINE_DISABLE_SANDBOX=1 \
    CALIBRE_CONFIG_DIRECTORY=/tmp/calibre/config \
    CALIBRE_TEMP_DIR=/tmp/calibre/tmp \
    CALIBRE_CACHE_DIRECTORY=/tmp/calibre/cache \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_RUNTIME_DIR=/tmp
# Every engine must start as the non-root user before the image counts as built, and Calibre
# must render a Cyrillic PDF headless (Qt WebEngine + fonts): a missing system lib or font fails
# here, not in a job. Calibre does not create CALIBRE_TEMP_DIR itself: a step creates all three.
USER filex
RUN set -e; cd /tmp; mkdir -p calibre/config calibre/tmp calibre/cache cache; \
    for f in /opt/calibre/lib/libQt6WebEngineCore.so.6 /opt/calibre/libexec/QtWebEngineProcess \
             /opt/calibre/plugins/platforms/libqoffscreen.so; do \
      if LD_LIBRARY_PATH=/opt/calibre/lib ldd "$f" | grep 'not found'; then exit 1; fi; done; \
    ebook-convert --version; pandoc --version | head -1; \
    ddjvu --help 2>&1 | head -1; python -c "import libarchive"; \
    printf '<html><body><p>Check: Съешь ещё булок.</p></body></html>' > check.html; \
    ebook-convert check.html check.pdf > /dev/null; \
    python -c "import pypdfium2 as p; t = p.PdfDocument('check.pdf')[0].get_textpage().get_text_range(); assert 'Съешь' in t, t"; \
    rm -rf /tmp/calibre /tmp/cache check.html check.pdf
CMD ["python", "-m", "filex.worker", "ebook"]
