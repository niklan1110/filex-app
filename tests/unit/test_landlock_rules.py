"""Landlock ruleset and policy (ADR-0007 item 11). Pure logic plus the syscall sequence against a
fake libc, so it runs on any OS; the real kernel checks are tests/integration/test_landlock.py."""

import ctypes

import pytest

from filex.worker import sandbox
from filex.worker.sandbox import (
    FS_FILE_RIGHTS,
    FS_IOCTL_DEV,
    FS_READ,
    FS_REFER,
    FS_TRUNCATE,
    handled_fs,
    landlock_policy,
    landlock_ruleset,
)

SCRATCH = "/data/tmp/j_x.0"
OUT = "/data/jobs/j_x/steps/0/w0"


@pytest.mark.parametrize(
    ("abi", "has", "lacks"),
    [
        (1, 0, FS_REFER | FS_TRUNCATE | FS_IOCTL_DEV),
        (2, FS_REFER, FS_TRUNCATE | FS_IOCTL_DEV),
        (3, FS_REFER | FS_TRUNCATE, FS_IOCTL_DEV),
        (5, FS_REFER | FS_TRUNCATE | FS_IOCTL_DEV, 0),
        (7, FS_REFER | FS_TRUNCATE | FS_IOCTL_DEV, 0),
    ],
)
def test_handled_rights_follow_the_abi(abi, has, lacks):
    bits = handled_fs(abi)
    assert bits & 0x1FFF == 0x1FFF  # every ABI 1 right
    assert bits & has == has and bits & lacks == 0


def test_tcp_is_denied_from_abi_4_with_no_port_allowed():
    assert landlock_ruleset(3, [SCRATCH]).net == 0
    assert landlock_ruleset(4, [SCRATCH]).net == sandbox.NET_BIND_TCP | sandbox.NET_CONNECT_TCP


def test_signals_and_abstract_sockets_are_scoped_from_abi_6():
    assert landlock_ruleset(5, [SCRATCH]).scoped == 0
    assert landlock_ruleset(7, [SCRATCH]).scoped == (
        sandbox.SCOPE_SIGNAL | sandbox.SCOPE_ABSTRACT_UNIX_SOCKET
    )


def test_only_the_steps_own_dirs_are_writable():
    rs = landlock_ruleset(7, [SCRATCH, OUT], read=["/usr/local/lib/python3.12", ""])
    assert rs.write == (SCRATCH, OUT)
    assert "/usr/local/lib/python3.12" in rs.read and "" not in rs.read
    assert set(sandbox.SYSTEM_READ) <= set(rs.read)


def test_no_data_tmp_or_secret_path_is_granted():
    rs = landlock_ruleset(7, [SCRATCH, OUT])
    for path in rs.read + rs.devices:
        assert not path.startswith(("/data", "/tmp", "/run", "/dev/shm", "/home", "/root")), path


@pytest.mark.parametrize(
    ("mode", "abi", "want"),
    [
        (None, 7, (7, "abi7")),
        ("auto", 4, (4, "abi4")),
        ("AUTO ", 0, (0, "unavailable")),
        ("auto", 1, (0, "unavailable")),  # ABI 1 has no REFER: renames across dirs fail
        ("required", 2, (2, "abi2")),
        ("off", 7, (0, "off")),
    ],
)
def test_policy(mode, abi, want):
    assert landlock_policy(mode, abi) == want


def test_required_without_landlock_raises():
    with pytest.raises(OSError):
        landlock_policy("required", 0)


def test_unknown_policy_is_a_config_error():
    with pytest.raises(ValueError):
        landlock_policy("maybe", 7)


class FakeLibc:
    """Records the Landlock syscalls; ``fail`` names a syscall number that returns -1."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def syscall(self, nr, *args):
        nr = nr.value
        if nr == sandbox._SYS_CREATE_RULESET:
            attr = args[0]._obj
            self.calls.append(
                (
                    "create",
                    args[1].value,
                    attr.handled_access_fs,
                    attr.handled_access_net,
                    attr.scoped,
                )
            )
            rc = 99
        elif nr == sandbox._SYS_ADD_RULE:
            rule = args[2]._obj
            self.calls.append(("rule", rule.parent_fd, rule.allowed_access))
            rc = 0
        else:
            self.calls.append(("restrict", args[0].value))
            rc = 0
        return -1 if nr == self.fail else rc


@pytest.fixture
def fake(monkeypatch):
    fds = {SCRATCH: (10, True), OUT: (11, True), "/usr": (12, True), "/dev/null": (13, False)}
    closed = []
    monkeypatch.setattr(sandbox, "_open_path", lambda p: fds.get(p))
    monkeypatch.setattr(sandbox.os, "close", closed.append)
    monkeypatch.setattr(sandbox, "_prctl", lambda *a: None)
    monkeypatch.setattr(ctypes, "get_errno", lambda: 13)

    def make(fail=None):
        libc = FakeLibc(fail)
        monkeypatch.setattr(sandbox, "_libc", lambda: libc)
        return libc, closed

    return make


def test_install_adds_a_rule_per_existing_path_then_restricts(fake):
    libc, closed = fake()
    sandbox.install_landlock(landlock_ruleset(7, [SCRATCH, OUT]))
    full = handled_fs(7)
    assert libc.calls[0] == ("create", 24, full, 3, 3)
    rules = {fd: access for kind, fd, access, *_ in libc.calls[1:-1] if kind == "rule"}
    assert rules == {
        12: FS_READ,
        10: full,
        11: full,
        13: (sandbox.FS_READ_FILE | sandbox.FS_WRITE_FILE | FS_IOCTL_DEV) & FS_FILE_RIGHTS,
    }
    assert libc.calls[-1] == ("restrict", 99)
    assert sorted(closed) == [10, 11, 12, 13, 99]


def test_attr_size_matches_the_abi(fake):
    libc, _ = fake()
    sandbox.install_landlock(landlock_ruleset(3, [SCRATCH]))
    assert libc.calls[0][1] == 8
    libc, _ = fake()
    sandbox.install_landlock(landlock_ruleset(5, [SCRATCH]))
    assert libc.calls[0][1] == 16


def test_a_refused_rule_fails_before_restricting(fake):
    libc, closed = fake(fail=sandbox._SYS_ADD_RULE)
    with pytest.raises(OSError):
        sandbox.install_landlock(landlock_ruleset(7, [SCRATCH]))
    assert all(c[0] != "restrict" for c in libc.calls)
    assert 99 in closed


def test_a_missing_writable_dir_fails(fake):
    libc, _ = fake()
    with pytest.raises(OSError):
        sandbox.install_landlock(landlock_ruleset(7, ["/data/tmp/gone"]))
    assert all(c[0] != "restrict" for c in libc.calls)


def test_path_beneath_attr_is_packed():
    assert ctypes.sizeof(sandbox._PathBeneathAttr) == 12
    assert ctypes.sizeof(sandbox._RulesetAttr) == 24


def test_tmp_only_takes_and_drops_sockets(fake, monkeypatch):
    """LibreOffice's OSL pipe lives in /tmp; nothing else there is readable or writable."""
    libc, _ = fake()
    real = sandbox._open_path
    monkeypatch.setattr(sandbox, "_open_path", lambda p: (14, True) if p == "/tmp" else real(p))
    sandbox.install_landlock(landlock_ruleset(7, [SCRATCH]))
    rules = {c[1]: c[2] for c in libc.calls if c[0] == "rule"}
    assert rules[14] == sandbox.FS_MAKE_SOCK | sandbox.FS_REMOVE_FILE
