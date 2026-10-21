"""The cross-substrate ``FileEntry`` contract (tkt_z8T1amZ-GUs0).

``Workspace.list_dir`` is the one place where "the same call means the
same thing on every backend" used to be false: ``SandboxWorkspace`` put
the SDK's absolute container path in ``name`` *and* ``path``, reported
``is_dir=True`` for every entry (the SDK's ``is_dir`` is a *method*, so
``bool(getattr(...))`` was always truthy), never set ``is_symlink``, and
raised the container's own ``ls`` failure instead of an ``OSError``.

These tests pin the contract on both directly-testable backends: the host
filesystem, and a sandbox session double shaped exactly like the SDK's
(``path``-only entries, ``kind`` enum, ``is_dir`` as a bound method).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional

import sys

import pytest

from omniagents.workspace._entries import entry_basename, make_entry, sort_entries
from omniagents.workspace.host import HostWorkspace
from omniagents.workspace.sandbox import SandboxWorkspace
from omniagents.workspace.types import WorkspaceEscapeError

# ---------------------------------------------------------------------------
#  Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a.txt", "a.txt"),
        ("sub/a.txt", "a.txt"),
        ("/workspace/sub/a.txt", "a.txt"),
        ("/workspace/sub/", "sub"),
        ("", ""),
        ("/", ""),
        # A backslash is a legal character *inside* a POSIX filename, not a
        # separator. Treating it as one would rename this entry to "b.txt"
        # and hand callers a path pointing at nothing.
        ("a\\b.txt", "a\\b.txt"),
        ("sub/a\\b.txt", "a\\b.txt"),
    ],
)
def test_entry_basename_collapses_every_spelling(raw: str, expected: str) -> None:
    assert entry_basename(raw) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/a.txt",
        "../a.txt",
        "sub/../../a.txt",
        "",
    ],
)
def test_make_entry_refuses_paths_that_break_the_contract(path: str) -> None:
    """Only *structural* invariants — the ones that decide whether the path
    still addresses something inside the workspace."""
    with pytest.raises(ValueError):
        make_entry(name="a.txt", path=path, is_dir=False, size=1)


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("a\\b.txt", "sub/a\\b.txt"),
        ("C:", "C:"),
        ("E:migration.sql", "sub/E:migration.sql"),
        ("\\\\server", "\\\\server"),
    ],
)
def test_make_entry_keeps_windows_shaped_posix_filenames(name: str, path: str) -> None:
    """These are ordinary, legal POSIX filenames — an agent can create them.

    Rejecting the *spelling* here would delete them from listings on some
    substrates and not others, which is the very split this module exists
    to eliminate. Drive letters, UNC and backslashes are refused at the
    wire boundaries that need them (``canonical_relpath``,
    ``canonical_repo_path``), where the cost is a typed error to one
    caller rather than a hole in everybody's directory listing.
    """
    entry = make_entry(name=name, path=path, is_dir=False, size=1)
    assert entry.name == name
    assert entry.path == path


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX filenames/symlinks and a POSIX probe shell"
)
async def test_host_lists_windows_shaped_filenames(
    host_ws: HostWorkspace, tmp_path: Path
) -> None:
    """The end-to-end version of the above: these files must not vanish."""
    for name in ("C:", "E:migration.sql", "a.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert [e.name for e in await host_ws.list_dir(".")] == [
        "C:",
        "E:migration.sql",
        "a.txt",
    ]


@pytest.mark.asyncio
async def test_host_logs_every_entry_it_drops(
    host_ws: HostWorkspace, monkeypatch, caplog
) -> None:
    """A short listing must never be indistinguishable from a full one."""
    from omniagents.workspace import host as host_module

    await host_ws.write_text("keep.txt", "x")
    await host_ws.write_text("drop.txt", "x")

    real = host_module.make_entry

    def _explode(**kwargs):
        if kwargs.get("name") == "drop.txt":
            raise ValueError("synthetic contract violation")
        return real(**kwargs)

    monkeypatch.setattr(host_module, "make_entry", _explode)
    with caplog.at_level("WARNING"):
        entries = await host_ws.list_dir(".")
    assert [e.name for e in entries] == ["keep.txt"]
    assert "dropped an entry" in caplog.text
    assert "drop.txt" not in [e.name for e in entries]


def test_make_entry_allows_absolute_only_when_asked() -> None:
    entry = make_entry(
        name="a.txt", path="/elsewhere/a.txt", is_dir=False, allow_absolute=True
    )
    assert entry.path == "/elsewhere/a.txt"
    assert entry.name == "a.txt"


def test_make_entry_normalizes_and_drops_directory_size() -> None:
    entry = make_entry(name="sub", path="./nested/./sub", is_dir=True, size=4096)
    assert entry.path == "nested/sub"
    assert entry.size is None


def test_make_entry_rejects_a_name_that_contradicts_its_path() -> None:
    with pytest.raises(ValueError):
        make_entry(name="a.txt", path="sub/b.txt", is_dir=False)


def test_sort_entries_orders_by_the_requested_key() -> None:
    entries = [
        make_entry(name="b", path="b", is_dir=False, size=1),
        make_entry(name="a", path="a", is_dir=False, size=9),
    ]
    assert [e.name for e in sort_entries(entries, "name")] == ["a", "b"]
    assert [e.name for e in sort_entries(entries, "size")] == ["a", "b"]


# ---------------------------------------------------------------------------
#  Host backend
# ---------------------------------------------------------------------------


@pytest.fixture
def host_ws(tmp_path: Path) -> HostWorkspace:
    return HostWorkspace(root=str(tmp_path))


@pytest.mark.asyncio
async def test_host_entry_paths_are_workspace_relative(
    host_ws: HostWorkspace, tmp_path: Path
) -> None:
    await host_ws.write_text("sub/a.txt", "hello")
    entries = await host_ws.list_dir("sub")
    assert [(e.name, e.path) for e in entries] == [("a.txt", "sub/a.txt")]
    # The contract's real promise: path round-trips through resolve().
    assert host_ws.resolve(entries[0].path) == str(tmp_path / "sub" / "a.txt")
    assert entries[0].size == 5


@pytest.mark.asyncio
async def test_host_directory_entry_has_no_size(host_ws: HostWorkspace) -> None:
    await host_ws.mkdir("sub")
    entry = next(e for e in await host_ws.list_dir(".") if e.name == "sub")
    assert entry.is_dir is True
    assert entry.size is None


@pytest.mark.asyncio
async def test_host_symlink_to_directory_is_a_symlink_not_a_directory(
    host_ws: HostWorkspace, tmp_path: Path
) -> None:
    """``is_dir`` describes the entry, never its target.

    Pyodide (``FS.lstat``) and the fs_* host scanner
    (``follow_symlinks=False``) always did this; the host workspace was
    the outlier.
    """
    await host_ws.mkdir("real")
    try:
        os.symlink(tmp_path / "real", tmp_path / "link")
    except OSError:  # pragma: no cover - platforms without symlink perms
        pytest.skip("symlinks unavailable")
    entry = next(e for e in await host_ws.list_dir(".") if e.name == "link")
    assert entry.is_symlink is True
    assert entry.is_dir is False


@pytest.mark.asyncio
async def test_host_dangling_symlink_still_lists(
    host_ws: HostWorkspace, tmp_path: Path
) -> None:
    try:
        os.symlink(tmp_path / "gone", tmp_path / "dangling")
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")
    names = [e.name for e in await host_ws.list_dir(".")]
    assert "dangling" in names


@pytest.mark.asyncio
async def test_host_list_dir_raises_os_errors(
    host_ws: HostWorkspace, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        await host_ws.list_dir("nope")
    await host_ws.write_text("a.txt", "x")
    with pytest.raises(NotADirectoryError):
        await host_ws.list_dir("a.txt")
    # An existing, empty directory is still an empty list — callers can
    # tell "nothing here" from "no such directory".
    await host_ws.mkdir("empty")
    assert await host_ws.list_dir("empty") == []


@pytest.mark.asyncio
async def test_unconfined_host_may_report_absolute_paths(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x", encoding="utf-8")
    workspace = HostWorkspace(root=str(tmp_path / "root"))
    entries = await workspace.list_dir(str(outside))
    assert [(e.name, e.path) for e in entries] == [
        ("x.txt", (outside / "x.txt").as_posix())
    ]


# ---------------------------------------------------------------------------
#  Sandbox backend
# ---------------------------------------------------------------------------


class _Kind(str, Enum):
    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True)
class _SdkEntry:
    """Shaped exactly like ``agents.sandbox.files.FileEntry``.

    No ``name`` field, no ``mtime``, no ``is_symlink`` — and ``is_dir`` is
    a *method*, which is what made the old translation report every entry
    as a directory.
    """

    path: str
    size: int
    kind: _Kind = _Kind.FILE

    def is_dir(self) -> bool:
        return self.kind == _Kind.DIRECTORY


class _ExecResult:
    def __init__(self, exit_code: int = 0, stdout: bytes = b"") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = b""


class _FakeSession:
    """Sandbox session double whose ``ls`` mimics ``ls -la`` semantics."""

    def __init__(
        self,
        entries: Optional[List[_SdkEntry]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self._entries = entries or []
        self._error = error
        self.exec_calls: List[str] = []

    async def ls(self, path: Any, *, user: Any = None) -> List[_SdkEntry]:
        if self._error is not None:
            raise self._error
        return list(self._entries)

    async def exec(self, *args: Any, **kwargs: Any) -> _ExecResult:
        self.exec_calls.append(args[-1] if args else "")
        return _ExecResult()


def _sandbox(session: _FakeSession) -> SandboxWorkspace:
    return SandboxWorkspace(session=session, root="/workspace")


@pytest.mark.asyncio
async def test_sandbox_entries_are_relative_typed_and_symlink_aware() -> None:
    session = _FakeSession(
        [
            _SdkEntry("/workspace/sub/a.txt", 6, _Kind.FILE),
            _SdkEntry("/workspace/sub/inner", 4096, _Kind.DIRECTORY),
            _SdkEntry("/workspace/sub/link", 4, _Kind.SYMLINK),
            _SdkEntry("/workspace/sub/pipe", 0, _Kind.OTHER),
        ]
    )
    entries = await _sandbox(session).list_dir("sub")
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"a.txt", "inner", "link", "pipe"}
    assert by_name["a.txt"].path == "sub/a.txt"
    assert by_name["a.txt"].is_dir is False
    assert by_name["a.txt"].size == 6
    assert by_name["inner"].is_dir is True
    assert by_name["inner"].size is None
    assert by_name["link"].is_symlink is True
    assert by_name["link"].is_dir is False
    assert by_name["pipe"].is_dir is False


@pytest.mark.asyncio
async def test_sandbox_hidden_filter_matches_on_the_basename() -> None:
    """The old code tested ``"/workspace/.dot".startswith(".")`` — never true."""
    session = _FakeSession(
        [
            _SdkEntry("/workspace/.dot", 7),
            _SdkEntry("/workspace/a.txt", 6),
        ]
    )
    workspace = _sandbox(session)
    assert [e.name for e in await workspace.list_dir(".")] == ["a.txt"]
    assert [e.name for e in await workspace.list_dir(".", include_hidden=True)] == [
        ".dot",
        "a.txt",
    ]


@pytest.mark.asyncio
async def test_sandbox_listing_a_file_raises_not_a_directory() -> None:
    """``ls -la`` on a file echoes the argument back as the only entry."""
    session = _FakeSession([_SdkEntry("/workspace/a.txt", 6)])
    with pytest.raises(NotADirectoryError):
        await _sandbox(session).list_dir("a.txt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("ls: /workspace/nope: No such file or directory", FileNotFoundError),
        ("ls: /workspace/x: Not a directory", NotADirectoryError),
        ("ls: /workspace/x: Permission denied", PermissionError),
        ("the container fell over", OSError),
    ],
)
async def test_sandbox_transport_errors_become_os_errors(
    stderr: str, expected: type
) -> None:
    class _ExecNonZero(Exception):
        def __init__(self, text: str) -> None:
            super().__init__("command failed")
            self.result = _ExecResult(1, text.encode())

    session = _FakeSession(error=_ExecNonZero(stderr))
    with pytest.raises(expected) as excinfo:
        await _sandbox(session).list_dir("x")
    # The substrate failure is preserved rather than swallowed.
    assert isinstance(excinfo.value.__cause__, _ExecNonZero)


@pytest.mark.asyncio
async def test_sandbox_path_policy_rejection_becomes_workspace_escape() -> None:
    class _InvalidManifestPathError(Exception):
        pass

    session = _FakeSession(
        error=_InvalidManifestPathError(
            "manifest path must be relative: /workspace/link"
        )
    )
    with pytest.raises(WorkspaceEscapeError):
        await _sandbox(session).list_dir("link")


@pytest.mark.asyncio
async def test_sandbox_one_unspellable_entry_does_not_abort_the_listing() -> None:
    session = _FakeSession(
        [
            _SdkEntry("/workspace/good.txt", 1),
            _SdkEntry("/elsewhere/bad.txt", 1),
            _SdkEntry("/workspace/also-good.txt", 2),
        ]
    )
    entries = await _sandbox(session).list_dir(".")
    assert [e.name for e in entries] == ["also-good.txt", "good.txt"]


@pytest.mark.asyncio
async def test_sandbox_read_error_is_translated_too() -> None:
    class _Session(_FakeSession):
        async def read(self, path: Any, *, user: Any = None) -> Any:
            raise RuntimeError("manifest path must not escape root: /etc/passwd")

    with pytest.raises(WorkspaceEscapeError):
        await _sandbox(_Session()).read_bytes("link/passwd")


# ---------------------------------------------------------------------------
#  Sandbox path confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../etc/passwd",
        "/etc/passwd",
        "sub/../../etc",
        "/workspace/../etc/passwd",
        "..",
        "sub/../..",
    ],
)
def test_sandbox_resolve_refuses_escapes(path: str) -> None:
    with pytest.raises(WorkspaceEscapeError):
        _sandbox(_FakeSession()).resolve(path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".", "/workspace"),
        ("", "/workspace"),
        ("a.txt", "/workspace/a.txt"),
        ("sub/../a.txt", "/workspace/a.txt"),
        ("/workspace/sub/a.txt", "/workspace/sub/a.txt"),
        ("/workspace", "/workspace"),
    ],
)
def test_sandbox_resolve_accepts_paths_inside_the_root(
    path: str, expected: str
) -> None:
    assert _sandbox(_FakeSession()).resolve(path) == expected


def test_sandbox_root_prefix_is_not_a_sibling_match() -> None:
    """``/workspace-evil`` must not pass as being under ``/workspace``."""
    with pytest.raises(WorkspaceEscapeError):
        _sandbox(_FakeSession()).resolve("/workspace-evil/x")


@pytest.mark.asyncio
async def test_sandbox_predicates_refuse_escapes_without_raising() -> None:
    workspace = _sandbox(_FakeSession())
    assert await workspace.exists("../etc/passwd") is False
    assert await workspace.is_file("/etc/passwd") is False
    assert await workspace.is_dir("../..") is False


@pytest.mark.asyncio
async def test_sandbox_predicates_confine_symlink_targets_in_container() -> None:
    """The shell probe realpaths the argument before answering.

    A bare ``test -e`` follows symlinks silently, so a link to ``/etc``
    would answer True in the container and False on the host for the
    identical call.
    """
    session = _FakeSession()
    workspace = _sandbox(session)
    await workspace.is_file("link")
    script = session.exec_calls[-1]
    assert "readlink -f" in script
    assert "/workspace/link" in script


# ---------------------------------------------------------------------------
#  The confinement probe, run through a real shell (tkt_k5Yk_JBkrIw6)
# ---------------------------------------------------------------------------
#
# Asserting the probe *script contains* "readlink -f" does not prove the
# probe refuses anything: the script is what decides, and only running it
# can say what it decides. Both properties below used to hold in exactly
# one place — ``test_fs_sandbox_substrate.py``, which needs Docker and
# never ran in CI. The script is ordinary POSIX shell, so a session double
# that executes it with the host's ``/bin/sh`` proves them here too, with
# no container involved.


class _ShellSession(_FakeSession):
    """Session double that really runs the probe script under ``/bin/sh``.

    *root* is a host directory standing in for the container's workspace
    mount, so ``resolve()``'s output addresses a real path and the script's
    ``readlink``/``test`` see a real tree.
    """

    def __init__(self, root: str, *, path_prefix: Optional[str] = None) -> None:
        super().__init__()
        self._root = root
        self._path_prefix = path_prefix

    async def exec(self, *args: Any, **kwargs: Any) -> _ExecResult:
        import subprocess

        script = args[-1]
        self.exec_calls.append(script)
        env = dict(os.environ)
        if self._path_prefix:
            env["PATH"] = f"{self._path_prefix}:{env['PATH']}"
        completed = subprocess.run(
            ["/bin/sh", "-lc", script],
            capture_output=True,
            check=False,
            env=env,
        )
        return _ExecResult(completed.returncode, completed.stdout)


def _shell_workspace(root: Path, **kwargs: Any) -> SandboxWorkspace:
    return SandboxWorkspace(session=_ShellSession(str(root), **kwargs), root=str(root))


def _probe_tree(root: Path, outside: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_text("inside", encoding="utf-8")
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, root / "escaping_file")
    os.symlink(outside.parent, root / "escaping_dir")


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX filenames/symlinks and a POSIX probe shell"
)
async def test_sandbox_probe_refuses_a_symlink_out_of_the_root(tmp_path: Path) -> None:
    """``test -f`` follows symlinks; the ``case`` guard is what refuses.

    Without it the probe answers for a file the workspace does not
    contain — the same ``True`` the host backend answers ``False`` to.
    """
    root = tmp_path / "workspace"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    try:
        _probe_tree(root, outside_dir / "secret.txt")
    except OSError:  # pragma: no cover - platforms without symlink perms
        pytest.skip("symlinks unavailable")
    workspace = _shell_workspace(root)

    assert await workspace.is_file("a.txt") is True
    assert await workspace.is_file("escaping_file") is False
    assert await workspace.exists("escaping_file") is False
    assert await workspace.is_dir("escaping_dir") is False


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX filenames/symlinks and a POSIX probe shell"
)
async def test_sandbox_probe_fails_closed_when_readlink_is_unavailable(
    tmp_path: Path,
) -> None:
    """No ``readlink`` means no answer — never a lexical fallback.

    A symlink's *lexical* path is inside the root by construction, so
    falling back to it would hand ``test -f`` the link and let it answer
    for the target outside the workspace. Refusing everything (including
    the legitimate ``a.txt``) is the correct failure mode; the cost is a
    false negative on an image with no ``readlink``.
    """
    root = tmp_path / "workspace"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    try:
        _probe_tree(root, outside_dir / "secret.txt")
    except OSError:  # pragma: no cover
        pytest.skip("symlinks unavailable")

    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "readlink").write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    (shim / "readlink").chmod(0o755)
    workspace = _shell_workspace(root, path_prefix=str(shim))

    assert await workspace.is_file("escaping_file") is False
    assert await workspace.exists("escaping_file") is False
    assert await workspace.is_dir("escaping_dir") is False
    assert await workspace.is_file("a.txt") is False
    # And a working readlink restores the ordinary answers, proving the
    # shim — not some unrelated breakage — caused the refusals above.
    assert await _shell_workspace(root).is_file("a.txt") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("invalid_manifest_path", WorkspaceEscapeError),
        ("workspace_read_not_found", FileNotFoundError),
        ("workspace_root_not_found", FileNotFoundError),
        ("exec_transport_error", OSError),
    ],
)
async def test_sandbox_maps_the_sdk_machine_readable_error_code(
    code: str, expected: type
) -> None:
    """The SDK's ``error_code`` is the stable signal; prose is the fallback."""

    class _SandboxError(Exception):
        def __init__(self, code: str) -> None:
            super().__init__("the sandbox refused the operation")
            self.error_code = code

    session = _FakeSession(error=_SandboxError(code))
    with pytest.raises(expected):
        await _sandbox(session).list_dir("x")


class _LegacySdkEntry:
    """A session entry with no ``kind`` and a *callable* ``is_dir``.

    This is the exact shape that produced the original defect:
    ``bool(getattr(entry, "is_dir", False))`` evaluates a bound method,
    which is always truthy, so every file reported as a directory.
    """

    def __init__(self, path: str, size: int, directory: bool) -> None:
        self.path = path
        self.size = size
        self._directory = directory

    def is_dir(self) -> bool:
        return self._directory

    def is_symlink(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_sandbox_calls_a_callable_is_dir_instead_of_trusting_truthiness() -> None:
    session = _FakeSession(
        [
            _LegacySdkEntry("/workspace/a.txt", 6, directory=False),
            _LegacySdkEntry("/workspace/sub", 4096, directory=True),
        ]
    )
    entries = {e.name: e for e in await _sandbox(session).list_dir(".")}
    assert entries["a.txt"].is_dir is False
    assert entries["a.txt"].size == 6
    assert entries["sub"].is_dir is True
    assert entries["sub"].size is None


@pytest.mark.asyncio
async def test_sandbox_accepts_entries_reported_as_bare_names() -> None:
    """A duck-typed session reporting bare names anchors at the listed dir."""

    class _BareEntry:
        def __init__(self, path: str) -> None:
            self.path = path
            self.size = 1
            self.is_dir = False
            self.is_symlink = False

    session = _FakeSession([_BareEntry("a.txt")])
    entries = await _sandbox(session).list_dir("sub")
    assert [(e.name, e.path) for e in entries] == [("a.txt", "sub/a.txt")]


@pytest.mark.parametrize("root", ["/workspace", "/workspace/", "/workspace//"])
def test_sandbox_root_spelling_does_not_change_behavior(root: str) -> None:
    """An unnormalized root made ``resolve(".")`` and ``resolve("a")``
    disagree about the separator, and the two spellings then compare
    unequal as dict keys, in ``relpath`` and in the shell guard."""
    workspace = SandboxWorkspace(session=_FakeSession(), root=root)
    assert workspace.root == "/workspace"
    assert workspace.resolve(".") == "/workspace"
    assert workspace.resolve("a.txt") == "/workspace/a.txt"


@pytest.mark.asyncio
async def test_sandbox_entry_paths_are_relative_to_an_unnormalized_root() -> None:
    session = _FakeSession([_SdkEntry("/workspace/sub/a.txt", 6, _Kind.FILE)])
    entries = await SandboxWorkspace(session=session, root="/workspace/").list_dir(
        "sub"
    )
    assert [(e.name, e.path) for e in entries] == [("a.txt", "sub/a.txt")]


@pytest.mark.asyncio
async def test_sandbox_drops_entries_it_reported_twice(caplog) -> None:
    """The SDK's ``ls -la`` parse loses a filename's leading space.

    A directory holding both ``" lead.txt"`` and ``"lead.txt"`` comes back
    as two rows both named ``lead.txt`` with different sizes, and nothing
    in that output says which is which. Reporting either one hands callers
    a path that does not round-trip — ``fs_stat`` served the 20-byte
    padded file's size for the 4-byte real one — so both are dropped and
    logged.
    """
    session = _FakeSession(
        [
            _SdkEntry("/workspace/lead.txt", 20, _Kind.FILE),
            _SdkEntry("/workspace/lead.txt", 4, _Kind.FILE),
            _SdkEntry("/workspace/ok.txt", 1, _Kind.FILE),
        ]
    )
    with caplog.at_level("WARNING"):
        entries = await _sandbox(session).list_dir(".")
    assert [e.name for e in entries] == ["ok.txt"]
    assert "reported 'lead.txt' 2 times" in caplog.text


@pytest.mark.asyncio
async def test_sandbox_keeps_distinct_entries_untouched() -> None:
    """The de-duplication must not touch an ordinary listing."""
    session = _FakeSession(
        [
            _SdkEntry("/workspace/a.txt", 1, _Kind.FILE),
            _SdkEntry("/workspace/b.txt", 2, _Kind.FILE),
        ]
    )
    entries = await _sandbox(session).list_dir(".")
    assert [(e.name, e.size) for e in entries] == [("a.txt", 1), ("b.txt", 2)]
