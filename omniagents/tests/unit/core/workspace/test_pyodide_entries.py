"""``PyodideWorkspace.list_dir`` against a fake worker (tkt_z8T1amZ-GUs0).

This backend had **no** tests anywhere in the repo, which is why it was
the one place the shared ``FileEntry`` contract went unverified. A live
run needs Node plus a pyodide build, but nothing about ``list_dir``
does: it talks to ``self._worker.call(op, params)``, so a two-method
double covers the whole translation — the same trick the SDK session
tests use.
"""

from __future__ import annotations

import errno
from typing import Any, Dict, List, Optional

import pytest

from omniagents.workspace.pyodide import PyodideWorkspace, PyodideWorkerError
from omniagents.workspace.types import WorkspaceEscapeError


class _FakeWorker:
    """Stands in for ``_PyodideWorker``: ``start`` plus ``call``."""

    def __init__(
        self,
        entries: Optional[List[Dict[str, Any]]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self._entries = entries or []
        self._error = error
        self.calls: List[tuple] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def call(self, op: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append((op, params))
        if self._error is not None:
            raise self._error
        return {"entries": list(self._entries)}


def _entry(
    name: str,
    *,
    is_dir: bool = False,
    is_file: bool = True,
    size: int = 0,
    mtime: float = 0.0,
    is_symlink: bool = False,
) -> Dict[str, Any]:
    """One ``fs_readdir`` row, shaped like the worker's ``statToObj``."""
    return {
        "name": name,
        "size": size,
        "mtime": mtime,
        "is_dir": is_dir,
        "is_file": is_file,
        "is_symlink": is_symlink,
    }


def _workspace(worker: _FakeWorker) -> PyodideWorkspace:
    return PyodideWorkspace(worker=worker)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_entries_are_relative_typed_and_sized() -> None:
    worker = _FakeWorker(
        [
            _entry("a.txt", size=6, mtime=1700000000.0),
            _entry("inner", is_dir=True, is_file=False, size=4096),
            _entry("link", is_file=False, is_symlink=True, size=4),
        ]
    )
    workspace = _workspace(worker)
    entries = {e.name: e for e in await workspace.list_dir("sub")}

    assert worker.calls[0] == ("fs_readdir", {"path": "/workspace/sub"})
    assert entries["a.txt"].path == "sub/a.txt"
    assert entries["a.txt"].is_dir is False
    assert entries["a.txt"].size == 6
    assert entries["a.txt"].mtime is not None
    assert entries["inner"].is_dir is True
    # A directory's size is a substrate artifact, never reported.
    assert entries["inner"].size is None
    assert entries["link"].is_symlink is True
    assert entries["link"].is_dir is False
    # entry.path round-trips through resolve() on this backend too.
    assert workspace.resolve(entries["a.txt"].path) == "/workspace/sub/a.txt"


@pytest.mark.asyncio
async def test_hidden_entries_are_filtered_unless_requested() -> None:
    worker = _FakeWorker([_entry(".dot"), _entry("a.txt")])
    workspace = _workspace(worker)
    assert [e.name for e in await workspace.list_dir(".")] == ["a.txt"]
    assert [e.name for e in await workspace.list_dir(".", include_hidden=True)] == [
        ".dot",
        "a.txt",
    ]


@pytest.mark.asyncio
async def test_entries_are_sorted_by_the_requested_key() -> None:
    worker = _FakeWorker(
        [_entry("b.txt", size=1), _entry("a.txt", size=9), _entry("c.txt", size=5)]
    )
    workspace = _workspace(worker)
    assert [e.name for e in await workspace.list_dir(".")] == [
        "a.txt",
        "b.txt",
        "c.txt",
    ]
    assert [e.name for e in await workspace.list_dir(".", sort_by="size")] == [
        "a.txt",
        "c.txt",
        "b.txt",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            PyodideWorkerError("ENOENT: no such file", errno=errno.ENOENT),
            FileNotFoundError,
        ),
        (PyodideWorkerError("boom", errno=errno.ENOTDIR), NotADirectoryError),
        (PyodideWorkerError("boom", errno=errno.EACCES), PermissionError),
        (PyodideWorkerError("boom", errno=errno.EPERM), PermissionError),
        (PyodideWorkerError("the worker fell over"), OSError),
    ],
)
async def test_worker_errors_become_os_errors(
    error: PyodideWorkerError, expected: type
) -> None:
    """A missing directory must not masquerade as an empty one.

    It used to return ``[]``, which made "no such directory"
    indistinguishable from "nothing here" — and left this the one backend
    that could not honor the ``PermissionError`` arm of the contract.
    """
    with pytest.raises(expected) as excinfo:
        await _workspace(_FakeWorker(error=error)).list_dir("x")
    assert excinfo.value.__cause__ is error


@pytest.mark.asyncio
async def test_one_unspellable_entry_does_not_abort_the_listing(caplog) -> None:
    """And the drop is logged — a short listing is never silent."""
    worker = _FakeWorker([_entry("good.txt"), _entry(""), _entry("also-good.txt")])
    with caplog.at_level("WARNING"):
        entries = await _workspace(worker).list_dir(".")
    assert [e.name for e in entries] == ["also-good.txt", "good.txt"]
    assert "dropped an entry" in caplog.text


@pytest.mark.asyncio
async def test_windows_shaped_filenames_are_listed_not_dropped() -> None:
    """``C:`` and ``E:migration.sql`` are legal POSIX filenames."""
    worker = _FakeWorker([_entry("C:"), _entry("E:migration.sql"), _entry("a.txt")])
    entries = await _workspace(worker).list_dir(".")
    assert [e.name for e in entries] == ["C:", "E:migration.sql", "a.txt"]
    assert [e.path for e in entries] == ["C:", "E:migration.sql", "a.txt"]


@pytest.mark.parametrize(
    "path", ["../etc/passwd", "/etc/passwd", "sub/../../etc", ".."]
)
def test_resolve_refuses_escapes(path: str) -> None:
    with pytest.raises(WorkspaceEscapeError):
        _workspace(_FakeWorker()).resolve(path)
