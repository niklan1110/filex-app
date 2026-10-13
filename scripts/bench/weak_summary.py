"""Markdown tables from weak_hardware.py results (plan task 43):

    python scripts/bench/weak_summary.py r2g.json r4g.json
    python scripts/bench/weak_summary.py --calibre cal.json

Per case: median wall time (client: upload to SUCCEEDED) with min-max, median step time (worker
log), median peak of the busiest worker container and of the whole stack, and failures / OOM
kills over all reps. Peaks are cgroup memory.peak (anon + page cache) after the stack's page
cache was reclaimed, so they are what the job itself brought in.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict


def spread(xs: list[float], unit: str = "", digits: int = 1) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    f = f"{{:.{digits}f}}"
    m = f.format(st.median(xs))
    return f"{m}{unit} ({f.format(min(xs))}-{f.format(max(xs))})" if len(xs) > 1 else m + unit


def stack(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    c = d["config"]
    print(
        f"\n### {c['mem_mb']} MB / {c['cpus']} CPUs, profiles `{c['COMPOSE_PROFILES']}`, "
        f"FILEX_MEM_CORE={c['FILEX_MEM_CORE']}, _OFFICE={c['FILEX_MEM_OFFICE']}, "
        f"_OCR={c['FILEX_MEM_OCR']}, _EBOOK={c['FILEX_MEM_EBOOK']}, "
        f"FILEX_CONCURRENCY={c['FILEX_CONCURRENCY']}, FILEX_HEAVY_SLOTS={c['FILEX_HEAVY_SLOTS']}\n"
    )
    idle = d.get("idle", {})
    if idle:
        names = [k for k in idle if ":" not in k and k != "_parent"]
        print(
            "Idle (MB, memory.current; anon in brackets): "
            + ", ".join(f"{n} {idle[n]:.0f} [{idle[n + ':anon']:.0f}]" for n in sorted(names))
            + f"; whole stack {idle['_parent']:.0f} [{idle['_parent:anon']:.0f}]\n"
        )
    steps = d.get("step_s", {})
    print(
        "| Case | Wall s, median (min-max) | Step s | Busiest worker peak MB | Stack peak MB "
        "| Failed / OOM |"
    )
    print("|---|---|---|---|---|---|")
    for name, runs in d["runs"].items():
        walls, step, wpeak, ppeak, bad, oom = [], [], defaultdict(list), [], [], 0
        for r in runs:
            walls.append(max(r["wall_s"]))
            step.append(max((steps.get(j) or 0) for j in r["job"]) if r["job"][0] else None)
            for k, v in r["mem"].items():
                if k.startswith("worker") and "peak_mb" in v:
                    wpeak[k].append(v["peak_mb"])
                oom += v.get("oom_kill", 0) if k != "_parent" else 0
            ppeak.append(r["mem"]["_parent"].get("peak_mb"))
            bad += [
                f"{s}:{(e or {}).get('code', '')}"
                for s, e in zip(r["status"], r["error"])
                if s != "SUCCEEDED"
            ]
        busy = max(wpeak, key=lambda k: st.median(wpeak[k]))
        print(
            f"| {name} | {spread(walls)} | {spread(step)} | {busy.removeprefix('worker-')} "
            f"{spread(wpeak[busy], digits=0)} | {spread(ppeak, digits=0)} | "
            f"{len(bad)}/{len(runs) * len(runs[0]['status'])} {' '.join(sorted(set(bad)))}"
            f"{' OOM ' + str(oom) if oom else ''} |"
        )
    print(f"\nRestarts / OOMKilled per container: {d.get('restarts')}")


def calibre(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    rows = defaultdict(list)
    for r in d["runs"]:
        rows[(r["rlimit_mb"], r["container_mb"], r["est_pages"])].append(r)
    print("| RLIMIT_DATA MB | Container MB | Est. pages | Result | PDF pages | Time s | Peak MB |")
    print("|---|---|---|---|---|---|---|")
    for (rl, ct, n), rs in sorted(rows.items()):
        res = ", ".join(
            sorted(
                {
                    "ok"
                    if r["rc"] == 0 and r.get("pages")
                    else f"rc={r['rc']}" + (" OOM" if r.get("oom_kill") else "")
                    for r in rs
                }
            )
        )
        print(
            f"| {rl} | {ct} | {n} | {res} | {spread([r.get('pages') for r in rs], digits=0)} "
            f"| {spread([r.get('s') for r in rs])} | "
            f"{spread([r.get('peak_mb') for r in rs], digits=0)} |"
        )


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--calibre":
        for p in args[1:]:
            calibre(p)
    else:
        for p in args:
            stack(p)
