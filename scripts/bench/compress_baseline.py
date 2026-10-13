"""Compression baseline for ``pdf.compress`` (plan tasks 24 and 25): every preset and a few
target sizes on the given PDFs, as Markdown tables on stdout. Needs Ghostscript (run it in
the worker-core image):

    python scripts/bench/compress_baseline.py tests/corpus/pdf/scanned_3p.pdf:2,0.3,0.15,0.1,0.05
    python scripts/bench/compress_baseline.py --no-presets generated/scan.pdf:5,2  # targets only
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from filex.tools.optimize import pdf_compress
from filex.tools.optimize.pdf_compress import Options, run


class Ctx:
    def __init__(self, out_dir: Path, name: str) -> None:
        self.out_dir, self.aux, self.target = out_dir, {}, None
        self.input_names, self.max_pages, self.max_pixels = [name], 10_000, 10**9
        self.warnings: list[tuple[str, str]] = []

    def progress(self, fraction: float, message: str = "") -> None:
        pass

    def warn(self, code: str, message: str) -> None:
        self.warnings.append((code, message))


passes: list[pdf_compress.Settings] = []
_real_pass = pdf_compress.ghostscript_pass


def counting_pass(s, *args, **kwargs):
    passes.append(s)
    return _real_pass(s, *args, **kwargs)


pdf_compress.ghostscript_pass = counting_pass


def one(src: Path, **options) -> tuple[int, float, list[tuple[str, str]]]:
    passes.clear()
    with tempfile.TemporaryDirectory() as tmp:
        ctx = Ctx(Path(tmp), src.name)
        t0 = time.perf_counter()
        (out,) = run(ctx, src, Options(**options))
        return out.stat().st_size, time.perf_counter() - t0, ctx.warnings


def main(specs: list[str], levels: list[str]) -> None:
    for spec in specs:
        path, _, targets = spec.partition(":")
        src = Path(path)
        size_in = src.stat().st_size
        print(f"\n### {src.name} ({size_in / 1e6:.2f} MB)\n")
        if levels:
            print("| Level | Output | Saved | Time |\n|---|---|---|---|")
        for level in levels:
            size, secs, _ = one(src, level=level)
            print(f"| {level} | {size / 1e6:.2f} MB | {1 - size / size_in:.0%} | {secs:.1f} s |")
        if not targets:
            continue
        print("\n| Target | Output | Passes (dpi/QFactor tried, in order) | Time | Warning |")
        print("|---|---|---|---|---|")
        for target in (float(t) for t in targets.split(",")):
            size, secs, warnings = one(src, target_mb=target)
            tried = ", ".join(f"{s.dpi}/{s.qfactor}" for s in passes) or "none (lossless)"
            warn = "; ".join(m for c, m in warnings if c == "TARGET_NOT_REACHED") or "-"
            print(
                f"| {target:g} MB | {size / 1e6:.2f} MB | {len(passes)} ({tried}) "
                f"| {secs:.1f} s | {warn} |"
            )


if __name__ == "__main__":
    args = sys.argv[1:]
    presets = "--no-presets" not in args
    main([a for a in args if a != "--no-presets"], list(pdf_compress.PRESETS) if presets else [])
