"""`_scrub_session_snapshot` / `_safe_symlink_target` — dropping unsafe symlinks
from a snapshot tar so a resume's hydrate validator doesn't reject it.

Reproduces the real failure: a cloned repo with a committed
`.fvm/flutter_sdk -> /Users/.../fvm/...` absolute symlink lands in the snapshot,
and on resume the SDK rejects the absolute target. The scrub must drop it (and
escaping relative links) while keeping regular files and safe relative links.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

from omni_code.serve_cli import _safe_symlink_target, _scrub_session_snapshot


def _add_file(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _add_symlink(tar: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def _members(path: Path) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(path, "r") as t:
        return {m.name: m for m in t.getmembers()}


def test_safe_symlink_target():
    # Absolute target → rejected (the .fvm case).
    assert _safe_symlink_target("omniagents-2/examples/agentdeck/.fvm/flutter_sdk", "/Users/x/fvm/3.32.8") is False
    # Escaping relative target → rejected.
    assert _safe_symlink_target("a/b/link", "../../../etc/passwd") is False
    # Safe relative target (stays inside root) → kept.
    assert _safe_symlink_target("a/b/link", "../b/file") is True
    assert _safe_symlink_target("a/link", "file") is True


def test_scrub_drops_unsafe_symlinks_keeps_the_rest(tmp_path: Path):
    snap = tmp_path / "sess.tar"
    with tarfile.open(snap, "w") as t:
        _add_file(t, "omniagents-2/README.md", b"hi")
        _add_symlink(t, "omniagents-2/examples/agentdeck/.fvm/flutter_sdk", "/Users/ericmartinez/fvm/versions/3.32.8")
        _add_symlink(t, "omniagents-2/escape", "../../../etc/passwd")
        _add_symlink(t, "omniagents-2/ok", "README.md")

    session = SimpleNamespace(state=SimpleNamespace(snapshot=SimpleNamespace(base_path=str(tmp_path), id="sess")))
    _scrub_session_snapshot(session)

    names = _members(snap)
    assert "omniagents-2/examples/agentdeck/.fvm/flutter_sdk" not in names  # absolute → dropped
    assert "omniagents-2/escape" not in names  # escaping → dropped
    assert "omniagents-2/README.md" in names  # regular file kept
    assert "omniagents-2/ok" in names  # safe relative symlink kept
    # The scrubbed tar now passes the same predicate the SDK validator uses.
    for name, m in names.items():
        if m.issym():
            assert _safe_symlink_target(name, m.linkname)


def test_scrub_is_noop_without_snapshot():
    # NoopSnapshot / unrecognized shape → must not raise.
    _scrub_session_snapshot(SimpleNamespace(state=SimpleNamespace(snapshot=None)))
    _scrub_session_snapshot(SimpleNamespace(state=None))


def test_scrub_noop_when_tar_missing(tmp_path: Path):
    session = SimpleNamespace(state=SimpleNamespace(snapshot=SimpleNamespace(base_path=str(tmp_path), id="absent")))
    _scrub_session_snapshot(session)  # no sess tar on disk → no error
    assert not (tmp_path / "absent.tar").exists()
