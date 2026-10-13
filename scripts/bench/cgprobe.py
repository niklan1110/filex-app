"""cgroup v2 helper for scripts/bench/weak_hardware.py. Runs in a privileged container that sees
the host's cgroup tree (``--privileged --cgroupns=host``), reads one JSON command per stdin line
and answers one JSON line. Only ever touches /sys/fs/cgroup/<parent> and its children.

Commands:
  {"cmd": "limits", "parent": "filex-bench", "mem_mb": 2048, "cpus": 2}  create + cap the parent
  {"cmd": "reset", "ids": {"api": "<container id>", ...}}  reclaim the parent's page cache, then
                                                           reset memory.peak (fd-local, 6.12+)
  {"cmd": "read"}   peak / current / anon / file MB and oom_kill per container and "_parent"
  {"cmd": "rmdir"}  remove the (empty) parent
"""

from __future__ import annotations

import json
import os
import sys

ROOT = "/sys/fs/cgroup"
MB = 2**20
parent = ""
peaks: dict[str, int] = {}  # name -> open fd on memory.peak (the reset is per fd)
dirs: dict[str, str] = {}


def write(path: str, value: str) -> None:
    with open(path, "w") as f:
        f.write(value)


def read(path: str) -> str:
    with open(path) as f:
        return f.read()


def kv(path: str) -> dict[str, int]:
    return {k: int(v) for k, v in (line.split() for line in read(path).splitlines())}


def handle(c: dict) -> dict:
    global parent
    if c["cmd"] == "limits":
        parent = os.path.join(ROOT, c["parent"])
        os.makedirs(parent, exist_ok=True)
        write(os.path.join(parent, "cgroup.subtree_control"), "+cpu +memory +pids")
        mem = str(c["mem_mb"] * MB) if c.get("mem_mb") else "max"
        write(os.path.join(parent, "memory.max"), mem)
        write(os.path.join(parent, "memory.swap.max"), "0")
        cpu = f"{int(c['cpus'] * 100000)} 100000" if c.get("cpus") else "max 100000"
        write(os.path.join(parent, "cpu.max"), cpu)
        return {
            "memory.max": read(os.path.join(parent, "memory.max")).strip(),
            "cpu.max": read(os.path.join(parent, "cpu.max")).strip(),
        }
    if c["cmd"] == "reset":
        for fd in peaks.values():
            os.close(fd)
        peaks.clear()
        dirs.clear()
        dirs["_parent"] = parent
        try:  # drop the stack's own page cache first, so peaks are this job's (only this subtree)
            write(os.path.join(parent, "memory.reclaim"), "4G")
        except OSError:  # EAGAIN: reclaimed less than asked, which is all that was reclaimable
            pass
        for name, cid in c["ids"].items():
            dirs[name] = os.path.join(parent, cid)
        for name, d in dirs.items():
            fd = os.open(os.path.join(d, "memory.peak"), os.O_RDWR)
            os.write(fd, b"reset")
            peaks[name] = fd
        return {"reset": sorted(dirs)}
    if c["cmd"] == "read":
        out = {}
        for name, d in dirs.items():
            try:
                os.lseek(peaks[name], 0, os.SEEK_SET)
                peak = int(os.read(peaks[name], 64))
                stat = kv(os.path.join(d, "memory.stat"))
                out[name] = {
                    "peak_mb": round(peak / MB, 1),
                    "current_mb": round(int(read(os.path.join(d, "memory.current"))) / MB, 1),
                    "anon_mb": round(stat["anon"] / MB, 1),
                    "file_mb": round(stat["file"] / MB, 1),
                    "oom_kill": kv(os.path.join(d, "memory.events")).get("oom_kill", 0),
                }
            except (OSError, ValueError) as e:  # container gone (restarted after an OOM kill)
                out[name] = {"error": str(e)}
        return out
    if c["cmd"] == "rmdir":
        for fd in peaks.values():
            os.close(fd)
        peaks.clear()
        os.rmdir(parent)
        return {"rmdir": parent}
    return {"error": f"unknown {c['cmd']}"}


for line in sys.stdin:
    try:
        reply = handle(json.loads(line))
    except Exception as e:  # noqa: BLE001 - report any error and keep serving
        reply = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(reply), flush=True)
