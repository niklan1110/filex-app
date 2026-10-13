"""Build docs/media/banner-{light,dark}.svg in the Broadsheet look.

Text is outlined from the app's own Archivo font, so the SVGs need no fonts or URLs and render
the same inside GitHub's <img>. Animation is plain CSS; prefers-reduced-motion shows a still frame.

    pip install fonttools brotli
    python scripts/media/make_banner.py
"""

from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[2]
FONT = ROOT / "web/public/fonts/archivo-latin-wdth-wght.woff2"
OUT = ROOT / "docs/media"

_cache = {}


def inst(wdth, wght):
    key = (wdth, wght)
    if key not in _cache:
        f = TTFont(FONT)
        f.flavor = None
        _cache[key] = instancer.instantiateVariableFont(f, {"wdth": wdth, "wght": wght})
    return _cache[key]


def text(s, size, x, y, wdth=125, wght=800, track=0.0, anchor="start"):
    """Return (path_d, width). Baseline at y."""
    f = inst(wdth, wght)
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    upm = f["head"].unitsPerEm
    sc = size / upm
    adv = []
    for ch in s:
        g = cmap.get(ord(ch))
        adv.append((g, gs[g].width * sc if g else size * 0.3))
    width = sum(a for _, a in adv) + track * size * (len(s) - 1)
    cx = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
    nd = 0 if size <= 16 else 1
    pen = SVGPathPen(
        gs, ntos=lambda v: f"{v:.{nd}f}".rstrip("0").rstrip(".") if nd else str(round(v))
    )
    for g, a in adv:
        if g:
            gs[g].draw(TransformPen(pen, (sc, 0, 0, -sc, cx, y)))
        cx += a + track * size
    return pen.getCommands(), width


THEMES = {
    "light": {
        "bg": "#f3f4f5",
        "surface": "#ffffff",
        "s3": "#dcdfe3",
        "border": "#d3d6db",
        "strong": "#6c737e",
        "ink": "#111316",
        "muted": "#484e57",
        "on_ink": "#ffffff",
        "on_ink_muted": "#c9cdd3",
        "on_cat": "#111316",
        "organize": "#a8d5f2",
        "optimize": "#a6dfb8",
        "convert": "#f5c98a",
        "ocr": "#9ddcd3",
        "edit": "#f3b3d6",
        "security": "#f5abae",
        "ebook": "#c9bdf2",
    },
    "dark": {
        "bg": "#0e0f11",
        "surface": "#17191c",
        "s3": "#2a2e33",
        "border": "#2c3036",
        "strong": "#757d89",
        "ink": "#f1f2f4",
        "muted": "#a9b0ba",
        "on_ink": "#0e0f11",
        "on_ink_muted": "#3d424a",
        "on_cat": "#f1f2f4",
        "organize": "#123a57",
        "optimize": "#14412a",
        "convert": "#4d3210",
        "ocr": "#0f403b",
        "edit": "#4b1b3b",
        "security": "#511d23",
        "ebook": "#2e2752",
    },
}


def fold_mix(hexc):
    """color-mix(in srgb, c 70%, #000)"""
    r, g, b = (int(hexc[i : i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*tuple(round(v * 0.7) for v in (r, g, b)))


W, H = 1280, 320
SW, SH, SF = 88, 116, 18  # sheet width, height, fold
LANE_TOP = 236 - SH  # sheets stand on the rule at y=236


def sheet(fill, fold, stroke=None, inner=""):
    st = f' stroke="{stroke}" stroke-width="1.5" stroke-linejoin="round"' if stroke else ""
    return (
        f'<path d="M0 0H{SW - SF}L{SW} {SF}V{SH}H0Z" fill="{fill}"{st}/>'
        f'<path d="M{SW - SF} 0V{SF}H{SW}Z" fill="{fold}"{st}/>{inner}'
    )


def lines(color, y0=30, n=5, w=(56, 62, 48, 60, 40), x=14, gap=11):
    return "".join(
        f'<rect x="{x}" y="{y0 + i * gap}" width="{w[i % len(w)]}" height="4" fill="{color}"/>'
        for i in range(n)
    )


def build(name):
    t = THEMES[name]
    p = []
    # Canvas is itself a sheet with the folded corner.
    FC = 40
    p.append(f'<path d="M0 0H{W - FC}L{W} {FC}V{H}H0Z" fill="{t["bg"]}"/>')
    p.append(f'<path d="M{W - FC} 0V{FC}H{W}Z" fill="{t["muted"]}"/>')

    # --- left: mark, wordmark, statement, eyebrow
    mx, my = 56, 70
    p.append(
        f'<g transform="translate({mx} {my})"><path d="M0 0H38L50 12V58H0Z" fill="{t["ink"]}"/>'
        f'<path d="M38 0V12H50Z" fill="{t["muted"]}"/>'
        f'<path d="M15 21l20 20M35 21L15 41" stroke="{t["bg"]}" stroke-width="5" stroke-linecap="round"/></g>'
    )
    d, _ = text("FileX", 84, 126, 128, 125, 900, -0.045)
    p.append(f'<path d="{d}" fill="{t["ink"]}"/>')
    d, _ = text("Your paper, your server.", 31, 56, 186, 125, 800, -0.025)
    p.append(f'<path d="{d}" fill="{t["ink"]}"/>')
    d, _ = text("PDF · OFFICE · EBOOKS · SCANS · IMAGES", 13, 56, 226, 100, 600, 0.08)
    p.append(f'<path d="{d}" fill="{t["muted"]}"/>')
    # Category swatches, like the filter chips, then the facts.
    cats = ["organize", "optimize", "convert", "ocr", "edit", "security", "ebook"]
    for i, c in enumerate(cats):
        p.append(
            f'<rect x="{56 + i * 18}" y="252" width="12" height="12" fill="{t[c]}" stroke="{fold_mix(t[c])}"/>'
        )
    d, _ = text(
        "30 TOOLS · SELF-HOSTED · NOTHING LEAVES YOUR BOX", 13, 56 + 7 * 18 + 8, 263, 100, 600, 0.08
    )
    p.append(f'<path d="{d}" fill="{t["muted"]}"/>')

    # --- right: the pipeline
    X0, X1 = 600, 1232
    SLAB_X, SLAB_W = 868, 120
    # conveyor rule, moving dashes
    p.append(f'<rect x="{X0}" y="236" width="{X1 - X0}" height="2" fill="{t["ink"]}"/>')
    p.append(
        f'<path class="belt" d="M{X0} 246H{X1}" stroke="{t["strong"]}" stroke-width="2" stroke-dasharray="2 10"/>'
    )
    # eyebrows over the lanes
    d, _ = text("IN", 12, X0 + 16, 92, 100, 600, 0.1)
    p.append(f'<path d="{d}" fill="{t["muted"]}"/>')
    d, _ = text("OUT", 12, X1 - 16, 92, 100, 600, 0.1, anchor="end")
    p.append(f'<path d="{d}" fill="{t["muted"]}"/>')

    in_x0, in_x1 = X0 + 16, SLAB_X + (SLAB_W - SW) / 2
    out_x1 = X1 - 16 - SW

    def label(s, size, y, color, wght=900, wdth=125, track=-0.03):
        d, w = text(s, size, SW / 2, y, wdth, wght, track, anchor="middle")
        if w > SW - 18:  # keep big labels inside the sheet
            d, w = text(s, size * (SW - 18) / w, SW / 2, y, wdth, wght, track, anchor="middle")
        return f'<path d="{d}" fill="{color}"/>'

    def in_sheet(fmt, size_s, scan=False):
        inner = (
            lines(t["s3"])
            + label(fmt, 15, 96, t["ink"], 800)
            + label(size_s, 10, 108, t["muted"], 600, 100, 0.06)
        )
        body = sheet(t["surface"], t["s3"], t["strong"], inner)
        if scan:  # a crooked scan with a grey smudge
            body = (
                f'<g transform="rotate(-7 {SW / 2} {SH})">'
                + sheet(
                    t["surface"],
                    t["s3"],
                    t["strong"],
                    lines(t["s3"])
                    + f'<circle cx="62" cy="70" r="9" fill="{t["s3"]}"/>'
                    + label(fmt, 15, 96, t["ink"], 800)
                    + label(size_s, 10, 108, t["muted"], 600, 100, 0.06),
                )
                + "</g>"
            )
        return body

    def out_sheet(cat, big, small, big_size=30):
        c = t[cat]
        inner = (
            lines(t["on_cat"], n=3).replace("<rect", '<rect opacity=".28"')
            + label(big, big_size, 92, t["on_cat"])
            + label(small, 10, 108, t["on_cat"], 600, 100, 0.06)
        )
        return sheet(c, fold_mix(c), None, inner)

    items = [
        (in_sheet("DOCX", "2.1 MB"), out_sheet("convert", "PDF", "CONVERTED")),
        (in_sheet("PDF", "8.4 MB"), out_sheet("optimize", "−62%", "3.2 MB", 27)),
        (in_sheet("SCAN", "JPG", scan=True), out_sheet("ocr", "OCR", "SEARCHABLE")),
        (in_sheet("EPUB", "1.3 MB"), out_sheet("ebook", "MOBI", "KINDLE", 26)),
    ]
    # Each sheet is defined once and reused.
    defs = "".join(f'<g id="i{i}">{a}</g><g id="o{i}">{b}</g>' for i, (a, b) in enumerate(items))
    p.insert(0, f"<defs>{defs}</defs>")
    # Animated layer: 4 items, 2 s apart, 8 s loop.
    anim = []
    for i in range(len(items)):
        delay = -i * 2
        anim.append(
            f'<use href="#i{i}" class="in" style="animation-delay:{delay}s" x="{in_x0}" y="{LANE_TOP}"/>'
            f'<use href="#o{i}" class="out" style="animation-delay:{delay}s" x="{in_x1}" y="{LANE_TOP}"/>'
        )
    p.append(f'<g class="anim">{"".join(anim)}</g>')
    # Static frame for reduced motion: the compress story.
    p.append(
        f'<g class="still"><use href="#i1" x="{in_x0 + 60}" y="{LANE_TOP}"/>'
        f'<use href="#o1" x="{out_x1 - 60}" y="{LANE_TOP}"/></g>'
    )
    # The press: an ink slab with the mark and a progress bar, drawn over the lanes.
    sy, sh = 104, 132
    p.append(
        f'<g transform="translate({SLAB_X} {sy})"><path d="M0 0H{SLAB_W - 16}L{SLAB_W} 16V{sh}H0Z" fill="{t["ink"]}"/>'
        f'<path d="M{SLAB_W - 16} 0V16H{SLAB_W}Z" fill="{t["muted"]}"/>'
        f'<path d="M48 30l24 24M72 30L48 54" stroke="{t["on_ink"]}" stroke-width="6" stroke-linecap="round"/>'
    )
    d, _ = text("FILEX", 11, SLAB_W / 2, 84, 100, 700, 0.14, anchor="middle")
    p.append(f'<path d="{d}" fill="{t["on_ink_muted"]}"/>')
    p.append(
        f'<rect x="16" y="104" width="{SLAB_W - 32}" height="6" fill="{t["on_ink_muted"]}"/>'
        f'<rect class="bar" x="16" y="104" width="{SLAB_W - 32}" height="6" fill="{t["on_ink"]}"/></g>'
    )
    travel_in = in_x1 - in_x0
    travel_out = out_x1 - in_x1
    css = f"""
.in,.out{{animation:8s linear infinite both}}
.in{{animation-name:fin}}.out{{animation-name:fout}}
@keyframes fin{{0%{{transform:translateX(-24px);opacity:0}}6%{{opacity:1}}47%{{transform:translateX({travel_in:.0f}px);opacity:1}}48%,100%{{transform:translateX({travel_in:.0f}px);opacity:0}}}}
@keyframes fout{{0%,47%{{transform:translateX(0);opacity:0}}48%{{opacity:1;transform:translateX(0)}}88%{{opacity:1}}94%,100%{{transform:translateX({travel_out + 20:.0f}px);opacity:0}}}}
.bar{{transform-box:fill-box;transform-origin:left;animation:bar 2s cubic-bezier(.45,0,.25,1) infinite}}
@keyframes bar{{0%{{transform:scaleX(0)}}80%,100%{{transform:scaleX(1)}}}}
.belt{{animation:belt 1s linear infinite}}
@keyframes belt{{to{{stroke-dashoffset:-12}}}}
.still{{display:none}}
@media (prefers-reduced-motion:reduce){{.anim{{display:none}}.still{{display:inline}}.bar,.belt{{animation:none}}}}
"""
    css = " ".join(l.strip() for l in css.strip().splitlines())
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
        'aria-label="FileX: documents go in on the left, pass through FileX, and come out converted, '
        'compressed, made searchable or turned into another ebook format">'
        "<title>FileX: your paper, your server</title>"
        f"<style>{css}</style>" + "".join(p) + "</svg>\n"
    )
    out = OUT / f"banner-{name}.svg"
    out.write_text(svg, encoding="utf-8")
    print(out.relative_to(ROOT), len(svg.encode()), "bytes")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for n in THEMES:
        build(n)
