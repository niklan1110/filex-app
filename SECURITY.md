# Security policy

FileX opens files from people you may not trust (PDFs, Office documents, images, e-books), so
security reports are welcome and taken seriously.

## Supported versions

| Version | Supported |
|---|---|
| Latest release | Yes |
| `main` branch | Yes |
| Older releases | No: update to the latest release first and check that the problem is still there |

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a security problem.**

Report it privately through GitHub instead:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**, or go straight to
   <https://github.com/niklan1110/filex-app/security/advisories/new>.

Only the maintainers can see the report. You need a GitHub account.

### What to include

- The FileX version (release tag or commit) and how you run it: local mode (`FILEX_AUTH=none`),
  access tokens or a sign-in server; your OS and Docker version.
- The affected part: the API, the web UI, a tool (its id, e.g. `pdf.compress`), a worker image
  or the compose setup.
- Steps to reproduce. If a file triggers it, attach a **minimal** sample, or a script that
  builds one. Please do not send real documents with personal data.
- What happens and what you expected, and the impact as you see it (for example: reading another
  job's files, escaping a worker's sandbox, running code, a request from another website being
  accepted, denial of service).
- Whether you want to be credited, and under which name.

### What happens next

This is a spare-time project, so the following is best effort, not a guarantee:

- An acknowledgement within **7 days**.
- A first assessment (confirmed or not, and how severe) within **30 days**.
- A fix in a new release as soon as it is ready. Problems that let one user read another user's
  files, or that let a file escape a worker's sandbox, come first.
- A GitHub security advisory once a fix is out, crediting you if you want.

Please give us a reasonable time to ship a fix before you disclose the problem publicly. We
will agree a date with you; 90 days from the report is the usual upper bound.

## Scope

In scope: everything in this repository: the API, the web UI, the tools and their engine
settings, the worker sandbox (seccomp, Landlock, resource limits), the Dockerfiles and
`compose.yaml`.

Known limits that are documented and not by themselves a vulnerability (see `docs/install.md`
and `docs/adr/`):

- **Local mode** (`FILEX_AUTH=none`) has no sign-in by design. It is meant for one person on the
  computer FileX runs on, and FileX refuses to start in that mode on a non-loopback address. A
  way around that guard, or a way for another website or device to use a local-mode FileX, is in
  scope.
- **Plain HTTP on a home network** when access tokens are used without a reverse proxy.
- Bugs in the third-party engines themselves (Ghostscript, LibreOffice, MuPDF, Tesseract,
  Calibre, Pandoc and others) should go to their projects. A FileX setting or wrapper that makes
  such a bug reachable or worse is in scope.

Out of scope: running FileX in ways the docs say not to (for example publishing the port on
`0.0.0.0` or to the internet, removing the container hardening in `compose.yaml`, or setting
`FILEX_LANDLOCK=off`), and findings that need an already compromised host.
