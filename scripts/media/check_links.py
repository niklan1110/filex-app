"""Check every relative link, image and #anchor in a README (GitHub heading-slug rules).

    python scripts/media/check_links.py [README.md | oss/README.md]

Paths resolve from the repo root (oss/README.md is copied to the public repo's root).
"""

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "README.md")
md = README.read_text(encoding="utf-8")


def slugs(text):
    out, seen = set(), {}
    in_code = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if not m:
            continue
        h = re.sub(r"<[^>]+>", "", m.group(1))
        h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
        s = re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if n == 0 else f"{s}-{n}")
    return out


targets = re.findall(r"\]\(([^)\s]+)\)", md) + re.findall(r'(?:src|srcset|href)="([^"]+)"', md)
bad = 0
for t in sorted(set(targets)):
    if re.match(r"^[a-z]+:", t):
        continue
    path, _, anchor = t.partition("#")
    f = ROOT / unquote(path) if path else README
    if not f.exists():
        print("MISSING FILE", t)
        bad += 1
        continue
    if anchor and f.suffix == ".md" and anchor not in slugs(f.read_text(encoding="utf-8")):
        print("MISSING ANCHOR", t)
        bad += 1
print(f"{len(set(targets))} targets, {bad} problems")
sys.exit(1 if bad else 0)
