"""Per-step sandbox pieces (ADR-0007 items 1-3, 7; review H1 fixes 1-2). Linux only; on other
platforms (developer machines) the functions that need Linux are skipped by their callers.

The seccomp filter talks to libseccomp (``libseccomp2``, already in Debian images) through
ctypes rather than the ``python3-seccomp`` package, whose Debian build lands in the system
Python's dist-packages and is invisible to the image's ``/usr/local/bin/python``.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import stat
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

MiB = 1024 * 1024
LINUX = sys.platform.startswith("linux")

# One filter for every group, no per-tool loosening without a security review (ADR-0007).
DENIED_SYSCALLS = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "keyctl",
    "bpf",
    "userfaultfd",
    "perf_event_open",
    "mount",
    # io_uring can create sockets without socket() (IORING_OP_SOCKET); Docker's default
    # profile blocks it too, this keeps the step safe on hosts that do not.
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    # Signals by pidfd, and another process's fds (security review H-1, M-3).
    "pidfd_send_signal",
    "pidfd_getfd",
)
AF_UNIX = 1  # the only socket family a step may open (LibreOffice and Qt use it internally)
# A step must not freeze its runner (security review H-1). In the image the runner is PID 1,
# which the kernel already shields; this is the second layer, in case `init` comes back.
STOP_SIGNALS = (19, 20, 21, 22)  # SIGSTOP, SIGTSTP, SIGTTIN, SIGTTOU (x86-64 and arm64)
_SIGNAL_ARG = {"kill": 1, "tkill": 1, "tgkill": 2, "rt_sigqueueinfo": 1, "rt_tgsigqueueinfo": 2}

_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_EPERM = 0x00050000 | 1  # SCMP_ACT_ERRNO(EPERM)
_SCMP_CMP_NE = 1
_SCMP_CMP_MASKED_EQ = 7


class _ArgCmp(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def _libseccomp() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2")
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_ArgCmp),
    ]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    return lib


def seccomp_available() -> bool:
    try:
        _libseccomp()
    except OSError:
        return False
    return LINUX


def install_seccomp() -> None:
    """Deny every socket family but AF_UNIX, process-inspection syscalls and stop signals for
    this process and its children. Raises OSError if the filter cannot be installed; the step
    must not run then."""
    lib = _libseccomp()
    ctx = lib.seccomp_init(_SCMP_ACT_ALLOW)
    if not ctx:
        raise OSError("seccomp_init failed")
    try:

        def deny(name: str, *args: _ArgCmp, required: bool = False) -> None:
            nr = lib.seccomp_syscall_resolve_name(name.encode())
            if nr < 0:
                if required:
                    raise OSError(f"seccomp cannot resolve {name}")
                return  # not a syscall on this architecture
            arr = (_ArgCmp * len(args))(*args) if args else None
            rc = lib.seccomp_rule_add_array(ctx, _SCMP_ACT_EPERM, nr, len(args), arr)
            if rc < 0:
                raise OSError(-rc, f"seccomp rule for {name}")

        for name in DENIED_SYSCALLS:
            deny(name)
        # An allowlist: anything but AF_UNIX is denied. The compare is on all 64 bits, so junk
        # in the upper half of the register fails closed.
        deny("socket", _ArgCmp(0, _SCMP_CMP_NE, AF_UNIX, 0), required=True)
        deny("socketpair", _ArgCmp(0, _SCMP_CMP_NE, AF_UNIX, 0))
        for name, arg in _SIGNAL_ARG.items():
            for sig in STOP_SIGNALS:  # masked: the kernel reads the signal as a 32-bit int
                deny(name, _ArgCmp(arg, _SCMP_CMP_MASKED_EQ, 0xFFFFFFFF, sig))
        rc = lib.seccomp_load(ctx)  # also sets no_new_privs
        if rc < 0:
            raise OSError(-rc, "seccomp_load")
    finally:
        lib.seccomp_release(ctx)


def apply_rlimits(memory_mb: int, cpu_s: int, fsize_mb: int, nofile: int, data: bool) -> None:
    """Called by the step child on itself before it imports the tool."""
    import resource  # POSIX only

    mem = memory_mb * MiB
    resource.setrlimit(resource.RLIMIT_DATA if data else resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_mb * MiB, fsize_mb * MiB))
    resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _prctl(option: int, value: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), f"prctl({option})")


def harden_runner() -> None:
    """PR_SET_CHILD_SUBREAPER (orphans of a step re-parent to the runner, so the sweep finds
    them) and PR_SET_DUMPABLE=0 (a step cannot read the runner's /proc environ or memory)."""
    if LINUX:
        _prctl(36, 1)  # PR_SET_CHILD_SUBREAPER
        _prctl(4, 0)  # PR_SET_DUMPABLE


def harden_step() -> None:
    """First thing the step child does (security review M-3, L-5). ``execve`` made it dumpable
    again, so a sibling step could open its /proc files; the runner sends the request (with
    secrets) only after this. The OOM score goes first: /proc/self turns root-owned once the
    process is not dumpable, and the container's OOM killer should pick a step, not the runner."""
    Path("/proc/self/oom_score_adj").write_text("1000")
    _prctl(4, 0)  # PR_SET_DUMPABLE


def processes() -> Iterator[tuple[int, int, int, str]]:
    """(pid, ppid, session id, state) of every visible process."""
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                stat = f.read().decode("ascii", "replace")
        except OSError:
            continue  # gone already
        fields = stat[stat.rindex(")") + 2 :].split()
        yield int(name), int(fields[1]), int(fields[3]), fields[0]


# --- Landlock (ADR-0007 item 11, review section 3): each step sees only its own files.
#
# The child restricts itself before it runs the tool: read and execute on the system paths and
# Python's import path, read and write on its scratch dir and output dirs, nothing else. Other
# jobs under /data, the runner's secrets and the shared /tmp stay out of reach. From ABI 4 TCP
# bind and connect are denied as well (no port is allowed), next to the seccomp socket rule;
# from ABI 6 the step can no longer signal or reach abstract UNIX sockets outside itself.

LANDLOCK_POLICIES = ("auto", "required", "off")
LANDLOCK_MIN_ABI = 2  # ABI 1 refuses every rename or link across directories (no REFER right)
_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446  # x86-64 and arm64
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

FS_EXECUTE, FS_WRITE_FILE, FS_READ_FILE, FS_READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
FS_REMOVE_FILE, FS_MAKE_SOCK = 1 << 5, 1 << 9
FS_REFER, FS_TRUNCATE, FS_IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
FS_READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
# The only rights a rule on a file (not a directory) may carry.
FS_FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1

# Read and execute for every group. Engines install under /usr and /opt (LibreOffice, Calibre,
# the LibreOffice profile template); /etc holds the loader, font and TLS config; /proc and /sys
# are what /proc/self, CPU counts and cgroup limits need (another process's /proc files stay
# closed: steps are not dumpable). /var/cache/fontconfig is the system font cache.
SYSTEM_READ = (
    "/usr",
    "/lib",
    "/lib64",
    "/lib32",
    "/bin",
    "/sbin",
    "/etc",
    "/opt",
    "/proc",
    "/sys",
    "/var/cache/fontconfig",
)
# Character devices engines open by name; read and write, files only.
DEVICES = ("/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom")
# LibreOffice binds its single-instance pipe (OSL_PIPE_<uid>_<hash of the profile URL>) in /tmp
# whatever TMPDIR says, and unlinks it on exit. A step may create and remove sockets there but
# not list, read or write /tmp: the pipe names hash the step's scratch path, which holds a
# 128-bit job id, so another step cannot find them.
SOCKET_DIRS = ("/tmp",)
SOCKET_RIGHTS = FS_MAKE_SOCK | FS_REMOVE_FILE


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1  # __attribute__((packed)) in <linux/landlock.h>: 12 bytes
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, 0 if it has none (or the syscall is filtered).
    Probed with the syscall itself: /sys/kernel/security/lsm is not readable in a container."""
    if not LINUX:
        return 0
    try:
        libc = _libc()
    except (OSError, TypeError):  # no C library to load (tests that fake LINUX elsewhere)
        return 0
    abi = libc.syscall(
        ctypes.c_long(_SYS_CREATE_RULESET),
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(_CREATE_RULESET_VERSION),
    )
    return max(int(abi), 0)


def landlock_policy(mode: str | None, abi: int) -> tuple[int, str]:
    """(ABI the steps enforce, 0 for none; the status label). ``mode`` is FILEX_LANDLOCK:
    ``auto`` (default) enforces when the kernel can, ``required`` raises when it cannot,
    ``off`` never enforces."""
    mode = (mode or "auto").strip().lower()
    if mode not in LANDLOCK_POLICIES:
        raise ValueError(f"FILEX_LANDLOCK must be one of {', '.join(LANDLOCK_POLICIES)}")
    if mode == "off":
        return 0, "off"
    if abi >= LANDLOCK_MIN_ABI:
        return abi, f"abi{abi}"
    if mode == "required":
        raise OSError(f"FILEX_LANDLOCK=required but the kernel's Landlock ABI is {abi}")
    return 0, "unavailable"


def handled_fs(abi: int) -> int:
    bits = (1 << 13) - 1  # ABI 1: EXECUTE .. MAKE_SYM
    if abi >= 2:
        bits |= FS_REFER
    if abi >= 3:
        bits |= FS_TRUNCATE
    if abi >= 5:
        bits |= FS_IOCTL_DEV
    return bits


@dataclass(frozen=True)
class Ruleset:
    abi: int
    fs: int  # handled filesystem rights: everything the ABI knows, so unlisted paths get none
    net: int  # handled TCP rights with no port rule: bind and connect always denied
    scoped: int
    read: tuple[str, ...]  # read and execute
    write: tuple[str, ...]  # every handled right
    devices: tuple[str, ...]
    sockets: tuple[str, ...] = SOCKET_DIRS


def landlock_ruleset(abi: int, write: Iterable[str], read: Iterable[str] = ()) -> Ruleset:
    """The step's ruleset: SYSTEM_READ plus ``read`` (Python's import path), and ``write`` (the
    scratch dir and output dirs). Paths are not checked here; missing ones are skipped when the
    ruleset is installed, except writable ones."""
    if abi < 1:
        raise ValueError("no Landlock ABI")
    return Ruleset(
        abi=abi,
        fs=handled_fs(abi),
        net=NET_BIND_TCP | NET_CONNECT_TCP if abi >= 4 else 0,
        scoped=SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL if abi >= 6 else 0,
        read=tuple(dict.fromkeys([*SYSTEM_READ, *(p for p in read if p)])),
        write=tuple(dict.fromkeys(write)),
        devices=DEVICES,
    )


def _open_path(path: str) -> tuple[int, bool] | None:
    """(O_PATH fd, is a directory), or None if the path does not exist."""
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    return fd, stat.S_ISDIR(os.fstat(fd).st_mode)


def install_landlock(rs: Ruleset) -> None:
    """Restrict this process and its future children to ``rs``. Raises OSError if the kernel
    refuses any part of it; the step must not run then."""
    libc = _libc()

    def call(nr: int, *args) -> int:
        rc = libc.syscall(ctypes.c_long(nr), *args)
        if rc < 0:
            err = ctypes.get_errno()
            raise OSError(err, f"landlock syscall {nr}: {os.strerror(err)}")
        return int(rc)

    attr = _RulesetAttr(rs.fs, rs.net, rs.scoped)
    size = 8 if rs.abi < 4 else 16 if rs.abi < 6 else 24  # fields the kernel knows
    ruleset_fd = call(
        _SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0)
    )
    try:
        rules = (
            [(p, FS_READ & rs.fs, False) for p in rs.read]
            + [(p, rs.fs, True) for p in rs.write]
            + [
                (p, (FS_READ_FILE | FS_WRITE_FILE | FS_IOCTL_DEV) & rs.fs, False)
                for p in rs.devices
            ]
            + [(p, SOCKET_RIGHTS, False) for p in rs.sockets]
        )
        for path, access, required in rules:
            opened = _open_path(path)
            if opened is None:
                if required:
                    raise OSError(2, f"landlock: {path} is missing")
                continue
            fd, is_dir = opened
            try:
                rule = _PathBeneathAttr(access if is_dir else access & FS_FILE_RIGHTS, fd)
                call(
                    _SYS_ADD_RULE,
                    ctypes.c_int(ruleset_fd),
                    ctypes.c_int(_RULE_PATH_BENEATH),
                    ctypes.byref(rule),
                    ctypes.c_uint32(0),
                )
            finally:
                os.close(fd)
        _prctl(_PR_SET_NO_NEW_PRIVS, 1)  # the container sets it already; Landlock needs it
        call(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0))
    finally:
        os.close(ruleset_fd)
