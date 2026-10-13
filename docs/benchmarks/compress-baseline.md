# pdf.compress baseline (plan tasks 24 and 25)

Measured 2026-10-11 in the worker-core image (Ghostscript 10.05, Debian trixie), `--cpus 2`,
no network, on a desktop x86-64 host with Docker Desktop. Sizes are in MB of 1,000,000 bytes.
Times are wall-clock per file for the whole tool run (the pikepdf rewrite plus every Ghostscript
pass).

Reproduce (inside the worker-core image, checkout mounted at `/src`, `PYTHONPATH=/src`):

```sh
python scripts/bench/compress_baseline.py tests/corpus/pdf/scanned_3p.pdf:2,0.3,0.15,0.1,0.05
python scripts/bench/compress_baseline.py tests/corpus/generated/scan_50p_30mb.pdf
python scripts/bench/compress_baseline.py --no-presets tests/corpus/generated/scan_50p_30mb.pdf:20,10,5,3,2,1
```

`scan_50p_30mb.pdf` comes from `python scripts/make_corpus.py --large --only "scan_50p*"`.

## Presets

Preset settings (dpi / QFactor): LOW 200 / 0.40, MEDIUM 100 / 1.2, HIGH 85 / 1.5, EXTREME 72 / 2.0.

### scanned_3p.pdf (0.45 MB, 3 pages, 150 dpi)

| Level | Output | Saved | Time |
|---|---|---|---|
| LOW | 0.45 MB | 0% (`NOT_SMALLER`, input kept) | 0.1 s |
| MEDIUM | 0.19 MB | 57% | 0.1 s |
| HIGH | 0.13 MB | 70% | 0.1 s |
| EXTREME | 0.09 MB | 81% | 0.1 s |

### scan_50p_30mb.pdf (31.88 MB, 50 pages, 300 dpi)

| Level | Output | Saved | Time |
|---|---|---|---|
| LOW | 23.93 MB | 25% | 1.9 s |
| MEDIUM | 5.47 MB | 83% | 1.5 s |
| HIGH | 3.70 MB | 88% | 1.5 s |
| EXTREME | 2.34 MB | 93% | 1.4 s |

## Target size (`targetMb`)

The search (`search_target` in `filex/tools/optimize/pdf_compress.py`): if the lossless rewrite
(or the input) is already under the target it is returned with no Ghostscript pass. Otherwise
EXTREME runs first; if it is still too big it is the result and `TARGET_NOT_REACHED` says so.
If it fits, bisection on one quality scale (300 dpi / QFactor 0.25 down to EXTREME, presets on
the way) keeps the largest result under the target. It stops after 6 Ghostscript passes, at a
result of 90% of the target or more, or when the 240 s budget has less than twice the slowest
pass left.

### scanned_3p.pdf

| Target | Output | Passes (dpi/QFactor tried, in order) | Time | Warning |
|---|---|---|---|---|
| 2 MB | 0.45 MB | 0 (input already under the target) | 0.0 s | - |
| 0.3 MB | 0.29 MB | 4 (72/2.0, 147/0.71, 103/1.19, 123/0.92) | 0.3 s | - |
| 0.15 MB | 0.15 MB | 6 (72/2.0, 147/0.71, 103/1.19, 86/1.54, 94/1.35, 90/1.45) | 0.4 s | - |
| 0.1 MB | 0.10 MB | 6 (72/2.0, 147/0.71, 103/1.19, 86/1.54, 79/1.76, 75/1.87) | 0.4 s | - |
| 0.05 MB | 0.09 MB | 1 (72/2.0) | 0.1 s | Best we could do: 88 KB (target 50 KB) for "scanned_3p.pdf". |

### scan_50p_30mb.pdf

| Target | Output | Passes (dpi/QFactor tried, in order) | Time | Warning |
|---|---|---|---|---|
| 20 MB | 19.30 MB | 6 (72/2.0, 147/0.71, 210/0.42, 176/0.55, 192/0.48, 184/0.51) | 8.9 s | - |
| 10 MB | 9.44 MB | 6 (72/2.0, 147/0.71, 103/1.19, 123/0.92, 134/0.81, 129/0.86) | 7.6 s | - |
| 5 MB | 4.62 MB | 5 (72/2.0, 147/0.71, 103/1.19, 86/1.54, 94/1.35) | 6.1 s | - |
| 3 MB | 2.99 MB | 5 (72/2.0, 147/0.71, 103/1.19, 86/1.54, 79/1.76) | 6.1 s | - |
| 2 MB | 2.34 MB | 1 (72/2.0) | 1.4 s | Best we could do: 2.34 MB (target 2 MB) for "scan_50p_30mb.pdf". |
| 1 MB | 2.34 MB | 1 (72/2.0) | 1.4 s | Best we could do: 2.34 MB (target 1 MB) for "scan_50p_30mb.pdf". |

Every reachable target ends between 91% and 100% of the target in at most 6 passes; an
unreachable one costs one pass.

## Not measured yet (rest of task 24)

- Visual quality (SSIM) per preset, and OCRmyPDF `-O3`-style optimisation (pikepdf + pngquant +
  jbig2) as a comparison: the rest of task 24, `docs/benchmarks/compress.md`.
- Times on a weak home-server CPU (x86-64): the numbers above are from a desktop CPU.
- `scan_50p_30mb.pdf` is clean synthetic text on white (no grain), which decodes and
  recompresses faster, and to smaller files, than real photographed or colour scans.
  `scan_200mb.pdf` (grainy) is the harder case and is not in this table.
