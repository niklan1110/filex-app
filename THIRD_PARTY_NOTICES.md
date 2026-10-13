# Third-party notices

FileX is licensed under the GNU Affero General Public License v3.0 or later
(`AGPL-3.0-or-later`, see [LICENSE](LICENSE)). Copyright (C) 2026 niklan1110.

FileX runs on top of many free and open-source engines and libraries. This file lists every
third-party component that ends up in a FileX Docker image or in the web UI bundle, with its
licence and where to get its source. Each component keeps its own licence; nothing here
relicenses it.

Last checked: 2026-10-13, against `docker/Dockerfile`, `requirements/*.txt`, `web/package.json`
and `web/package-lock.json` at that date. Python licences come from each package's own metadata
(installed `dist-info`, or the PyPI JSON API for the exact locked version); npm licences from
`package-lock.json`; Debian licences from the Debian 13 (trixie) `copyright` files; Calibre,
Pandoc and Valkey from the licence files in their upstream source at the pinned version.

When a dependency is added or bumped, update this file in the same change.

## Contents

- [How the images are distributed](#how-the-images-are-distributed)
- [Licence compatibility summary](#licence-compatibility-summary)
- [Every Python image (api and all workers)](#every-python-image-api-and-all-workers)
- [api image](#api-image)
- [Web UI bundle (served by the api image)](#web-ui-bundle-served-by-the-api-image)
- [worker-core image](#worker-core-image)
- [worker-office image (optional profile `office`)](#worker-office-image-optional-profile-office)
- [worker-ocr image (optional profile `ocr`)](#worker-ocr-image-optional-profile-ocr)
- [worker-ebook image (optional profile `ebook`)](#worker-ebook-image-optional-profile-ebook)
- [redis service, Valkey (not built by FileX)](#redis-service-valkey-not-built-by-filex)
- [Build-only tools (not shipped)](#build-only-tools-not-shipped)

## How the images are distributed

Today FileX publishes **source only**. `docker compose build` on the user's own machine downloads
the engines from Debian, PyPI, npm and the Calibre and Pandoc release servers and builds the images
there. FileX itself does not hand out any of those binaries, so the copyleft "provide the source"
duties of the GPL-family engines fall on their upstream distributors (Debian, PyPI, the Calibre and
Pandoc projects), who already publish the source.

If prebuilt FileX images are ever published to a registry, FileX becomes a distributor of every
binary listed below and must then:

1. Ship this file, `LICENSE`, and the licence texts of the components inside every image (see
   "Notices inside the images" in the summary).
2. Offer the Corresponding Source of every GPL, LGPL and AGPL component in that image, for at least
   as long as the image is offered (GPL-3.0 section 6) and in any case for three years for the
   GPL-2.0 components (GPL-2.0 section 3). The simplest compliant way is to publish, next to each
   image tag, a source bundle built from the same inputs: `apt-get source` of the Debian packages,
   the Calibre and Pandoc source tarballs of the pinned versions, and the source distributions of
   the locked Python packages. A link to Debian's archive alone is not enough for GPL-2.0 binaries.

FileX's own AGPL duty applies in every case, prebuilt images or not: anyone who runs a modified
FileX as a network service must offer its users the modified source (AGPL-3.0 section 13).

## Licence compatibility summary

FileX is AGPL-3.0-or-later. "In-process" means the component is imported or linked into a FileX
Python process, so FileX plus that component form one combined work and the licences must be
compatible. "Separate program" means FileX starts it as its own executable and exchanges files and
command-line arguments with it; that is mere aggregation, and the component's licence does not
extend to FileX (nor FileX's to it).

| Component | Licence | How FileX uses it | Verdict |
|---|---|---|---|
| PyMuPDF (MuPDF) | AGPL-3.0 (or commercial) | in-process | Compatible. This is one reason FileX must be AGPL itself. |
| Ghostscript (+ jbig2dec, URW base35 fonts) | AGPL-3.0-or-later | separate program | Compatible. Same licence family as FileX. |
| pikepdf, OCRmyPDF, certifi | MPL-2.0 | in-process | Compatible (MPL-2.0 section 3.3 allows combination with GPL-family licences; MPL files stay MPL). |
| LibreOffice | MPL-2.0 (parts LGPL-3.0-or-later, Apache-2.0) | separate program | Compatible. |
| img2pdf | LGPL-3.0 | in-process | Compatible. |
| fpdf2 | LGPL-3.0-only | in-process (OCRmyPDF dependency) | Compatible. |
| pillow-heif binary wheel | wheel as a whole GPL-2.0 (bundles x265, GPL-2.0-or-later; libheif and libde265, LGPL-3.0) | in-process | Compatible: x265 is "or later", so it can be combined under GPL-3.0, which allows linking with AGPL-3.0 code (GPL-3.0 section 13). It would conflict if FileX were GPL-2.0-only. |
| Pandoc | GPL-2.0-or-later | separate program | Compatible. Source must be offered if images are distributed. |
| Calibre | GPL-3.0 (bundle also contains LGPL Qt, Qt WebEngine, FFmpeg, Poppler, unrar...) | separate program | Compatible. Source must be offered if images are distributed. **Contains non-free unrar, see below.** |
| djvulibre | GPL-2.0-or-later | separate program | Compatible. |
| unpaper | GPL-2.0-only | separate program | Fine as a separate program. It could **not** be linked into FileX (GPL-2.0-only and AGPL-3.0 are incompatible). |
| pngquant (with libimagequant) | BSD-2-Clause CLI, linked to libimagequant GPL-3.0-or-later | separate program | Compatible. |
| jbig2enc | Apache-2.0 (some files GPL-3.0-or-later) | separate program | Compatible. |
| Apache-2.0 components (Tesseract, tessdata, opencv, pdf.js, ...) | Apache-2.0 | both | Compatible with AGPL-3.0 (not with GPL-2.0-only, which does not matter here). |
| Fonts (Noto, Liberation, Carlito, Caladea, DejaVu, Archivo, URW base35) | OFL-1.1, Bitstream Vera, AGPL-3.0 with font exception | data | Fine. The fonts may be bundled with software but not sold on their own; documents FileX produces with embedded fonts are not covered by the font licences. |
| ReportLab wheel's bundled Type 1 fonts | Monotype and Adobe, "All Rights Reserved", no free licence | **removed from the image** | **Resolved.** The `reportlab` wheel on PyPI ships Arial, Times New Roman, Courier, Symbol, ZapfDingbats and Adobe Sans/Serif MM `.pfb` files in `reportlab/fonts/`. FileX never used them (it embeds DejaVu, Noto or ReportLab's Bitstream Vera). The `worker-core` build deletes them (`_*.pfb`, `cob*`, `com*`, `coo*`, `sy*`, `zd*`, `zx*`, `zy*`) in the same layer that installs ReportLab, and fails if any `.pfb` marked "All rights reserved" is left. The free DarkGarden (GPL-2.0-or-later), Calligrapher (free with notice) and Bitstream Vera fonts stay. |
| **unrar inside Calibre** | **unRAR licence (freeware, non-free)** | inside the Calibre bundle | **Flag.** Calibre 9.16's binary bundle includes unrar 7.2.2 for RAR/CBR input. The unRAR licence allows free redistribution but forbids using the code to recreate the RAR compression algorithm; it is not an open-source licence. FileX does not use it (CBR is read through libarchive, BSD) and redistribution is allowed, but it has to be named, and it breaks a strict "free software only" image. It is part of the upstream binary bundle and cannot be left out without building Calibre from source. |
| Valkey 8 (compose `redis` service) | BSD-3-Clause | separate service, pulled from Docker Hub | Compatible. FileX's job queue ran on Redis 7.4 (RSALv2/SSPLv1, source-available, not open source) until 2026-10-13; it now runs on Valkey, the open-source fork of Redis, with the same protocol, Streams and ACLs. The compose service keeps the name `redis`. |

No in-process component is GPL-2.0-only, and nothing in-process is proprietary. Under
AGPL-3.0-or-later every component may be shipped, with the source offers and notices above.

### Notices inside the images

Debian's `-slim` base images remove `/usr/share/doc/*` but keep `/usr/share/doc/*/copyright`, so
every apt package carries its licence inside the image. Python wheels keep their licence files in
`site-packages/*.dist-info/`. Calibre keeps its licences under `/opt/calibre`. On top of that:

- Every image has FileX's `LICENSE` and this file in `/usr/share/doc/filex/`.
- `worker-ebook` has Pandoc's `COPYRIGHT` and `COPYING.md` (GPL-2.0 text) in
  `/usr/share/doc/pandoc/`, fetched from Pandoc's source at the pinned tag and checked against
  pinned SHA-256 digests (the release tarball holds only the binary and man pages).
- The web bundle is minified, so the MIT and Apache headers of React, supabase-js and pdf.js are
  stripped from the JavaScript. The api image therefore serves `LICENSE` and this file next to
  the bundle, at `/LICENSE` and `/THIRD_PARTY_NOTICES.md`.

`scripts/ci/image-tests.sh` checks all of this in every image, including that every installed
Debian package has its `copyright` file.

## Every Python image (api and all workers)

Base image `python:3.12-slim-trixie` (pinned by digest in `docker/Dockerfile`).

| Component | Version | Licence | Source |
|---|---|---|---|
| CPython | 3.12 | PSF-2.0 | <https://www.python.org/downloads/source/> |
| pip | from the base image | MIT | <https://pypi.org/project/pip/> |
| Debian 13 base system (libc, coreutils, bash, OpenSSL, ...) | trixie | various free licences (GPL, LGPL, BSD, MIT...); see `/usr/share/doc/*/copyright` in the image | <https://sources.debian.org/> |

Python packages installed in all five images (`requirements/{api,core,office,ocr,ebook}.txt`):

| Package | Version | Licence | Link |
|---|---|---|---|
| annotated-types | 0.8.0 | MIT | <https://pypi.org/project/annotated-types/> |
| defusedxml | 0.7.1 | PSF-2.0 | <https://pypi.org/project/defusedxml/> |
| pydantic | 2.14.1 | MIT | <https://pypi.org/project/pydantic/> |
| pydantic-core | 2.50.1 | MIT | <https://pypi.org/project/pydantic-core/> |
| redis (redis-py client) | 8.1.0 | MIT | <https://pypi.org/project/redis/> |
| typing-extensions | 4.16.0 | PSF-2.0 | <https://pypi.org/project/typing-extensions/> |
| typing-inspection | 0.4.4 | MIT | <https://pypi.org/project/typing-inspection/> |

`setuptools` 84.0.0 (MIT) is installed only to build FileX and uninstalled in the same layer.

## api image

Python packages (`requirements/api.txt`), in addition to the common ones:

| Package | Version | Licence | Link |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | <https://pypi.org/project/annotated-doc/> |
| anyio | 4.15.1 | MIT | <https://pypi.org/project/anyio/> |
| certifi | 2026.7.22 | MPL-2.0 | <https://pypi.org/project/certifi/> |
| click | 8.5.0 | BSD-3-Clause | <https://pypi.org/project/click/> |
| fastapi | 0.143.0 | MIT | <https://pypi.org/project/fastapi/> |
| h11 | 0.16.0 | MIT | <https://pypi.org/project/h11/> |
| httpcore | 1.0.9 | BSD-3-Clause | <https://pypi.org/project/httpcore/> |
| httpx | 0.28.1 | BSD-3-Clause | <https://pypi.org/project/httpx/> |
| idna | 3.20 | BSD-3-Clause | <https://pypi.org/project/idna/> |
| opentelemetry-api | 1.45.1 | Apache-2.0 | <https://pypi.org/project/opentelemetry-api/> |
| python-multipart | 0.0.32 | Apache-2.0 | <https://pypi.org/project/python-multipart/> |
| starlette | 1.8.0 | BSD-3-Clause | <https://pypi.org/project/starlette/> |
| uvicorn | 0.54.0 | BSD-3-Clause | <https://pypi.org/project/uvicorn/> |

No apt packages beyond the base image.

## Web UI bundle (served by the api image)

Built in the `web` stage of `docker/Dockerfile` and copied to `/app/web/dist`. Runtime
dependencies from `web/package-lock.json` (dev dependencies such as Vite, TypeScript, ESLint,
Vitest, Playwright and pdf-lib are build or test tools and are not in the bundle). Packages the
bundler tree-shakes away are listed anyway.

| Package | Version | Licence | Link |
|---|---|---|---|
| react | 19.3.0 | MIT | <https://github.com/facebook/react> |
| react-dom | 19.3.0 | MIT | <https://github.com/facebook/react> |
| scheduler | 0.28.0 | MIT | <https://github.com/facebook/react> |
| @supabase/supabase-js | 2.117.2 | MIT | <https://github.com/supabase/supabase-js> |
| @supabase/auth-js | 2.117.2 | MIT | <https://github.com/supabase/supabase-js> |
| @supabase/functions-js | 2.117.2 | MIT | <https://github.com/supabase/supabase-js> |
| @supabase/postgrest-js | 2.117.2 | MIT | <https://github.com/supabase/supabase-js> |
| @supabase/realtime-js | 2.117.2 | MIT | <https://github.com/supabase/supabase-js> |
| @supabase/storage-js | 2.117.2 | MIT | <https://github.com/supabase/supabase-js> |
| @supabase/phoenix | 0.4.5 | MIT | <https://www.npmjs.com/package/@supabase/phoenix> |
| iceberg-js | 0.8.1 | MIT | <https://www.npmjs.com/package/iceberg-js> |
| tslib | 2.8.1 | 0BSD | <https://github.com/microsoft/tslib> |
| pdfjs-dist (PDF.js library and worker) | 6.3.289 | Apache-2.0 | <https://github.com/mozilla/pdf.js> |

**pdfjs-dist assets.** Only the PDF.js library (`build/pdf.mjs`) and its worker
(`build/pdf.worker.min.mjs`) are bundled. The package's other assets are **not** shipped today
(`web/src/preview/renderPage.ts` sets no `standardFontDataUrl`, `cMapUrl` or `wasmUrl`). If they are
ever added, their licences are:

| pdfjs-dist asset | Licence |
|---|---|
| `standard_fonts/Foxit*.pfb` | BSD-3-Clause style (PDFium authors), `LICENSE_FOXIT` |
| `standard_fonts/LiberationSans-*.ttf` | SIL OFL-1.1, `LICENSE_LIBERATION` |
| `cmaps/*.bcmap` | Adobe CMap resources, BSD-3-Clause |
| `iccs/*.icc` | CC0-1.0 |
| `wasm/jbig2.wasm`, `openjpeg.wasm`, `qcms_bg.wasm` | PDFium BSD-3-Clause, OpenJPEG BSD-2-Clause, qcms MIT (`LICENSE_*` files next to them) |

`@napi-rs/canvas` (MIT) is an optional Node.js-only dependency of pdfjs-dist; it is not in the
browser bundle and not in any image.

**Font in the bundle.**

| Font | Licence | Link |
|---|---|---|
| Archivo (`web/public/fonts/archivo-latin-wdth-wght.woff2`) | SIL OFL-1.1 (`web/public/fonts/Archivo-OFL.txt`, shipped next to it) | <https://github.com/Omnibus-Type/Archivo> |

The icon sprite (`web/src/assets/icons.svg`) is FileX's own artwork.

## worker-core image

Debian 13 packages (current trixie version at build time):

| Package | Licence | Link |
|---|---|---|
| ghostscript (with libgs10, libgs10-common) | AGPL-3.0-or-later (a few files GPL-2.0, Apache-2.0, permissive) | <https://packages.debian.org/trixie/ghostscript> |
| libjbig2dec0 (Ghostscript dependency) | AGPL-3.0-or-later | <https://packages.debian.org/trixie/libjbig2dec0> |
| fonts-urw-base35 (Ghostscript dependency) | AGPL-3.0 with font exception | <https://packages.debian.org/trixie/fonts-urw-base35> |
| fonts-dejavu-core | Bitstream Vera licence (DejaVu changes public domain) | <https://packages.debian.org/trixie/fonts-dejavu-core> |
| libseccomp2 | LGPL-2.1 | <https://packages.debian.org/trixie/libseccomp2> |

Their own dependencies (FreeType, Little CMS, libjpeg-turbo, OpenJPEG, libtiff, libpng, zlib,
CUPS libraries, libpaper, X11 libraries and others) are under free licences listed in
`/usr/share/doc/*/copyright` inside the image.

Python packages (`requirements/core.txt`), in addition to the common ones:

| Package | Version | Licence | Link |
|---|---|---|---|
| **pymupdf** (bundles MuPDF) | 1.28.2 | **AGPL-3.0** or Artifex commercial licence | <https://pypi.org/project/pymupdf/> |
| pikepdf (bundles qpdf, Apache-2.0) | 10.17.0 | MPL-2.0 | <https://pypi.org/project/pikepdf/> |
| pypdfium2 (bundles PDFium) | 5.14.0 | BSD-3-Clause and Apache-2.0, plus bundled-library licences in its `dist-info/licenses` | <https://pypi.org/project/pypdfium2/> |
| pillow (bundles libjpeg-turbo, libpng, libtiff, libwebp, FreeType, OpenJPEG, Little CMS, zlib and others, all permissive) | 12.3.0 | MIT-CMU | <https://pypi.org/project/pillow/> |
| **pillow-heif** (bundles libheif and libde265, LGPL-3.0; x265, GPL-2.0-or-later) | 1.8.1 | source BSD-3-Clause; **binary wheel GPL-2.0** (stated in its `LICENSES_bundled.txt`) | <https://pypi.org/project/pillow-heif/> |
| img2pdf | 0.6.3 | LGPL-3.0 | <https://pypi.org/project/img2pdf/> |
| reportlab (its non-free Type 1 fonts are deleted, see the summary) | 5.0.1 | BSD-3-Clause style (ReportLab licence); bundled DarkGarden font GPL-2.0-or-later, Calligrapher font free with notice, Bitstream Vera fonts Bitstream Vera licence | <https://pypi.org/project/reportlab/> |
| pyhanko | 0.37.0 | MIT | <https://pypi.org/project/pyhanko/> |
| pyhanko-certvalidator | 0.32.1 | MIT | <https://pypi.org/project/pyhanko-certvalidator/> |
| asn1crypto | 1.5.1 | MIT | <https://pypi.org/project/asn1crypto/> |
| oscrypto | 1.3.0 | MIT | <https://pypi.org/project/oscrypto/> |
| cryptography (bundles OpenSSL 3, Apache-2.0) | 50.0.2 | Apache-2.0 OR BSD-3-Clause | <https://pypi.org/project/cryptography/> |
| cffi | 2.1.1 | MIT-0 | <https://pypi.org/project/cffi/> |
| pycparser | 3.11 | BSD-3-Clause | <https://pypi.org/project/pycparser/> |
| python-docx | 1.2.0 | MIT | <https://pypi.org/project/python-docx/> |
| lxml (bundles libxml2 and libxslt, MIT) | 6.1.3 | BSD-3-Clause | <https://pypi.org/project/lxml/> |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | <https://pypi.org/project/aiohttp/> |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 | <https://pypi.org/project/aiohappyeyeballs/> |
| aiosignal | 1.4.0 | Apache-2.0 | <https://pypi.org/project/aiosignal/> |
| attrs | 26.1.0 | MIT | <https://pypi.org/project/attrs/> |
| frozenlist | 1.8.0 | Apache-2.0 | <https://pypi.org/project/frozenlist/> |
| multidict | 7.1.0 | Apache-2.0 | <https://pypi.org/project/multidict/> |
| propcache | 0.5.4 | Apache-2.0 | <https://pypi.org/project/propcache/> |
| yarl | 1.25.1 | Apache-2.0 | <https://pypi.org/project/yarl/> |
| certifi | 2026.7.22 | MPL-2.0 | <https://pypi.org/project/certifi/> |
| charset-normalizer | 3.5.2 | MIT | <https://pypi.org/project/charset-normalizer/> |
| idna | 3.20 | BSD-3-Clause | <https://pypi.org/project/idna/> |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | <https://pypi.org/project/packaging/> |
| tzdata | 2026.5 | Apache-2.0 | <https://pypi.org/project/tzdata/> |
| tzlocal | 5.4.4 | MIT | <https://pypi.org/project/tzlocal/> |
| uritools | 6.1.3 | MIT | <https://pypi.org/project/uritools/> |

## worker-office image (optional profile `office`)

Debian 13 packages (current trixie version at build time):

| Package | Licence | Link |
|---|---|---|
| libreoffice-writer-nogui, libreoffice-calc-nogui, libreoffice-impress-nogui (and the LibreOffice core packages they pull in) | MPL-2.0 (older code also LGPL-3.0-or-later; some files Apache-2.0, MIT, CC0-1.0) | <https://packages.debian.org/trixie/libreoffice-writer-nogui> |
| fonts-noto-core | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-noto-core> |
| fonts-dejavu-core | Bitstream Vera licence | <https://packages.debian.org/trixie/fonts-dejavu-core> |
| fonts-liberation (Liberation 2) | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-liberation> |
| fonts-crosextra-carlito | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-crosextra-carlito> |
| fonts-crosextra-caladea | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-crosextra-caladea> |
| fonts-noto-cjk (only with `--build-arg CJK_FONTS=true`) | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-noto-cjk> |
| libseccomp2 | LGPL-2.1 | <https://packages.debian.org/trixie/libseccomp2> |

LibreOffice's own dependencies (ICU, Boost, HarfBuzz, NSS, Poppler and others) are listed with
their licences in `/usr/share/doc/*/copyright` inside the image.

Python packages (`requirements/office.txt`), in addition to the common ones:

| Package | Version | Licence | Link |
|---|---|---|---|
| pikepdf (bundles qpdf, Apache-2.0) | 10.17.0 | MPL-2.0 | <https://pypi.org/project/pikepdf/> |
| pillow | 12.3.0 | MIT-CMU | <https://pypi.org/project/pillow/> |
| lxml | 6.1.3 | BSD-3-Clause | <https://pypi.org/project/lxml/> |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | <https://pypi.org/project/packaging/> |

## worker-ocr image (optional profile `ocr`)

Debian 13 packages (current trixie version at build time):

| Package | Licence | Link |
|---|---|---|
| tesseract-ocr | Apache-2.0 | <https://packages.debian.org/trixie/tesseract-ocr> |
| tesseract-ocr-eng, -rus, -deu, -fra, -spa, -ita, -osd (plus any `FILEX_OCR_EXTRA_LANGS`) | Apache-2.0 | <https://packages.debian.org/trixie/tesseract-ocr-eng> |
| ghostscript (with libjbig2dec0, fonts-urw-base35) | AGPL-3.0-or-later; fonts AGPL-3.0 with font exception | <https://packages.debian.org/trixie/ghostscript> |
| **unpaper** | **GPL-2.0-only** (separate program, see summary) | <https://packages.debian.org/trixie/unpaper> |
| pngquant (linked to libimagequant) | BSD-2-Clause; libimagequant GPL-3.0-or-later | <https://packages.debian.org/trixie/pngquant> |
| jbig2 (jbig2enc) | Apache-2.0 (some files GPL-3.0-or-later) | <https://packages.debian.org/trixie/jbig2> |
| libseccomp2 | LGPL-2.1 | <https://packages.debian.org/trixie/libseccomp2> |

Python packages (`requirements/ocr.txt`), in addition to the common ones:

| Package | Version | Licence | Link |
|---|---|---|---|
| ocrmypdf | 17.13.0 | MPL-2.0 | <https://pypi.org/project/ocrmypdf/> |
| opencv-python-headless (bundles OpenCV and FFmpeg, LGPL-2.1-or-later) | 5.0.0.93 | Apache-2.0 | <https://pypi.org/project/opencv-python-headless/> |
| numpy (wheel bundles OpenBLAS and the GCC Fortran runtime) | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | <https://pypi.org/project/numpy/> |
| pikepdf | 10.17.0 | MPL-2.0 | <https://pypi.org/project/pikepdf/> |
| pypdfium2 | 5.14.0 | BSD-3-Clause and Apache-2.0 | <https://pypi.org/project/pypdfium2/> |
| pillow | 12.3.0 | MIT-CMU | <https://pypi.org/project/pillow/> |
| img2pdf | 0.6.3 | LGPL-3.0 | <https://pypi.org/project/img2pdf/> |
| fpdf2 | 2.8.9 | LGPL-3.0-only | <https://pypi.org/project/fpdf2/> |
| fonttools | 4.66.1 | MIT | <https://pypi.org/project/fonttools/> |
| uharfbuzz (bundles HarfBuzz, MIT) | 0.56.3 | Apache-2.0 | <https://pypi.org/project/uharfbuzz/> |
| pdfminer-six | 20260107 | MIT | <https://pypi.org/project/pdfminer-six/> |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | <https://pypi.org/project/cryptography/> |
| cffi | 2.1.1 | MIT-0 | <https://pypi.org/project/cffi/> |
| pycparser | 3.11 | BSD-3-Clause | <https://pypi.org/project/pycparser/> |
| charset-normalizer | 3.5.2 | MIT | <https://pypi.org/project/charset-normalizer/> |
| lxml | 6.1.3 | BSD-3-Clause | <https://pypi.org/project/lxml/> |
| jsonschema | 4.26.0 | MIT | <https://pypi.org/project/jsonschema/> |
| jsonschema-specifications | 2025.9.1 | MIT | <https://pypi.org/project/jsonschema-specifications/> |
| referencing | 0.37.0 | MIT | <https://pypi.org/project/referencing/> |
| rpds-py | 2026.9.1 | MIT | <https://pypi.org/project/rpds-py/> |
| attrs | 26.1.0 | MIT | <https://pypi.org/project/attrs/> |
| rich | 15.0.0 | MIT | <https://pypi.org/project/rich/> |
| markdown-it-py | 4.2.0 | MIT | <https://pypi.org/project/markdown-it-py/> |
| mdurl | 0.1.2 | MIT | <https://pypi.org/project/mdurl/> |
| pygments | 2.21.0 | BSD-2-Clause | <https://pypi.org/project/pygments/> |
| pluggy | 1.6.0 | MIT | <https://pypi.org/project/pluggy/> |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | <https://pypi.org/project/packaging/> |

## worker-ebook image (optional profile `ebook`)

Upstream binaries, downloaded at build time and checked against pinned SHA-256 digests:

| Component | Version | Licence | Source |
|---|---|---|---|
| **Calibre** (`/opt/calibre`, only `ebook-convert` on PATH) | 9.16.0 | **GPL-3.0** | <https://github.com/kovidgoyal/calibre/tree/v9.16.0>, <https://download.calibre-ebook.com/9.16.0/> |
| **Pandoc** (`/usr/local/bin/pandoc`, static binary; licence in `/usr/share/doc/pandoc/`) | 3.11 | **GPL-2.0-or-later** (the static binary also contains Haskell libraries under BSD-style licences) | <https://github.com/jgm/pandoc/releases/tag/3.11> |

Calibre's binary bundle carries its own copies of, among others (from its build manifest
`bypy/sources.json`): Python 3.14, Qt 6.10 and Qt WebEngine (LGPL-3.0, with Chromium under BSD and
other licences), FFmpeg 7.1 (LGPL/GPL), Poppler 25.11 (GPL-2.0/GPL-3.0), PoDoFo (LGPL-2.0-or-later),
ICU, libxml2, libxslt, OpenSSL 3.5, SQLite, Hunspell, espeak-ng (GPL-3.0-or-later), ONNX Runtime
(MIT), image codecs and fonts, and **unrar 7.2.2 (non-free unRAR licence, see the summary)**.
Their licence texts are inside `/opt/calibre`.

Debian 13 packages (current trixie version at build time):

| Package | Licence | Link |
|---|---|---|
| djvulibre-bin | GPL-2.0-or-later | <https://packages.debian.org/trixie/djvulibre-bin> |
| libarchive13t64 (reads CBR/RAR itself; no unrar-free or 7zip) | BSD-2-Clause (some files other BSD variants, MIT) | <https://packages.debian.org/trixie/libarchive13t64> |
| libegl1, libopengl0, libglx0 (libglvnd) | MIT style | <https://packages.debian.org/trixie/libegl1> |
| libxcb-cursor0 | MIT | <https://packages.debian.org/trixie/libxcb-cursor0> |
| libxkbcommon0 | MIT | <https://packages.debian.org/trixie/libxkbcommon0> |
| libfontconfig1 | MIT style (fontconfig licence) | <https://packages.debian.org/trixie/libfontconfig1> |
| libnss3 | MPL-2.0 | <https://packages.debian.org/trixie/libnss3> |
| libasound2t64 | LGPL-2.1-or-later | <https://packages.debian.org/trixie/libasound2t64> |
| fonts-noto-core | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-noto-core> |
| fonts-dejavu-core | Bitstream Vera licence | <https://packages.debian.org/trixie/fonts-dejavu-core> |
| fonts-liberation | SIL OFL-1.1 | <https://packages.debian.org/trixie/fonts-liberation> |
| libseccomp2 | LGPL-2.1 | <https://packages.debian.org/trixie/libseccomp2> |

Python packages (`requirements/ebook.txt`), in addition to the common ones:

| Package | Version | Licence | Link |
|---|---|---|---|
| libarchive-c | 5.3 | CC0-1.0 | <https://pypi.org/project/libarchive-c/> |
| img2pdf | 0.6.3 | LGPL-3.0 | <https://pypi.org/project/img2pdf/> |
| pikepdf | 10.17.0 | MPL-2.0 | <https://pypi.org/project/pikepdf/> |
| pypdfium2 | 5.14.0 | BSD-3-Clause and Apache-2.0 | <https://pypi.org/project/pypdfium2/> |
| pillow | 12.3.0 | MIT-CMU | <https://pypi.org/project/pillow/> |
| lxml | 6.1.3 | BSD-3-Clause | <https://pypi.org/project/lxml/> |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | <https://pypi.org/project/packaging/> |

## redis service, Valkey (not built by FileX)

`compose.yaml` pulls the official `valkey/valkey:8.1-alpine` image (pinned by digest) from Docker
Hub; FileX only mounts its own start script and ACL file into it. The service is still called
`redis`, and the FileX code talks to it with the redis-py client.

| Component | Version | Licence | Link |
|---|---|---|---|
| Valkey | 8.1 (8.1.10 at the pinned digest) | BSD-3-Clause | <https://github.com/valkey-io/valkey/blob/8.1.10/COPYING> |
| Alpine Linux base | from the image | various free licences | <https://alpinelinux.org/> |

## Build-only tools (not shipped)

These run during `docker compose build` or in CI and do not end up in any image: the
`node:24-trixie-slim` build stage (Node.js, npm), every npm dev dependency in `web/package.json`,
`setuptools` (uninstalled after the build), and the `ebook-fetch` stage that downloads Calibre and
Pandoc.
