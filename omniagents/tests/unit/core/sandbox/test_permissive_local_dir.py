"""Tests for ``PermissiveLocalDir``.

Exercises the host-side walk (gitignore, symlink handling) against
real temp dirs; session calls are mocked, so we capture and inspect
the tar bytes that would be shipped to the container.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "UnixLocalSandbox is POSIX-only; Windows hosts use docker/wasm sandboxes or host mode",
        allow_module_level=True,
    )

import asyncio
import io
import os
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agents.sandbox.entries.base import BaseEntry

from omniagents.core.sandbox.permissive_local_dir import (
    _DEFAULT_MAX_SEED_BYTES,
    _build_dir_tar,
    _resolve_max_seed_bytes,
    PermissiveLocalDir,
    PermissiveLocalDirError,
)

# --- helpers ---------------------------------------------------------- #


def _make_session() -> MagicMock:
    session = MagicMock()
    session.mkdir = AsyncMock(return_value=None)
    session.write = AsyncMock(return_value=None)

    ok_result = MagicMock()
    ok_result.ok.return_value = True
    ok_result.stderr = b""
    session.exec = AsyncMock(return_value=ok_result)
    session._exec_checked_nonzero = AsyncMock(return_value=ok_result)
    return session


def _apply(
    entry: PermissiveLocalDir, session: Any, dest: Path = Path("/workspace")
) -> None:
    asyncio.run(entry.apply(session=session, dest=dest, base_dir=Path("/")))


def _file_names(tar: tarfile.TarFile) -> set[str]:
    return {m.name for m in tar.getmembers() if m.isfile() or m.issym()}


def _tar_from_write_call(session: MagicMock) -> tarfile.TarFile:
    """Open the in-memory tar that ``apply`` shipped via ``session.write``."""
    call = session.write.call_args
    body = call.args[1] if len(call.args) > 1 else call.kwargs["body"]
    if hasattr(body, "read"):
        body.seek(0)
        data = body.read()
    else:
        data = body
    return tarfile.open(fileobj=io.BytesIO(data), mode="r")


# --- registration ----------------------------------------------------- #


def test_subclass_registered_under_type_string():
    parsed = BaseEntry.parse({"type": "permissive_local_dir", "src": "/tmp/x"})
    assert isinstance(parsed, PermissiveLocalDir)
    assert parsed.src == Path("/tmp/x")


# --- happy path ------------------------------------------------------- #


def test_plain_dir_files_round_trip(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.txt").write_text("alpha\n")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("beta\n")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    assert _file_names(tar) == {"a.txt", "sub/b.txt"}


# --- gitignore semantics --------------------------------------------- #


def test_root_gitignore_excludes_files(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / ".gitignore").write_text("*.log\nbuild/\n")
    (src / "a.txt").write_text("alpha\n")
    (src / "app.log").write_text("noise\n")
    (src / "build").mkdir()
    (src / "build" / "out.bin").write_text("noise\n")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    names = _file_names(tar)
    assert "a.txt" in names
    assert ".gitignore" in names  # the file itself isn't ignored
    assert "app.log" not in names
    assert not any(n.startswith("build/") for n in names)


def test_nested_gitignore_is_scoped_to_its_subtree(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    # Nested .gitignore: only affects files under sub/
    (src / "a.log").write_text("kept\n")
    (src / "sub").mkdir()
    (src / "sub" / ".gitignore").write_text("*.log\n")
    (src / "sub" / "b.log").write_text("dropped\n")
    (src / "sub" / "b.txt").write_text("kept\n")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    names = _file_names(tar)
    assert "a.log" in names  # root has no .gitignore — log is kept
    assert "sub/b.log" not in names  # nested .gitignore drops it
    assert "sub/b.txt" in names


def test_anchored_pattern_with_leading_slash(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / ".gitignore").write_text("/build\n")  # anchored to root only
    (src / "build").mkdir()
    (src / "build" / "a.txt").write_text("dropped\n")
    (src / "sub").mkdir()
    (src / "sub" / "build").mkdir()
    (src / "sub" / "build" / "b.txt").write_text("kept\n")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    names = _file_names(tar)
    assert "build/a.txt" not in names
    assert "sub/build/b.txt" in names


# --- symlinks --------------------------------------------------------- #


def test_file_symlink_silently_skipped(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "real.txt").write_text("real\n")
    os.symlink("real.txt", src / "link.txt")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    names = _file_names(tar)
    assert names == {"real.txt"}


def test_dir_symlink_silently_skipped(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "real").mkdir()
    (src / "real" / "x.txt").write_text("x\n")
    os.symlink("real", src / "alias", target_is_directory=True)
    (src / "keep.txt").write_text("k\n")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    names = _file_names(tar)
    assert names == {"real/x.txt", "keep.txt"}


def test_dangling_symlink_silently_skipped(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "keep.txt").write_text("k\n")
    os.symlink("nope-does-not-exist", src / "dangling")

    session = _make_session()
    _apply(PermissiveLocalDir(src=src), session)

    tar = _tar_from_write_call(session)
    names = _file_names(tar)
    assert names == {"keep.txt"}


# --- errors ----------------------------------------------------------- #


def test_missing_src_raises(tmp_path: Path):
    src = tmp_path / "does-not-exist"
    session = _make_session()
    with pytest.raises(PermissiveLocalDirError):
        _apply(PermissiveLocalDir(src=src), session)


def test_src_is_file_raises(tmp_path: Path):
    src = tmp_path / "afile.txt"
    src.write_text("x")
    session = _make_session()
    with pytest.raises(PermissiveLocalDirError):
        _apply(PermissiveLocalDir(src=src), session)


def test_tar_extract_failure_raises(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.txt").write_text("a\n")

    session = _make_session()
    bad_result = MagicMock()
    bad_result.ok.return_value = False
    bad_result.stderr = b"tar: broken"
    session.exec = AsyncMock(return_value=bad_result)

    with pytest.raises(PermissiveLocalDirError) as exc:
        _apply(PermissiveLocalDir(src=src), session)
    assert "tar extract failed" in str(exc.value)


# --- unix_local short-circuit ---------------------------------------- #


def test_unix_local_session_is_pure_noop(tmp_path: Path):
    """Under unix_local, apply() must NEVER call into the session.

    The agent operates directly on the host filesystem, so any host write
    (mkdir, write, exec, metadata) is a relocation we don't want. Source
    descriptors flow through the manifest as inert records.
    """
    from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxSession

    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.txt").write_text("a\n")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("b\n")

    session = MagicMock(spec=UnixLocalSandboxSession)
    session.mkdir = AsyncMock(return_value=None)
    session.write = AsyncMock(return_value=None)
    session.exec = AsyncMock(return_value=MagicMock(ok=lambda: True, stderr=b""))

    # dest is completely unrelated to src — would normally tar+extract.
    dest = tmp_path / "unrelated_dest"
    asyncio.run(
        PermissiveLocalDir(src=src).apply(
            session=session, dest=dest, base_dir=Path("/")
        )
    )

    session.mkdir.assert_not_awaited()
    session.write.assert_not_awaited()
    session.exec.assert_not_awaited()
    # And no directory was created on the host.
    assert not dest.exists()


# --- seed-size cap ---------------------------------------------------- #


def test_seed_cap_aborts_when_exceeded(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(3):
        (src / f"f{i}.bin").write_bytes(b"x" * 1000)  # 3000 bytes total

    with pytest.raises(PermissiveLocalDirError) as ei:
        _build_dir_tar(src, max_bytes=2000)
    assert "seed limit" in str(ei.value)


def test_seed_under_cap_builds_full_tar(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.bin").write_bytes(b"x" * 1000)
    (src / "b.bin").write_bytes(b"x" * 1000)

    tar = tarfile.open(
        fileobj=io.BytesIO(_build_dir_tar(src, max_bytes=10_000)), mode="r"
    )
    assert _file_names(tar) == {"a.bin", "b.bin"}


def test_gitignored_files_dont_count_toward_cap(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / ".gitignore").write_text("big.bin\n")
    (src / "big.bin").write_bytes(b"x" * 100_000)  # excluded → must not count
    (src / "keep.txt").write_bytes(b"x" * 100)

    # Cap below big.bin but above the kept content: succeeds because the
    # size check runs after the gitignore filter (big.bin never counts).
    tar = tarfile.open(
        fileobj=io.BytesIO(_build_dir_tar(src, max_bytes=1000)), mode="r"
    )
    assert _file_names(tar) == {".gitignore", "keep.txt"}


def test_cap_disabled_with_none(tmp_path: Path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "big.bin").write_bytes(b"x" * 50_000)
    # max_bytes=None at the build level means no cap.
    tar = tarfile.open(
        fileobj=io.BytesIO(_build_dir_tar(src, max_bytes=None)), mode="r"
    )
    assert _file_names(tar) == {"big.bin"}


def test_resolve_max_seed_bytes(monkeypatch):
    monkeypatch.delenv("OMNI_MAX_SEED_BYTES", raising=False)
    # Unset → default.
    assert _resolve_max_seed_bytes(None) == _DEFAULT_MAX_SEED_BYTES
    # Explicit value wins; <=0 means unlimited (None).
    assert _resolve_max_seed_bytes(123) == 123
    assert _resolve_max_seed_bytes(0) is None
    assert _resolve_max_seed_bytes(-5) is None
    # Env override (only consulted when explicit is None).
    monkeypatch.setenv("OMNI_MAX_SEED_BYTES", "4096")
    assert _resolve_max_seed_bytes(None) == 4096
    monkeypatch.setenv("OMNI_MAX_SEED_BYTES", "0")
    assert _resolve_max_seed_bytes(None) is None
