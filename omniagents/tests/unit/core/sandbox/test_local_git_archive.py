"""Tests for ``LocalGitArchive``.

These exercise the host-side ``git archive`` invocation and the
session interaction. Session calls are mocked; the host git ops run
against real temp repos so we get genuine git semantics (tracked vs
ignored vs untracked, symlink object handling, etc.).
"""

from __future__ import annotations

import asyncio
import io
import subprocess
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.sandbox.entries.base import BaseEntry
from omniagents.core.sandbox.local_git_archive import (
    LocalGitArchive,
    LocalGitArchiveError,
)


# --- fixtures --------------------------------------------------------- #


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "PATH": "/usr/bin:/bin",
        },
    )


@pytest.fixture
def repo_with_commit(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("alpha\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n")
    _git(repo, "add", "a.txt", "src/main.py")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _make_session() -> MagicMock:
    """A fake :class:`BaseSandboxSession` with the methods ``apply``
    actually calls. ``exec`` returns an ``ok()``-able result by default.
    """
    session = MagicMock()
    session.mkdir = AsyncMock(return_value=None)
    session.write = AsyncMock(return_value=None)

    ok_result = MagicMock()
    ok_result.ok.return_value = True
    ok_result.stderr = b""
    session.exec = AsyncMock(return_value=ok_result)
    # _apply_metadata (inherited from BaseEntry) calls this for the
    # final chmod on dest.
    session._exec_checked_nonzero = AsyncMock(return_value=ok_result)
    return session


def _apply(entry: LocalGitArchive, session: Any, dest: Path = Path("/workspace")) -> None:
    asyncio.run(entry.apply(session=session, dest=dest, base_dir=Path("/")))


def _file_names(tar: tarfile.TarFile) -> list[str]:
    """Names of file/symlink entries (not directory entries) in tar."""
    return [m.name for m in tar.getmembers() if m.isfile() or m.issym()]


# --- registration ----------------------------------------------------- #


def test_subclass_registered_under_type_string():
    parsed = BaseEntry.parse({"type": "local_git_archive", "src": "/tmp/x"})
    assert isinstance(parsed, LocalGitArchive)
    assert parsed.src == Path("/tmp/x")
    assert parsed.include_untracked is True


def test_include_untracked_round_trips_through_pydantic():
    parsed = BaseEntry.parse(
        {"type": "local_git_archive", "src": "/tmp/x", "include_untracked": False}
    )
    assert isinstance(parsed, LocalGitArchive)
    assert parsed.include_untracked is False


# --- happy path ------------------------------------------------------- #


def test_archive_contains_committed_files(repo_with_commit: Path):
    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    _apply(entry, session)

    # Inspect the tarball that was streamed to session.write
    write_call = session.write.await_args
    assert write_call is not None
    _dest, fileobj = write_call.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        names = set(_file_names(tar))
    assert "a.txt" in names
    assert "src/main.py" in names


def test_calls_session_in_expected_order(repo_with_commit: Path):
    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    _apply(entry, session)

    session.mkdir.assert_awaited_once()
    session.write.assert_awaited_once()
    # exec is called twice: tar xf, then rm -f cleanup
    assert session.exec.await_count == 2
    tar_call = session.exec.await_args_list[0]
    assert tar_call.args[0] == "tar"
    assert tar_call.args[1] == "xf"
    rm_call = session.exec.await_args_list[1]
    assert rm_call.args[0] == "rm"


# --- gitignore is respected (because git itself enforces it) ---------- #


def test_gitignored_files_not_in_archive(repo_with_commit: Path):
    (repo_with_commit / ".gitignore").write_text("ignored.txt\n")
    (repo_with_commit / "ignored.txt").write_text("secret\n")
    _git(repo_with_commit, "add", ".gitignore")
    _git(repo_with_commit, "commit", "-q", "-m", "add gitignore")

    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        names = set(_file_names(tar))
    assert "ignored.txt" not in names
    assert ".gitignore" in names


def test_untracked_files_included_by_default(repo_with_commit: Path):
    (repo_with_commit / "untracked.txt").write_text("hot off the editor\n")
    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        names = set(_file_names(tar))
    # include_untracked=True is the default — uncommitted-but-not-ignored
    # files appear in the archive so the agent sees what the user sees.
    assert "untracked.txt" in names


def test_untracked_files_excluded_when_opted_out(repo_with_commit: Path):
    (repo_with_commit / "untracked.txt").write_text("hot off the editor\n")
    entry = LocalGitArchive(src=repo_with_commit, include_untracked=False)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        names = set(_file_names(tar))
    assert "untracked.txt" not in names


def test_uncommitted_modifications_are_seen(repo_with_commit: Path):
    # ls-files reads the working tree; uncommitted changes are visible.
    # This is the deliberate departure from `git archive HEAD` semantics
    # — see module docstring.
    (repo_with_commit / "a.txt").write_text("OVERWRITTEN\n")
    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        member = tar.getmember("a.txt")
        contents = tar.extractfile(member).read().decode()
    assert contents == "OVERWRITTEN\n"


# --- works in repos without an initial commit ------------------------- #


def test_empty_repo_no_head_still_archives_untracked(tmp_path: Path):
    """A freshly ``git init``'d repo with no commits is the common
    "user just started this project" case. ``git archive`` would fail
    here; this entry walks the working tree via ``ls-files --others``
    and ships what the user has, gitignore-respected.
    """
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# Project\n")
    (repo / ".gitignore").write_text("dist/\n")
    (repo / "dist").mkdir()
    (repo / "dist" / "build.js").write_text("ignored output\n")

    entry = LocalGitArchive(src=repo)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        names = set(_file_names(tar))
    assert "README.md" in names
    assert ".gitignore" in names
    assert "dist/build.js" not in names  # gitignored even without a commit


# --- symlinks: stored as symlinks, target is committed string -------- #


def test_committed_symlink_appears_as_symlink_in_archive(repo_with_commit: Path):
    (repo_with_commit / "link").symlink_to("a.txt")
    _git(repo_with_commit, "add", "link")
    _git(repo_with_commit, "commit", "-q", "-m", "add symlink")

    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        member = tar.getmember("link")
    assert member.issym()
    assert member.linkname == "a.txt"


def test_symlink_with_absolute_target_is_skipped(tmp_path: Path):
    """An in-repo symlink whose target is absolute (e.g. left over
    from another machine: ``.fvm/flutter_sdk -> /Users/.../fvm/...``,
    or a malicious ``secrets -> /etc/shadow``) is silently dropped at
    seed time. The launcher never reads the target bytes either way,
    so the cross-tenant exfil property is unchanged; the stricter
    behavior matches the SDK snapshot-restore validator
    (``allow_external_symlink_targets=False``) so the symlink can't
    poison the next session's resume.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "secrets").symlink_to("/home/other-user/.aws/credentials")
    _git(repo, "add", "secrets")
    _git(repo, "commit", "-q", "-m", "add evil symlink")

    entry = LocalGitArchive(src=repo)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        assert "secrets" not in tar.getnames()


def test_symlink_escaping_via_dotdot_is_skipped(tmp_path: Path):
    """A symlink whose relative target normalizes outside the archive
    root (e.g. ``subdir/x -> ../../outside``) is rejected on the same
    grounds as absolute targets — the SDK restore validator would
    refuse it, so we drop it at seed time."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "subdir").mkdir()
    (repo / "subdir" / "escape").symlink_to("../../outside")
    _git(repo, "add", "subdir/escape")
    _git(repo, "commit", "-q", "-m", "add escaping symlink")

    entry = LocalGitArchive(src=repo)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        assert "subdir/escape" not in tar.getnames()


def test_relative_symlink_staying_inside_is_preserved(tmp_path: Path):
    """A relative symlink that resolves within the workspace (e.g.
    ``docs/README -> ../README.md``) is preserved. This is the
    legitimate in-repo case the SDK's restore validator also accepts.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hi\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "README").symlink_to("../README.md")
    _git(repo, "add", "README.md", "docs/README")
    _git(repo, "commit", "-q", "-m", "add safe relative symlink")

    entry = LocalGitArchive(src=repo)
    session = _make_session()
    _apply(entry, session)

    _dest, fileobj = session.write.await_args.args
    fileobj.seek(0)
    with tarfile.open(fileobj=fileobj, mode="r") as tar:
        member = tar.getmember("docs/README")
    assert member.issym()
    assert member.linkname == "../README.md"


# --- error cases ------------------------------------------------------ #


def test_src_does_not_exist_raises(tmp_path: Path):
    entry = LocalGitArchive(src=tmp_path / "nope")
    session = _make_session()
    with pytest.raises(LocalGitArchiveError) as exc:
        _apply(entry, session)
    assert "does not exist" in str(exc.value)
    # No session interaction if we never got past validation
    session.mkdir.assert_not_awaited()


def test_src_is_a_file_not_a_dir_raises(tmp_path: Path):
    file_path = tmp_path / "regular.txt"
    file_path.write_text("hi\n")
    entry = LocalGitArchive(src=file_path)
    session = _make_session()
    with pytest.raises(LocalGitArchiveError) as exc:
        _apply(entry, session)
    assert "must be a directory" in str(exc.value)


def test_src_is_not_a_git_repo_raises(tmp_path: Path):
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    (plain_dir / "file.txt").write_text("hi\n")
    entry = LocalGitArchive(src=plain_dir)
    session = _make_session()
    with pytest.raises(LocalGitArchiveError) as exc:
        _apply(entry, session)
    assert "not a git repo" in str(exc.value)


def test_tar_extract_failure_surfaces_stderr(repo_with_commit: Path):
    entry = LocalGitArchive(src=repo_with_commit)
    session = _make_session()
    fail = MagicMock()
    fail.ok.return_value = False
    fail.stderr = b"tar: corrupted\n"
    ok = MagicMock()
    ok.ok.return_value = True
    ok.stderr = b""
    # First exec call (tar) fails; second (rm) succeeds — cleanup runs
    # in `finally` so the test still verifies cleanup happens even on
    # the unhappy path.
    session.exec = AsyncMock(side_effect=[fail, ok])

    with pytest.raises(LocalGitArchiveError) as exc:
        _apply(entry, session)
    assert "tar extract failed" in str(exc.value)
    assert exc.value.stderr is not None
    assert "corrupted" in exc.value.stderr
    # Verify cleanup ran despite the extract failure
    assert session.exec.await_count == 2
    assert session.exec.await_args_list[1].args[0] == "rm"


