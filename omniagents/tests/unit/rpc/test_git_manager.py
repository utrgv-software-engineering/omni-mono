"""Unit tests for the typed git surface (tkt_l_l0lUi4A5V2).

Two layers:

* the pure machine-format parsers and the patch builder, exercised on
  captured git output (including the shapes that only appear during a
  conflict or a rename), and
* :class:`GitManager` driven against **real temporary repositories**, so
  every claim about staging, hunk selection, confirmation and rollback is
  verified by git itself rather than by a mock.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any, Dict, List

import sys

import pytest

from omniagents.rpc.git import (
    _ProgressScope,
    ConfirmationBroker,
    GitCapabilityError,
    GitConfirmationRequired,
    GitManager,
    GitOperationFailed,
    GitPathError,
    GitRepositoryNotFound,
    GitRpcError,
    GitStateInvalid,
    build_patch,
    canonical_repo_path,
    local_remote_path,
    redact_remote_url,
    parse_conflict_regions,
    parse_numstat_z,
    parse_patch_blocks,
    parse_push_porcelain,
    parse_raw_z,
    parse_refspec_intent,
    parse_status_v2,
    parse_worktree_porcelain,
)
from omniagents.workspace.host import HostWorkspace
from omniagents.workspace.mounted_host import MountedHostWorkspace

# ---------------------------------------------------------------------------
#  Fixtures / helpers
# ---------------------------------------------------------------------------


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main", ".")
    git(path, "config", "user.email", "tester@example.com")
    git(path, "config", "user.name", "Tester")
    git(path, "config", "commit.gpgsign", "false")
    return path


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


NUMBERED = "\n".join(f"line{index}" for index in range(1, 21)) + "\n"


def two_hunk_edit() -> str:
    lines = [f"line{index}" for index in range(1, 21)]
    lines[1] = "CHANGED-TOP"
    lines[17] = "CHANGED-BOTTOM"
    return "\n".join(lines) + "\n"


def make_manager(workspace, **overrides: Any) -> GitManager:
    async def resolver(environment_id: str):
        return workspace

    return GitManager(workspace_resolver=resolver, **overrides)


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def repo(workspace_root: Path) -> Path:
    target = init_repo(workspace_root / "app")
    write(target / "f.txt", NUMBERED)
    git(target, "add", "-A")
    git(target, "commit", "-qm", "init")
    return target


@pytest.fixture
def manager(workspace_root: Path) -> GitManager:
    return make_manager(HostWorkspace(root=str(workspace_root)))


class NoShellWorkspace:
    """Substrate double without a shell (the PyodideWorkspace posture)."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.scratch_root = root

    @property
    def supports_shell(self) -> bool:
        return False

    def resolve(self, path: str) -> str:
        return self.root if path in ("", ".") else os.path.join(self.root, path)

    @property
    def enforces_confinement(self) -> bool:
        return True

    def resolve_confined(self, path: str) -> str:
        # Confined substrate: resolve already refuses to leave the root, so
        # the wire surfaces' confined form is the same call.
        return self.resolve(path)

    def relative_path(self, absolute_path: str):
        try:
            return Path(absolute_path).relative_to(self.root).as_posix() or "."
        except ValueError:
            return None

    def contains_path(self, absolute_path: str) -> bool:
        return self.relative_path(absolute_path) is not None


# ---------------------------------------------------------------------------
#  Path canonicalization
# ---------------------------------------------------------------------------


def test_canonical_repo_path_normalizes():
    assert canonical_repo_path("") == "."
    assert canonical_repo_path(".") == "."
    assert canonical_repo_path("./apps//web/") == "apps/web"
    assert canonical_repo_path("apps/x/../web") == "apps/web"


@pytest.mark.parametrize(
    "path",
    [
        "..",
        "../etc/passwd",
        "apps/../../etc/passwd",
        "/etc/passwd",
        "C:/windows",
        "a\\b",
        "a/\x00b",
    ],
)
def test_canonical_repo_path_rejects_escapes(path):
    with pytest.raises(GitPathError):
        canonical_repo_path(path)


def test_canonical_repo_path_can_require_a_file():
    with pytest.raises(GitPathError):
        canonical_repo_path(".", allow_root=False)


# ---------------------------------------------------------------------------
#  Machine-format parsers
# ---------------------------------------------------------------------------


def test_parse_raw_z_handles_renames_and_paths_with_spaces():
    text = (
        ":100644 100644 57ac8df 0000000 M\0bin dat\0"
        ":100644 100644 71ac1b5 71ac1b5 R100\0old.txt\0new.txt\0"
        ":000000 100644 0000000 e69de29 A\0added.txt\0"
    )
    records = parse_raw_z(text)
    assert [record.change for record in records] == ["modified", "renamed", "added"]
    assert records[0].path == "bin dat"
    assert records[1].old_path == "old.txt"
    assert records[1].path == "new.txt"
    assert records[1].similarity == 100
    assert records[2].old_mode == "000000"


def test_parse_raw_z_rejects_truncated_records():
    with pytest.raises(ValueError):
        parse_raw_z(":100644 100644 57ac8df 0000000 M\0")


def test_parse_numstat_z_marks_binary_structurally():
    text = "-\t-\tbin.dat\0" "2\t2\tf1.txt\0" "3\t1\t\0old.txt\0new.txt\0"
    parsed = parse_numstat_z(text)
    assert parsed["bin.dat"] == (None, None)
    assert parsed["f1.txt"] == (2, 2)
    assert parsed["new.txt"] == (3, 1)


def test_parse_patch_blocks_separates_combined_conflict_blocks():
    patch = (
        "diff --cc c.txt\n"
        "index db13301,397340f..0000000\n"
        "--- a/c.txt\n"
        "+++ b/c.txt\n"
        "@@@ -1,3 -1,3 +1,7 @@@\n"
        "  l1\n"
        "diff --git a/o.txt b/o.txt\n"
        "index e45c9c2..71b3e33 100644\n"
        "--- a/o.txt\n"
        "+++ b/o.txt\n"
        "@@ -1 +1 @@\n"
        "-other\n"
        "+other2\n"
    )
    blocks = parse_patch_blocks(patch)
    assert [block.combined for block in blocks] == [True, False]
    assert blocks[1].hunks == [["@@ -1 +1 @@", "-other", "+other2"]]
    assert blocks[1].header_lines[0] == "diff --git a/o.txt b/o.txt"


def test_parse_patch_blocks_never_mistakes_body_lines_for_boundaries():
    patch = (
        "diff --git a/x b/x\n"
        "--- a/x\n"
        "+++ b/x\n"
        "@@ -1,2 +1,2 @@\n"
        "-diff --git a/fake b/fake\n"
        "+diff --git a/other b/other\n"
        " diff --git a/context b/context\n"
    )
    blocks = parse_patch_blocks(patch)
    assert len(blocks) == 1
    assert len(blocks[0].hunks[0]) == 4


def test_parse_status_v2_reads_branch_upstream_and_unmerged():
    text = (
        "# branch.oid f27abd5\0"
        "# branch.head main\0"
        "# branch.upstream origin/main\0"
        "# branch.ab +2 -3\0"
        "# stash 1\0"
        "1 .M N... 100644 100644 100644 57ac8df 57ac8df bin.dat\0"
        "2 R. N... 100644 100644 100644 71ac1b5 71ac1b5 R100 new.txt\0old.txt\0"
        "u UU N... 100644 100644 100644 100644 f0f2307 db13301 397340f c.txt\0"
        "? untracked.txt\0"
    )
    parsed = parse_status_v2(text)
    assert parsed["head"] == {
        "detached": False,
        "unborn": False,
        "branch": "main",
        "oid": "f27abd5",
    }
    assert parsed["upstream"] == {"name": "origin/main", "ahead": 2, "behind": 3}
    assert parsed["stash_count"] == 1
    assert parsed["untracked"] == ["untracked.txt"]
    assert parsed["conflicted"] == ["c.txt"]
    modified, renamed, unmerged = parsed["entries"]
    assert modified["path"] == "bin.dat"
    assert (modified["index_status"], modified["worktree_status"]) == (
        "unmodified",
        "modified",
    )
    assert renamed["path"] == "new.txt"
    assert renamed["orig_path"] == "old.txt"
    assert renamed["similarity"] == 100
    assert unmerged["unmerged"] == {
        "base_mode": "100644",
        "ours_mode": "100644",
        "theirs_mode": "100644",
        "worktree_mode": "100644",
        "base_oid": "f0f2307",
        "ours_oid": "db13301",
        "theirs_oid": "397340f",
    }


def test_parse_status_v2_represents_detached_and_unborn_heads():
    detached = parse_status_v2("# branch.oid abc123\0# branch.head (detached)\0")
    assert detached["head"]["detached"] is True
    assert detached["head"]["branch"] is None
    unborn = parse_status_v2("# branch.oid (initial)\0# branch.head main\0")
    assert unborn["head"]["unborn"] is True
    assert unborn["head"]["oid"] is None


def test_parse_worktree_porcelain():
    text = (
        "worktree /w/main\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree /scratch/worktrees/app-worker-1\nHEAD abc\n"
        "branch refs/heads/worker/app-worker-1\nlocked in use\n\n"
        "worktree /w/detached\nHEAD def\ndetached\nprunable gitdir gone\n\n"
    )
    parsed = parse_worktree_porcelain(text)
    assert [item["path"] for item in parsed] == [
        "/w/main",
        "/scratch/worktrees/app-worker-1",
        "/w/detached",
    ]
    assert parsed[1]["locked"] is True and parsed[1]["lock_reason"] == "in use"
    assert parsed[2]["detached"] is True and parsed[2]["prunable"] is True


def test_parse_push_porcelain_uses_flags_not_prose():
    text = (
        "To ../remote.git\n"
        "*\tHEAD:refs/heads/main\t[new branch]\n"
        "!\trefs/heads/topic:refs/heads/topic\t[rejected]\tnon-fast-forward\n"
        "=\trefs/heads/other:refs/heads/other\t[up to date]\n"
        "Done\n"
    )
    parsed = parse_push_porcelain(text)
    assert parsed["remote_url"] == "../remote.git"
    assert [item["status"] for item in parsed["updates"]] == [
        "created",
        "rejected",
        "up_to_date",
    ]
    assert parsed["updates"][1]["rejected"] is True
    assert parsed["updates"][1]["reason_raw"] == "non-fast-forward"
    assert parsed["updates"][0]["target_ref"] == "refs/heads/main"


def test_parse_conflict_regions_handles_merge_and_diff3_styles():
    merged = "l1\n<<<<<<< HEAD\nMAIN\n=======\nFEATURE\n>>>>>>> feature\nl3\n"
    (region,) = parse_conflict_regions(merged)
    assert region["ours"] == ["MAIN"]
    assert region["theirs"] == ["FEATURE"]
    assert region["base"] is None
    assert region["ours_label"] == "HEAD"
    assert region["theirs_label"] == "feature"
    assert (region["start_line"], region["end_line"]) == (2, 6)

    diff3 = (
        "l1\n<<<<<<< ours\nMAIN\n||||||| base\nORIGINAL\n"
        "=======\nFEATURE\n>>>>>>> theirs\n"
    )
    (region3,) = parse_conflict_regions(diff3)
    assert region3["base"] == ["ORIGINAL"]
    assert region3["base_label"] == "base"


def test_parse_conflict_regions_ignores_an_unterminated_marker():
    assert parse_conflict_regions("a\n<<<<<<< HEAD\nb\n") == []


# ---------------------------------------------------------------------------
#  Patch builder line arithmetic
# ---------------------------------------------------------------------------


def _diff_file_with_two_hunks(scope: str = "worktree"):
    from omniagents.rpc.git import assemble_diff

    raw = ":100644 100644 aaa bbb M\0f.txt\0"
    numstat = "2\t2\tf.txt\0"
    patch = (
        "diff --git a/f.txt b/f.txt\n"
        "index aaa..bbb 100644\n"
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@ -1,3 +1,4 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        "+BB\n"
        " c\n"
        "@@ -10,3 +11,3 @@ section\n"
        " x\n"
        "-y\n"
        "+Y\n"
        " z\n"
    )
    (diff_file,) = assemble_diff(
        scope=scope,
        raw_records=parse_raw_z(raw),
        numstat=parse_numstat_z(numstat),
        blocks=parse_patch_blocks(patch),
    )
    return diff_file


def test_build_patch_forward_shifts_only_the_new_side():
    diff_file = _diff_file_with_two_hunks()
    # Keep the *second* hunk only: the first hunk's +1 line must not be
    # counted, so the new side starts where the old side does.
    patch = build_patch([(diff_file, [diff_file.hunks[1]])], direction="forward")
    assert "@@ -10,3 +10,3 @@ section" in patch
    assert "@@ -1,3" not in patch

    both = build_patch([(diff_file, diff_file.hunks)], direction="forward")
    assert "@@ -1,3 +1,4 @@" in both
    # Second hunk shifts by the +1 the first hunk introduced.
    assert "@@ -10,3 +11,3 @@ section" in both


def test_build_patch_reverse_shifts_only_the_old_side():
    diff_file = _diff_file_with_two_hunks()
    patch = build_patch([(diff_file, [diff_file.hunks[1]])], direction="reverse")
    # Reverse-applied, the *new* side is the preimage and must be exact.
    assert "@@ -11,3 +11,3 @@ section" in patch

    both = build_patch([(diff_file, diff_file.hunks)], direction="reverse")
    assert "@@ -1,3 +1,4 @@" in both
    assert "@@ -10,3 +11,3 @@ section" in both


def test_build_patch_rejects_unknown_direction():
    diff_file = _diff_file_with_two_hunks()
    with pytest.raises(ValueError):
        build_patch([(diff_file, diff_file.hunks)], direction="sideways")


def test_hunk_ids_are_scoped_and_content_addressed():
    worktree = _diff_file_with_two_hunks("worktree")
    staged = _diff_file_with_two_hunks("staged")
    assert worktree.hunks[0].hunk_id != staged.hunks[0].hunk_id
    assert worktree.hunks[0].hunk_id != worktree.hunks[1].hunk_id
    assert worktree.hunks[0].hunk_id == _diff_file_with_two_hunks().hunks[0].hunk_id


def test_assemble_diff_refuses_to_guess_when_association_breaks():
    from omniagents.rpc.git import assemble_diff

    with pytest.raises(ValueError, match="diff association failed"):
        assemble_diff(
            scope="worktree",
            raw_records=parse_raw_z(":100644 100644 a b M\0f.txt\0"),
            numstat={},
            blocks=[],
        )


# ---------------------------------------------------------------------------
#  Confirmation broker
# ---------------------------------------------------------------------------


def test_confirmation_token_is_single_use_and_impact_bound():
    broker = ConfirmationBroker()
    impact = {"paths": ["a.txt"]}
    token = broker.mint("environment-1", "app", "discard_changes", impact)
    assert (
        broker.redeem(token, "environment-1", "app", "discard_changes", impact) is True
    )
    # Single use.
    assert (
        broker.redeem(token, "environment-1", "app", "discard_changes", impact) is False
    )


def test_confirmation_token_rejects_a_changed_impact_or_foreign_environment():
    broker = ConfirmationBroker()
    impact = {"paths": ["a.txt"]}
    token = broker.mint("environment-1", "app", "discard_changes", impact)
    assert (
        broker.redeem(
            token,
            "environment-1",
            "app",
            "discard_changes",
            {"paths": ["b.txt"]},
        )
        is False
    )

    token = broker.mint("environment-1", "app", "discard_changes", impact)
    assert (
        broker.redeem(token, "other-environment", "app", "discard_changes", impact)
        is False
    )

    token = broker.mint("environment-1", "app", "discard_changes", impact)
    assert (
        broker.redeem(token, "environment-1", "other-repo", "discard_changes", impact)
        is False
    )

    token = broker.mint("environment-1", "app", "discard_changes", impact)
    assert broker.redeem(token, "environment-1", "app", "reset_hard", impact) is False


def test_confirmation_token_expires():
    broker = ConfirmationBroker(ttl_seconds=-1.0)
    token = broker.mint("environment-1", "app", "reset_hard", {})
    assert broker.redeem(token, "environment-1", "app", "reset_hard", {}) is False


# ---------------------------------------------------------------------------
#  Repository discovery & confinement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_environment_id_resolves_and_addresses_the_workspace(
    workspace_root: Path,
):
    resolved_environment_ids: List[str] = []

    class EmptyWorkspace(NoShellWorkspace):
        async def list_dir(self, *args, **kwargs):
            return []

    workspace = EmptyWorkspace(str(workspace_root))

    async def resolver(environment_id: str):
        resolved_environment_ids.append(environment_id)
        return workspace

    manager = GitManager(workspace_resolver=resolver)
    result = await manager.list_repositories("environment-primary")

    assert resolved_environment_ids == ["environment-primary"]
    assert result["environment_id"] == "environment-primary"
    assert "session_id" not in result


@pytest.mark.asyncio
async def test_multi_repository_projects_are_addressed_unambiguously(
    workspace_root: Path, manager: GitManager
):
    for name in ("apps/web", "services/api"):
        target = init_repo(workspace_root / name)
        write(target / "README.md", "hi\n")
        git(target, "add", "-A")
        git(target, "commit", "-qm", "init")

    listing = await manager.list_repositories("s1")
    assert [item["repo"] for item in listing["repositories"]] == [
        "apps/web",
        "services/api",
    ]
    # Each id resolves to its own repository; nothing is implicit.
    web = await manager.status("s1", "apps/web")
    api = await manager.status("s1", "services/api")
    assert web["repo"] == "apps/web" and api["repo"] == "services/api"

    write(workspace_root / "apps/web/only-web.txt", "x\n")
    web_status = await manager.status("s1", "apps/web")
    api_status = await manager.status("s1", "services/api")
    assert web_status["untracked"] == ["only-web.txt"]
    assert api_status["untracked"] == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="these directory names cannot be created on Windows"
)
@pytest.mark.asyncio
@pytest.mark.parametrize("awkward", ["back\\slash", "C:", "E:migration.sql"])
async def test_discovery_survives_directories_it_cannot_address(
    workspace_root: Path, manager: GitManager, awkward: str
):
    """A directory name this surface's wire policy refuses must be skipped,
    not allowed to abort the whole enumeration.

    ``FileEntry`` deliberately treats a backslash as a filename character
    (it is one, on POSIX) while ``canonical_repo_path`` refuses it on the
    wire, along with drive-letter spellings. An agent can create such a
    directory; when it did, ``git_list_repositories`` failed outright with
    ``git_path_invalid`` and the caller lost every legitimate repository
    alongside it.
    """
    target = init_repo(workspace_root / "app")
    write(target / "f.txt", "x\n")
    git(target, "add", "-A")
    git(target, "commit", "-qm", "init")
    (workspace_root / awkward).mkdir()

    listing = await manager.list_repositories("s1")
    assert [item["repo"] for item in listing["repositories"]] == ["app"]


@pytest.mark.asyncio
async def test_repo_outside_the_workspace_is_refused(tmp_path: Path):
    outer = init_repo(tmp_path / "outer")
    inner = outer / "workspace"
    inner.mkdir()
    write(outer / "seed.txt", "x\n")
    git(outer, "add", "-A")
    git(outer, "commit", "-qm", "init")

    manager = make_manager(HostWorkspace(root=str(inner)))
    # The workspace is *inside* a repository whose root is above it: git
    # would happily operate on the parent, this surface must not.
    with pytest.raises(GitRepositoryNotFound) as excinfo:
        await manager.status("s1", ".")
    assert excinfo.value.data["reason"] == "outside_workspace"
    assert (await manager.list_repositories("s1"))["repositories"] == []


@pytest.mark.asyncio
async def test_repo_traversal_is_rejected_before_any_git_runs(manager: GitManager):
    for candidate in ("../elsewhere", "/etc", "app/../../escape"):
        with pytest.raises(GitPathError):
            await manager.status("s1", candidate)


@pytest.mark.asyncio
async def test_missing_repository_is_a_distinct_typed_error(
    workspace_root: Path, manager: GitManager
):
    (workspace_root / "plain").mkdir()
    with pytest.raises(GitRepositoryNotFound) as excinfo:
        await manager.status("s1", "plain")
    assert excinfo.value.code == -32090
    assert excinfo.value.data["kind"] == "git_repository_not_found"
    assert excinfo.value.data["reason"] == "no_work_tree"


@pytest.mark.asyncio
async def test_substrate_without_a_shell_reports_a_capability_error(
    workspace_root: Path,
):
    manager = make_manager(NoShellWorkspace(str(workspace_root)))
    with pytest.raises(GitCapabilityError) as excinfo:
        await manager.status("s1", "app")
    assert excinfo.value.code == -32095
    assert excinfo.value.data["reason"] == "no_shell"


# ---------------------------------------------------------------------------
#  Status / diff shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_detached_head_structurally(
    repo: Path, manager: GitManager
):
    oid = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "--detach", oid)
    status = await manager.status("s1", "app")
    assert status["head"]["detached"] is True
    assert status["head"]["branch"] is None
    assert status["head"]["oid"] == oid
    assert status["state"] == "clean"


@pytest.mark.asyncio
async def test_status_reports_an_unborn_head(workspace_root: Path, manager: GitManager):
    init_repo(workspace_root / "fresh")
    status = await manager.status("s1", "fresh")
    assert status["head"]["unborn"] is True
    assert status["head"]["oid"] is None


@pytest.mark.asyncio
async def test_diff_exposes_hunks_with_stable_identity(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app")
    (diff_file,) = diff["files"]
    assert diff_file["path"] == "f.txt"
    assert diff_file["change"] == "modified"
    assert diff_file["binary"] is False
    assert diff_file["hunk_selectable"] is True
    assert len(diff_file["hunks"]) == 2
    assert diff_file["added_lines"] == 2 and diff_file["deleted_lines"] == 2

    first = diff_file["hunks"][0]
    assert [line["origin"] for line in first["lines"]].count("add") == 1
    added = next(line for line in first["lines"] if line["origin"] == "add")
    assert added["content"] == "CHANGED-TOP"
    assert added["old_lineno"] is None and added["new_lineno"] == 2

    again = await manager.diff("s1", "app")
    assert [hunk["hunk_id"] for hunk in again["files"][0]["hunks"]] == [
        hunk["hunk_id"] for hunk in diff_file["hunks"]
    ]


@pytest.mark.asyncio
async def test_binary_files_are_marked_and_not_hunk_selectable(
    repo: Path, manager: GitManager
):
    (repo / "bin.dat").write_bytes(b"\x00\x01original\x00")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "bin")
    (repo / "bin.dat").write_bytes(b"\x00\x02changed\x00")

    diff = await manager.diff("s1", "app")
    (diff_file,) = diff["files"]
    assert diff_file["binary"] is True
    assert diff_file["hunks"] == []
    assert diff_file["hunk_selectable"] is False

    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage(
            "s1", "app", hunks=[{"path": "bin.dat", "hunk_id": "0" * 16}]
        )
    assert excinfo.value.data["reason"] == "hunk_selection_unsupported"


@pytest.mark.asyncio
async def test_diff_modes_and_log(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "add", "-A")
    assert (await manager.diff("s1", "app", mode="worktree"))["files"] == []
    staged = await manager.diff("s1", "app", mode="staged")
    assert [item["path"] for item in staged["files"]] == ["f.txt"]
    head = await manager.diff("s1", "app", mode="head")
    assert [item["path"] for item in head["files"]] == ["f.txt"]

    git(repo, "commit", "-qm", "second commit\n\nbody line")
    for index in range(3):
        write(repo / f"extra{index}.txt", "x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", f"extra {index}")
    # max_count really limits: the repo now holds 5 commits.
    log = await manager.log("s1", "app", max_count=2)
    assert [commit["subject"] for commit in log["commits"]] == [
        "extra 2",
        "extra 1",
    ]
    assert log["max_count"] == 2 and log["truncated"] is True
    full = await manager.log("s1", "app")
    assert len(full["commits"]) == 5
    assert full["truncated"] is False
    skipped = await manager.log("s1", "app", max_count=2, skip=2)
    assert [commit["subject"] for commit in skipped["commits"]] == [
        "extra 0",
        "second commit",
    ]
    log = await manager.log("s1", "app", max_count=5)
    assert [commit["subject"] for commit in log["commits"][-2:]] == [
        "second commit",
        "init",
    ]
    body_commit = next(
        item for item in log["commits"] if item["subject"] == "second commit"
    )
    assert body_commit["body"].strip() == "body line"
    assert log["commits"][0]["parents"] == [log["commits"][1]["oid"]]

    ranged = await manager.diff(
        "s1", "app", mode="range", from_rev="HEAD~4", to_rev="HEAD~3"
    )
    assert [item["path"] for item in ranged["files"]] == ["f.txt"]

    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.diff("s1", "app", mode="sideways")
    assert excinfo.value.data["reason"] == "unknown_mode"


@pytest.mark.asyncio
async def test_revisions_that_look_like_options_are_refused(
    repo: Path, manager: GitManager
):
    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.log("s1", "app", rev="--output=/tmp/pwned")
    assert excinfo.value.data["reason"] == "invalid_revision"


# ---------------------------------------------------------------------------
#  Hunk-level staging (the heart of the ticket)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hunk_level_stage_and_unstage_round_trip(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app")
    hunks = diff["files"][0]["hunks"]

    result = await manager.stage(
        "s1", "app", hunks=[{"path": "f.txt", "hunk_id": hunks[1]["hunk_id"]}]
    )
    assert result["staged_hunks"] == [{"path": "f.txt", "hunk_id": hunks[1]["hunk_id"]}]
    # Exactly the second hunk is in the index; the first is still unstaged.
    staged = git(repo, "diff", "--cached")
    assert "CHANGED-BOTTOM" in staged and "CHANGED-TOP" not in staged
    unstaged = git(repo, "diff")
    assert "CHANGED-TOP" in unstaged and "CHANGED-BOTTOM" not in unstaged
    # The worktree file is untouched by staging.
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    staged_diff = await manager.diff("s1", "app", mode="staged")
    staged_hunk = staged_diff["files"][0]["hunks"][0]
    await manager.unstage(
        "s1", "app", hunks=[{"path": "f.txt", "hunk_id": staged_hunk["hunk_id"]}]
    )
    assert git(repo, "diff", "--cached").strip() == ""
    assert (repo / "f.txt").read_text() == two_hunk_edit()


@pytest.mark.asyncio
async def test_staging_several_hunks_across_files_is_one_atomic_patch(
    repo: Path, manager: GitManager
):
    write(repo / "g.txt", NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add g")
    write(repo / "f.txt", two_hunk_edit())
    write(repo / "g.txt", two_hunk_edit())

    diff = await manager.diff("s1", "app")
    selection = [
        {"path": item["path"], "hunk_id": item["hunks"][0]["hunk_id"]}
        for item in diff["files"]
    ]
    await manager.stage("s1", "app", hunks=selection)
    staged = git(repo, "diff", "--cached")
    assert staged.count("CHANGED-TOP") == 2
    assert "CHANGED-BOTTOM" not in staged


@pytest.mark.asyncio
async def test_a_stale_hunk_id_fails_closed(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app")
    stale = diff["files"][0]["hunks"][0]["hunk_id"]
    # The client's view is now out of date.
    write(repo / "f.txt", NUMBERED.replace("line2", "SOMETHING-ELSE"))

    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage("s1", "app", hunks=[{"path": "f.txt", "hunk_id": stale}])
    assert excinfo.value.data["reason"] == "hunk_not_found"
    assert excinfo.value.data["stale"] == [{"path": "f.txt", "hunk_id": stale}]
    assert git(repo, "diff", "--cached").strip() == ""


@pytest.mark.asyncio
async def test_a_path_cannot_be_selected_whole_and_by_hunk(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app")
    hunk_id = diff["files"][0]["hunks"][0]["hunk_id"]
    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage(
            "s1",
            "app",
            paths=["f.txt"],
            hunks=[{"path": "f.txt", "hunk_id": hunk_id}],
        )
    assert excinfo.value.data["reason"] == "overlapping_selection"


@pytest.mark.asyncio
async def test_empty_selection_is_refused(repo: Path, manager: GitManager):
    for call in (manager.stage, manager.unstage, manager.discard):
        with pytest.raises(GitStateInvalid) as excinfo:
            await call("s1", "app")
        assert excinfo.value.data["reason"] == "empty_selection"


@pytest.mark.asyncio
async def test_file_level_staging_covers_untracked_files(
    repo: Path, manager: GitManager
):
    write(repo / "new.txt", "brand new\n")
    result = await manager.stage("s1", "app", paths=["new.txt"])
    assert result["staged_paths"] == ["new.txt"]
    status = await manager.status("s1", "app")
    entry = next(item for item in status["entries"] if item["path"] == "new.txt")
    assert entry["index_status"] == "added" and entry["staged"] is True

    await manager.unstage("s1", "app", paths=["new.txt"])
    assert (await manager.status("s1", "app"))["untracked"] == ["new.txt"]


@pytest.mark.asyncio
async def test_unstage_on_an_unborn_branch_drops_the_index_entry(
    workspace_root: Path, manager: GitManager
):
    fresh = init_repo(workspace_root / "fresh")
    write(fresh / "a.txt", "a\n")
    await manager.stage("s1", "fresh", paths=["a.txt"])
    assert (await manager.status("s1", "fresh"))["entries"][0]["staged"] is True
    await manager.unstage("s1", "fresh", paths=["a.txt"])
    status = await manager.status("s1", "fresh")
    assert status["entries"] == []
    assert status["untracked"] == ["a.txt"]


# ---------------------------------------------------------------------------
#  Failure rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_apply_failure_rolls_the_index_back(
    repo: Path, manager: GitManager
):
    """A genuine ``git apply`` rejection, with no injection at all.

    ``paths`` are git pathspecs, so ``src/*.txt`` also covers the file the
    hunk selection targets: step one stages it whole, which moves the
    index out from under the patch step two was built against, and git
    refuses. The index must come back exactly as it was.
    """
    (repo / "src").mkdir()
    write(repo / "src" / "b.txt", NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add src")
    write(repo / "src" / "b.txt", two_hunk_edit())
    write(repo / "f.txt", two_hunk_edit())

    diff = await manager.diff("s1", "app")
    target = next(item for item in diff["files"] if item["path"] == "src/b.txt")
    with pytest.raises(GitOperationFailed) as excinfo:
        await manager.stage(
            "s1",
            "app",
            paths=["src/*.txt"],
            hunks=[{"path": "src/b.txt", "hunk_id": target["hunks"][0]["hunk_id"]}],
        )
    assert excinfo.value.data["rolled_back"] is True
    # Neither half of the operation survived.
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""
    assert (repo / "src" / "b.txt").read_text() == two_hunk_edit()
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    # The repository is still usable: the same hunk stages on its own.
    await manager.stage(
        "s1",
        "app",
        hunks=[{"path": "src/b.txt", "hunk_id": target["hunks"][0]["hunk_id"]}],
    )
    assert git(repo, "diff", "--cached", "--name-only").strip() == "src/b.txt"


@pytest.mark.asyncio
async def test_a_typed_failure_between_steps_rolls_the_index_back(
    repo: Path, manager: GitManager, monkeypatch
):
    """The injected twin of the test above, pinning the error contract."""
    write(repo / "g.txt", NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add g")
    write(repo / "f.txt", "brand new content\n")
    write(repo / "g.txt", two_hunk_edit())

    diff = await manager.diff("s1", "app")
    g_hunk = next(item for item in diff["files"] if item["path"] == "g.txt")["hunks"][0]

    async def exploding_apply(*args: Any, **kwargs: Any) -> None:
        raise GitOperationFailed(
            "simulated apply failure", operation="stage_hunks", exit_code=1
        )

    monkeypatch.setattr(GitManager, "_apply_patch", exploding_apply)

    with pytest.raises(GitOperationFailed) as excinfo:
        await manager.stage(
            "s1",
            "app",
            paths=["f.txt"],
            hunks=[{"path": "g.txt", "hunk_id": g_hunk["hunk_id"]}],
        )
    assert excinfo.value.data["rolled_back"] is True
    assert git(repo, "diff", "--cached").strip() == ""
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""


@pytest.mark.asyncio
async def test_the_index_snapshot_really_restores_state(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    workspace = HostWorkspace(root=str(repo.parent))
    snapshot = await manager._index_snapshot(workspace, str(repo))
    assert snapshot
    git(repo, "add", "-A")
    assert git(repo, "diff", "--cached", "--name-only").strip() == "f.txt"
    await manager._restore_index(workspace, str(repo), snapshot)
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""


@pytest.mark.asyncio
async def test_a_multi_step_operation_refuses_an_unsnapshottable_index(
    workspace_root: Path, manager: GitManager
):
    """An unmerged index cannot be snapshotted, so it cannot be rolled back.

    Rather than run a two-step stage that might strand the repository
    half-updated, the surface refuses up front.
    """
    target = make_conflict(workspace_root / "conflict")
    write(target / "other.txt", NUMBERED)
    git(target, "add", "other.txt")
    git(target, "commit", "-qm", "other", check=False)
    write(target / "other.txt", two_hunk_edit())
    write(target / "extra.txt", "extra\n")

    diff = await manager.diff("s1", "conflict")
    other = next(item for item in diff["files"] if item["path"] == "other.txt")
    assert other["hunk_selectable"] is True

    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage(
            "s1",
            "conflict",
            paths=["extra.txt"],
            hunks=[{"path": "other.txt", "hunk_id": other["hunks"][0]["hunk_id"]}],
        )
    assert excinfo.value.data["reason"] == "unmerged_index"
    assert excinfo.value.data["operation"] == "stage"
    assert "extra.txt" not in git(target, "diff", "--cached", "--name-only")


@pytest.mark.asyncio
async def test_unmerged_paths_cannot_be_selected_by_hunk(
    workspace_root: Path, manager: GitManager
):
    make_conflict(workspace_root / "conflict")
    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage(
            "s1", "conflict", hunks=[{"path": "c.txt", "hunk_id": "0" * 16}]
        )
    assert excinfo.value.data["reason"] == "hunk_selection_unsupported"
    assert excinfo.value.data["paths"] == ["c.txt"]


# ---------------------------------------------------------------------------
#  Destructive operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discard_requires_confirmation_and_then_applies(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app")
    hunks = diff["files"][0]["hunks"]

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.discard(
            "s1", "app", hunks=[{"path": "f.txt", "hunk_id": hunks[0]["hunk_id"]}]
        )
    data = excinfo.value.data
    assert data["kind"] == "git_confirmation_required"
    assert data["operation"] == "discard_changes"
    assert data["impact"]["hunks"] == [f"f.txt:{hunks[0]['hunk_id']}"]
    # Nothing happened yet.
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    await manager.discard(
        "s1",
        "app",
        hunks=[{"path": "f.txt", "hunk_id": hunks[0]["hunk_id"]}],
        confirmation_token=data["confirmation_token"],
    )
    content = (repo / "f.txt").read_text()
    assert "CHANGED-TOP" not in content
    assert "CHANGED-BOTTOM" in content


@pytest.mark.asyncio
async def test_a_confirmation_token_is_invalidated_by_a_changed_repository(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    with pytest.raises(GitConfirmationRequired) as first:
        await manager.discard("s1", "app", paths=["f.txt"])
    token = first.value.data["confirmation_token"]

    # The worktree moves on before the client confirms.
    write(repo / "f.txt", NUMBERED.replace("line5", "LATER-EDIT"))
    with pytest.raises(GitConfirmationRequired) as second:
        await manager.discard("s1", "app", paths=["f.txt"], confirmation_token=token)
    assert second.value.data["reason"] == "stale_confirmation"
    assert second.value.data["confirmation_token"] != token
    assert "LATER-EDIT" in (repo / "f.txt").read_text()


@pytest.mark.asyncio
async def test_discard_pre_validates_the_whole_selection(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.discard("s1", "app", paths=["f.txt", "never-touched.txt"])
    assert excinfo.value.data["reason"] == "nothing_to_discard"
    assert excinfo.value.data["paths"] == ["never-touched.txt"]
    # The valid half of the selection is untouched: nothing partially ran.
    assert (repo / "f.txt").read_text() == two_hunk_edit()


@pytest.mark.asyncio
async def test_discard_restores_binary_and_deleted_files(
    repo: Path, manager: GitManager
):
    (repo / "bin.dat").write_bytes(b"\x00\x01original\x00")
    write(repo / "gone.txt", "still here\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "binary")
    (repo / "bin.dat").write_bytes(b"\x00\x02changed\x00")
    (repo / "gone.txt").unlink()

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.discard("s1", "app", paths=["bin.dat", "gone.txt"])
    await manager.discard(
        "s1",
        "app",
        paths=["bin.dat", "gone.txt"],
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert (repo / "bin.dat").read_bytes() == b"\x00\x01original\x00"
    assert (repo / "gone.txt").read_text() == "still here\n"


@pytest.mark.asyncio
async def test_reset_hard_requires_confirmation(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.reset("s1", "app", mode="hard")
    impact = excinfo.value.data["impact"]
    assert impact["dirty_paths"] == ["f.txt"]
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    await manager.reset(
        "s1",
        "app",
        mode="hard",
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert (repo / "f.txt").read_text() == NUMBERED


@pytest.mark.asyncio
async def test_soft_and_mixed_resets_need_no_confirmation(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "add", "-A")
    status = await manager.reset("s1", "app", mode="mixed")
    assert status["entries"][0]["staged"] is False
    # The worktree content survives a mixed reset — nothing was destroyed.
    assert (repo / "f.txt").read_text() == two_hunk_edit()


@pytest.mark.asyncio
async def test_checkout_creates_switches_and_gates_discarding(
    repo: Path, manager: GitManager
):
    status = await manager.checkout("s1", "app", branch="feature", create=True)
    assert status["head"]["branch"] == "feature"
    write(repo / "f.txt", two_hunk_edit())
    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.checkout("s1", "app", branch="main", discard_changes=True)
    assert excinfo.value.data["operation"] == "checkout_discard_changes"
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    status = await manager.checkout(
        "s1",
        "app",
        branch="main",
        discard_changes=True,
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert status["head"]["branch"] == "main"
    assert (repo / "f.txt").read_text() == NUMBERED


@pytest.mark.asyncio
async def test_commit_and_amend(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    await manager.stage("s1", "app", paths=["f.txt"])
    committed = await manager.commit("s1", "app", "typed commit")
    assert committed["oid"] == git(repo, "rev-parse", "HEAD").strip()
    assert committed["amended"] is False

    with pytest.raises(GitStateInvalid) as empty:
        await manager.commit("s1", "app", "   ")
    assert empty.value.data["reason"] == "empty_message"

    with pytest.raises(GitStateInvalid) as nothing:
        await manager.commit("s1", "app", "nothing here")
    assert nothing.value.data["reason"] == "nothing_staged"

    with pytest.raises(GitConfirmationRequired) as amend:
        await manager.commit("s1", "app", "reworded", amend=True)
    assert amend.value.data["operation"] == "amend_commit"
    amended = await manager.commit(
        "s1",
        "app",
        "reworded",
        amend=True,
        confirmation_token=amend.value.data["confirmation_token"],
    )
    assert amended["amended"] is True
    assert git(repo, "log", "-1", "--format=%s").strip() == "reworded"


# ---------------------------------------------------------------------------
#  Conflicts & worktrees
# ---------------------------------------------------------------------------


def make_conflict(path: Path) -> Path:
    target = init_repo(path)
    write(target / "c.txt", "l1\nl2\nl3\n")
    git(target, "add", "-A")
    git(target, "commit", "-qm", "base")
    git(target, "checkout", "-q", "-b", "feature")
    write(target / "c.txt", "l1\nFEATURE\nl3\n")
    git(target, "commit", "-qam", "feature")
    git(target, "checkout", "-q", "main")
    write(target / "c.txt", "l1\nMAIN\nl3\n")
    git(target, "commit", "-qam", "main")
    git(target, "merge", "feature", check=False)
    return target


@pytest.mark.asyncio
async def test_conflicts_are_represented_structurally(
    workspace_root: Path, manager: GitManager
):
    make_conflict(workspace_root / "conflict")
    status = await manager.status("s1", "conflict")
    assert status["state"] == "merging"
    assert status["conflicted"] == ["c.txt"]
    entry = next(item for item in status["entries"] if item["path"] == "c.txt")
    assert entry["xy"] == "UU"
    assert entry["unmerged"]["ours_oid"] != entry["unmerged"]["theirs_oid"]

    conflicts = await manager.conflicts("s1", "conflict")
    assert conflicts["state"] == "merging"
    (conflict,) = conflicts["conflicts"]
    assert conflict["path"] == "c.txt"
    assert set(conflict["stages"]) == {"base", "ours", "theirs"}
    assert conflict["regions_available"] is True
    (region,) = conflict["regions"]
    assert region["ours"] == ["MAIN"]
    assert region["theirs"] == ["FEATURE"]


@pytest.mark.asyncio
async def test_diff_skips_unmerged_paths_without_breaking_association(
    workspace_root: Path, manager: GitManager
):
    target = make_conflict(workspace_root / "conflict")
    write(target / "other.txt", "other\n")
    git(target, "add", "other.txt")
    write(target / "other.txt", "other changed\n")

    diff = await manager.diff("s1", "conflict")
    by_path = {item["path"]: item for item in diff["files"]}
    assert by_path["c.txt"]["unmerged"] is True
    assert by_path["c.txt"]["hunks"] == []
    assert by_path["c.txt"]["hunk_selectable"] is False
    assert by_path["other.txt"]["hunks"]


@pytest.mark.asyncio
async def test_worktrees_outside_the_workspace_are_visible_but_not_operable(
    repo: Path, workspace_root: Path, tmp_path: Path, manager: GitManager
):
    scratch = tmp_path / "scratch" / "worktrees" / "app-worker-1"
    git(
        repo, "worktree", "add", "-q", "-b", "worker/app-worker-1", str(scratch), "HEAD"
    )
    inside = workspace_root / "app-review"
    git(repo, "worktree", "add", "-q", "-b", "review", str(inside), "HEAD")

    listing = await manager.list_worktrees("s1", "app")
    by_path = {item["path"]: item for item in listing["worktrees"]}
    worker = by_path[str(scratch)]
    assert worker["accessible"] is False
    assert worker["inaccessible_reason"] == "outside_workspace"
    assert worker["category"] == "worker"
    assert worker["branch"] == "refs/heads/worker/app-worker-1"

    local = by_path[str(inside)]
    assert local["accessible"] is True
    assert local["repo"] == "app-review"

    # A remote-tracking ref exists, so include_remote genuinely filters.
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    local_only = await manager.list_branches("s1", "app", include_remote=False)
    assert all(not item["remote"] for item in local_only["branches"])
    with_remote = await manager.list_branches("s1", "app", include_remote=True)
    assert [item["name"] for item in with_remote["branches"] if item["remote"]] == [
        "origin/main"
    ]
    worker_branch = next(
        item for item in local_only["branches"] if item["name"] == "worker/app-worker-1"
    )
    assert worker_branch["category"] == "worker"
    assert worker_branch["worktree_path"] == str(scratch)


# ---------------------------------------------------------------------------
#  Network operations against a local remote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_push_and_force_push_confirmation(
    repo: Path, workspace_root: Path, tmp_path: Path, manager: GitManager
):
    remote = workspace_root / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))

    pushed = await manager.push(
        "s1", "app", remote="origin", refspec="main:main", set_upstream=True
    )
    assert pushed["ok"] is True
    assert [item["status"] for item in pushed["updates"]] == ["created"]
    assert pushed["updates"][0]["target_ref"] == "refs/heads/main"

    # Nothing new upstream: no remote-tracking ref for a branch moved.
    fetched = await manager.fetch("s1", "app", remote="origin")
    assert [
        item for item in fetched["updated_refs"] if item["ref"].endswith("/main")
    ] == []

    # Publish a commit, then rewrite it: pushing the rewrite is a genuine
    # non-fast-forward and needs an explicit confirmation.
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "commit", "-qam", "published")
    await manager.push("s1", "app", remote="origin", refspec="main:main")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    write(repo / "f.txt", "divergent\n")
    git(repo, "commit", "-qam", "divergent")

    rejected = await manager.push("s1", "app", remote="origin", refspec="main:main")
    assert rejected["ok"] is False
    assert rejected["updates"][0]["status"] == "rejected"

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.push(
            "s1", "app", remote="origin", refspec="main:main", force=True
        )
    assert excinfo.value.data["operation"] == "force_push"

    forced = await manager.push(
        "s1",
        "app",
        remote="origin",
        refspec="main:main",
        force=True,
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert forced["ok"] is True
    assert forced["updates"][0]["status"] == "forced_update"

    # A fetch after the remote moved reports the ref delta structurally.
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    refreshed = await manager.fetch("s1", "app", remote="origin")
    moved = next(
        item
        for item in refreshed["updated_refs"]
        if item["ref"] == "refs/remotes/origin/main"
    )
    assert moved["change"] == "created"
    assert moved["new_oid"] == git(repo, "rev-parse", "HEAD").strip()


@pytest.mark.asyncio
async def test_a_rejected_push_is_reported_structurally(
    repo: Path, workspace_root: Path, tmp_path: Path, manager: GitManager
):
    remote = workspace_root / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    await manager.push("s1", "app", remote="origin", refspec="main:main")

    # Move the remote ahead so a plain push is non-fast-forward.
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True
    )
    git(other, "config", "user.email", "o@example.com")
    git(other, "config", "user.name", "Other")
    write(other / "f.txt", "remote side\n")
    git(other, "commit", "-qam", "remote work")
    git(other, "push", "-q", "origin", "main")

    write(repo / "f.txt", "local side\n")
    git(repo, "commit", "-qam", "local work")
    result = await manager.push("s1", "app", remote="origin", refspec="main:main")
    assert result["ok"] is False
    assert result["rejected"] == ["refs/heads/main"]
    assert result["updates"][0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_pull_reports_conflicts_as_a_state_not_an_error(
    repo: Path, workspace_root: Path, tmp_path: Path, manager: GitManager
):
    remote = workspace_root / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    await manager.push("s1", "app", remote="origin", refspec="main:main")

    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True
    )
    git(other, "config", "user.email", "o@example.com")
    git(other, "config", "user.name", "Other")
    write(other / "f.txt", NUMBERED.replace("line2", "REMOTE"))
    git(other, "commit", "-qam", "remote change")
    git(other, "push", "-q", "origin", "main")

    write(repo / "f.txt", NUMBERED.replace("line2", "LOCAL"))
    git(repo, "commit", "-qam", "local change")

    result = await manager.pull("s1", "app", remote="origin", refspec="main")
    assert result["ok"] is False
    assert result["conflicted"] == ["f.txt"]
    assert result["state"] == "merging"

    conflicts = await manager.conflicts("s1", "app")
    (conflict,) = conflicts["conflicts"]
    assert conflict["regions"][0]["ours"] == ["LOCAL"]
    assert conflict["regions"][0]["theirs"] == ["REMOTE"]


@pytest.mark.asyncio
async def test_a_rebasing_pull_reports_the_rebase_state(
    repo: Path, workspace_root: Path, tmp_path: Path, manager: GitManager
):
    remote = workspace_root / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    await manager.push("s1", "app", remote="origin", refspec="main:main")

    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True
    )
    git(other, "config", "user.email", "o@example.com")
    git(other, "config", "user.name", "Other")
    write(other / "f.txt", NUMBERED.replace("line2", "REMOTE"))
    git(other, "commit", "-qam", "remote change")
    git(other, "push", "-q", "origin", "main")

    write(repo / "f.txt", NUMBERED.replace("line2", "LOCAL"))
    git(repo, "commit", "-qam", "local change")

    result = await manager.pull(
        "s1", "app", remote="origin", refspec="main", rebase=True
    )
    assert result["ok"] is False
    assert result["state"] == "rebasing"
    assert result["conflicted"] == ["f.txt"]
    assert (await manager.status("s1", "app"))["state"] == "rebasing"


@pytest.mark.asyncio
async def test_progress_notifications_describe_the_server_lifecycle(
    repo: Path, workspace_root: Path, tmp_path: Path, manager: GitManager
):
    remote = workspace_root / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    events: List[Dict[str, Any]] = []

    async def emit(payload: Dict[str, Any]) -> None:
        events.append(payload)

    await manager.push(
        "environment-primary",
        "app",
        remote="origin",
        refspec="main:main",
        progress=emit,
    )
    assert [item["phase"] for item in events] == ["started", "completed"]
    assert {item["operation"] for item in events} == {"push"}
    assert {item["environment_id"] for item in events} == {"environment-primary"}
    assert all("session_id" not in item for item in events)
    assert len({item["operation_id"] for item in events}) == 1
    assert events[1]["detail"]["exit_code"] == 0

    events.clear()
    # A *configured* remote whose URL does not exist: the failure comes
    # from git, not from remote-name validation.
    git(repo, "remote", "add", "broken", str(workspace_root / "missing.git"))
    with pytest.raises(GitOperationFailed):
        await manager.fetch(
            "environment-primary", "app", remote="broken", progress=emit
        )
    assert [item["phase"] for item in events] == ["started", "failed"]
    assert {item["environment_id"] for item in events} == {"environment-primary"}
    assert all("session_id" not in item for item in events)
    assert events[1]["detail"]["error_kind"] == "git_operation_failed"


@pytest.mark.asyncio
async def test_progress_payload_is_addressed_to_the_environment():
    events: List[Dict[str, Any]] = []

    async def emit(payload: Dict[str, Any]) -> None:
        events.append(payload)

    async with _ProgressScope(emit, "fetch", "app", "environment-primary") as report:
        await report({"objects": 3})

    assert [event["phase"] for event in events] == ["started", "completed"]
    assert events[-1]["detail"] == {"objects": 3}
    assert {event["environment_id"] for event in events} == {"environment-primary"}
    assert all("session_id" not in event for event in events)


@pytest.mark.asyncio
async def test_a_failing_git_command_carries_the_exit_code_not_prose(
    repo: Path, manager: GitManager
):
    with pytest.raises(GitOperationFailed) as excinfo:
        await manager.checkout("s1", "app", branch="no-such-branch")
    data = excinfo.value.data
    assert data["kind"] == "git_operation_failed"
    assert data["operation"] == "checkout"
    assert data["exit_code"] != 0
    assert "stderr" in data


class _RecordingWorkspace(HostWorkspace):
    """A host workspace that remembers how every command was invoked."""

    def __init__(self, root: str) -> None:
        super().__init__(root=root)
        self.exec_kwargs: List[Dict[str, Any]] = []

    async def exec(self, command, **kwargs):  # type: ignore[override]
        self.exec_kwargs.append({"command": command, **kwargs})
        return await super().exec(command, **kwargs)


@pytest.mark.asyncio
async def test_every_git_command_is_argv_with_no_cwd(repo: Path):
    """The invocation shape a sandbox substrate requires (tkt_k5Yk_JBkrIw6).

    ``SandboxWorkspace.exec`` refuses ``cwd=`` together with
    ``shell=False`` and has no shell to interpolate into, so ``git.py``
    addresses repositories with ``git -C`` and passes an argv list. On the
    host both spellings work identically, which is exactly why this can
    regress unnoticed: without this assertion the only thing that would
    catch a stray ``cwd=`` or ``shell=True`` is
    ``test_git_sandbox_substrate.py``, which needs Docker.
    """
    workspace = _RecordingWorkspace(root=str(repo.parent))
    manager = make_manager(workspace)
    write(repo / "f.txt", two_hunk_edit())

    await manager.list_repositories("s1")
    await manager.status("s1", "app")
    diff = await manager.diff("s1", "app")
    await manager.stage(
        "s1",
        "app",
        hunks=[{"path": "f.txt", "hunk_id": diff["files"][0]["hunks"][0]["hunk_id"]}],
    )
    await manager.commit("s1", "app", "from a recorded run")
    await manager.log("s1", "app")

    assert workspace.exec_kwargs, "no command reached the workspace boundary"
    for call in workspace.exec_kwargs:
        assert call.get("shell") is False, call
        assert call.get("cwd") is None, call
        assert isinstance(call["command"], (list, tuple)), call
        assert call["command"][0] == "git"
        # ``git -C`` is how the repository is addressed; a bare `git` with
        # an implied working directory would operate on the wrong tree.
        assert call["command"][1] == "-C", call


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_workspace(repo: Path, manager: GitManager):
    write(repo / "f.txt", two_hunk_edit())
    results = await asyncio.gather(
        manager.status("s1", "app"),
        manager.diff("s1", "app"),
        manager.log("s1", "app"),
        manager.list_branches("s1", "app"),
    )
    assert results[0]["repo"] == "app"
    assert results[1]["files"][0]["path"] == "f.txt"
    assert results[2]["commits"][0]["subject"] == "init"
    assert [item["name"] for item in results[3]["branches"]] == ["main"]


# ---------------------------------------------------------------------------
#  Review round: repository confinement (D1, D8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_git_file_pointing_outside_the_workspace_is_refused(tmp_path: Path):
    """A ``.git`` *file* can redirect a checkout's entire object store.

    ``gitdir: /outside/.git`` leaves ``--show-toplevel`` inside the
    workspace while objects, refs, index and config all come from
    outside it — a read escape (log/diff), an exfiltration primitive
    (checkout materialises outside content inside), and a write escape
    (commit/reset/push rewrite the outside repository).
    """
    outside = init_repo(tmp_path / "outside")
    write(outside / "secret.txt", "TOP SECRET\n")
    git(outside, "add", "-A")
    git(outside, "commit", "-qm", "outside work")
    outside_head = git(outside, "rev-parse", "HEAD").strip()

    workspace_root = tmp_path / "workspace"
    (workspace_root / "sneaky").mkdir(parents=True)
    (workspace_root / "sneaky" / ".git").write_text(f"gitdir: {outside}/.git\n")

    manager = make_manager(HostWorkspace(root=str(workspace_root)))
    for call in (
        lambda: manager.status("s1", "sneaky"),
        lambda: manager.log("s1", "sneaky"),
        lambda: manager.diff("s1", "sneaky"),
        lambda: manager.list_branches("s1", "sneaky"),
        lambda: manager.commit("s1", "sneaky", "hijack"),
        lambda: manager.reset("s1", "sneaky", mode="soft"),
        lambda: manager.checkout("s1", "sneaky", branch="main"),
    ):
        with pytest.raises(GitRepositoryNotFound) as excinfo:
            await call()
        assert excinfo.value.data["reason"] == "outside_workspace"

    # Discovery does not offer it either, and the outside repository is
    # byte-for-byte untouched.
    listing = await manager.list_repositories("s1")
    assert listing["repositories"] == []
    assert git(outside, "rev-parse", "HEAD").strip() == outside_head
    assert (outside / "secret.txt").read_text() == "TOP SECRET\n"
    assert not (workspace_root / "sneaky" / "secret.txt").exists()


@pytest.mark.asyncio
async def test_a_linked_worktree_inside_the_workspace_still_works(
    repo: Path, workspace_root: Path, manager: GitManager
):
    """The git-dir check must not break legitimate linked worktrees.

    ``app-review`` has a ``.git`` *file* too, but its git dir lives under
    ``app/.git/worktrees`` — inside the workspace — so it stays operable.
    """
    inside = workspace_root / "app-review"
    git(repo, "worktree", "add", "-q", "-b", "review", str(inside), "HEAD")
    status = await manager.status("s1", "app-review")
    assert status["head"]["branch"] == "review"
    write(inside / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app-review")
    assert diff["files"][0]["hunks"]
    await manager.stage(
        "s1",
        "app-review",
        hunks=[{"path": "f.txt", "hunk_id": diff["files"][0]["hunks"][0]["hunk_id"]}],
    )
    assert "CHANGED-TOP" in git(inside, "diff", "--cached")


@pytest.mark.asyncio
async def test_a_symlinked_repository_is_refused_even_when_unconfined(tmp_path: Path):
    """The boundary holds without ``HostWorkspace.confine``.

    Some embedders construct unconfined host workspaces, so the git
    surface asserts the invariant itself instead of inheriting it.
    """
    outside = init_repo(tmp_path / "outside")
    write(outside / "a.txt", "a\n")
    git(outside, "add", "-A")
    git(outside, "commit", "-qm", "init")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    os.symlink(outside, workspace_root / "linked")

    manager = make_manager(HostWorkspace(root=str(workspace_root)))
    with pytest.raises(GitPathError) as excinfo:
        await manager.status("s1", "linked")
    assert excinfo.value.data["reason"] == "escapes_workspace"


@pytest.mark.asyncio
async def test_a_bare_repository_has_no_operable_work_tree(
    workspace_root: Path, manager: GitManager
):
    subprocess.run(
        ["git", "init", "-q", "--bare", str(workspace_root / "bare.git")],
        check=True,
        capture_output=True,
    )
    with pytest.raises(GitRepositoryNotFound) as excinfo:
        await manager.status("s1", "bare.git")
    assert excinfo.value.data["reason"] == "no_work_tree"


# ---------------------------------------------------------------------------
#  Review round: large patches and rollback (D2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_patch_far_larger_than_the_argv_limit_applies(
    repo: Path, manager: GitManager
):
    """Regression: patches used to ride a heredoc in a single argv string.

    Linux caps one argument at ``MAX_ARG_STRLEN`` (128 KiB), so an
    ordinary large diff failed with an untyped ``OSError`` *and* skipped
    rollback. The patch is now written to a scratch file instead, so size
    is irrelevant.
    """
    original = "".join(f"original line {index}\n" for index in range(20000))
    write(repo / "big.txt", original)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "big")
    # One contiguous rewrite → a single hunk, ~500 KiB of patch text.
    write(
        repo / "big.txt", "".join(f"rewritten line {index}\n" for index in range(20000))
    )

    diff = await manager.diff("s1", "app", context_lines=0)
    (diff_file,) = diff["files"]
    assert len(diff_file["hunks"]) == 1
    from omniagents.rpc.git import build_patch

    files = await manager._collect_diff(
        HostWorkspace(root=str(repo.parent)),
        "app",
        str(repo),
        scope="worktree",
        context_lines=0,
    )
    patch = build_patch([(files[0], files[0].hunks)], direction="forward")
    assert len(patch) > 256 * 1024, f"patch is only {len(patch)} bytes"

    result = await manager.stage(
        "s1",
        "app",
        hunks=[{"path": "big.txt", "hunk_id": diff_file["hunks"][0]["hunk_id"]}],
        context_lines=0,
    )
    assert result["staged_hunks"]
    assert "rewritten line 0" in git(repo, "diff", "--cached")
    assert git(repo, "diff").strip() == ""


@pytest.mark.asyncio
async def test_a_non_git_failure_between_steps_still_rolls_back(
    repo: Path, manager: GitManager, monkeypatch
):
    """``_atomic`` must not only catch its own typed errors.

    An OS-level failure (the old argv-limit ``OSError`` was exactly this)
    used to escape the rollback handler and strand a half-updated index.
    """
    write(repo / "g.txt", NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add g")
    write(repo / "f.txt", "new content\n")
    write(repo / "g.txt", two_hunk_edit())

    diff = await manager.diff("s1", "app")
    g_hunk = next(item for item in diff["files"] if item["path"] == "g.txt")["hunks"][0]

    async def exploding_apply(*args: Any, **kwargs: Any) -> None:
        raise OSError(7, "Argument list too long")

    monkeypatch.setattr(GitManager, "_apply_patch", exploding_apply)

    with pytest.raises(GitOperationFailed) as excinfo:
        await manager.stage(
            "s1",
            "app",
            paths=["f.txt"],
            hunks=[{"path": "g.txt", "hunk_id": g_hunk["hunk_id"]}],
        )
    assert excinfo.value.data["rolled_back"] is True
    assert excinfo.value.data["reason"] == "step_failed"
    assert git(repo, "diff", "--cached").strip() == ""


@pytest.mark.asyncio
async def test_the_scratch_patch_never_shows_up_as_a_workspace_change(
    repo: Path, manager: GitManager
):
    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app")
    await manager.stage(
        "s1",
        "app",
        hunks=[{"path": "f.txt", "hunk_id": diff["files"][0]["hunks"][0]["hunk_id"]}],
    )
    status = await manager.status("s1", "app", include_untracked=True)
    assert status["untracked"] == []
    assert not list((repo / ".git").glob("omniagents-apply-*.patch"))


# ---------------------------------------------------------------------------
#  Review round: type changes (D3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_type_change_does_not_break_the_rest_of_the_diff(
    repo: Path, manager: GitManager
):
    """git renders one ``T`` raw record as *two* patch blocks.

    Consuming one would shift every later file onto the wrong hunks; the
    whole repository used to become undiffable.
    """
    write(repo / "other.txt", NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "other")
    (repo / "f.txt").unlink()
    os.symlink("/etc/hostname", repo / "f.txt")
    write(repo / "other.txt", two_hunk_edit())

    diff = await manager.diff("s1", "app")
    by_path = {item["path"]: item for item in diff["files"]}
    assert set(by_path) == {"f.txt", "other.txt"}
    assert by_path["f.txt"]["change"] == "type_changed"
    assert by_path["f.txt"]["hunk_selectable"] is False
    # The unrelated file keeps its real, selectable hunks.
    assert len(by_path["other.txt"]["hunks"]) == 2
    assert by_path["other.txt"]["hunk_selectable"] is True

    await manager.stage(
        "s1",
        "app",
        hunks=[
            {
                "path": "other.txt",
                "hunk_id": by_path["other.txt"]["hunks"][0]["hunk_id"],
            }
        ],
    )
    assert "CHANGED-TOP" in git(repo, "diff", "--cached")


@pytest.mark.asyncio
async def test_a_type_change_back_to_a_regular_file(repo: Path, manager: GitManager):
    (repo / "link").symlink_to("f.txt")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "link")
    (repo / "link").unlink()
    write(repo / "link", "now a real file\n")

    diff = await manager.diff("s1", "app")
    (diff_file,) = diff["files"]
    assert diff_file["change"] == "type_changed"
    assert diff_file["hunk_selectable"] is False
    # Both halves of the change are still visible to a renderer.
    assert len(diff_file["hunks"]) == 2

    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage(
            "s1",
            "app",
            hunks=[{"path": "link", "hunk_id": diff_file["hunks"][0]["hunk_id"]}],
        )
    assert excinfo.value.data["reason"] == "hunk_selection_unsupported"

    # File-level staging of a type change works.
    await manager.stage("s1", "app", paths=["link"])
    assert "link" in git(repo, "diff", "--cached", "--name-only")


# ---------------------------------------------------------------------------
#  Review round: confirmation impacts pin content (D4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hard_reset_token_dies_when_the_content_changes(
    repo: Path, manager: GitManager
):
    """Same paths, different bytes: the old token must not redeem.

    A parallel agent column rewriting the file between the prompt and the
    confirmation would otherwise destroy work the user never saw.
    """
    write(repo / "f.txt", two_hunk_edit())
    with pytest.raises(GitConfirmationRequired) as first:
        await manager.reset("s1", "app", mode="hard")
    token = first.value.data["confirmation_token"]
    assert first.value.data["impact"]["dirty_paths"] == ["f.txt"]

    write(repo / "f.txt", NUMBERED.replace("line2", "DIFFERENT WORK"))
    with pytest.raises(GitConfirmationRequired) as second:
        await manager.reset("s1", "app", mode="hard", confirmation_token=token)
    assert second.value.data["reason"] == "stale_confirmation"
    # The path list is identical, so only the content digest can tell.
    assert second.value.data["impact"]["dirty_paths"] == ["f.txt"]
    assert (
        second.value.data["impact"]["content_digest"]
        != first.value.data["impact"]["content_digest"]
    )
    assert "DIFFERENT WORK" in (repo / "f.txt").read_text()


@pytest.mark.asyncio
async def test_a_discarding_checkout_token_dies_when_the_content_changes(
    repo: Path, manager: GitManager
):
    git(repo, "branch", "other")
    write(repo / "f.txt", two_hunk_edit())
    with pytest.raises(GitConfirmationRequired) as first:
        await manager.checkout("s1", "app", branch="other", discard_changes=True)
    token = first.value.data["confirmation_token"]

    write(repo / "f.txt", NUMBERED.replace("line2", "DIFFERENT WORK"))
    with pytest.raises(GitConfirmationRequired) as second:
        await manager.checkout(
            "s1",
            "app",
            branch="other",
            discard_changes=True,
            confirmation_token=token,
        )
    assert second.value.data["reason"] == "stale_confirmation"
    assert second.value.data["impact"]["dirty_paths"] == ["f.txt"]
    assert "DIFFERENT WORK" in (repo / "f.txt").read_text()


# ---------------------------------------------------------------------------
#  Review round: destructive pushes hide in the refspec (D5 / item 1)
# ---------------------------------------------------------------------------


def test_parse_refspec_intent():
    assert parse_refspec_intent(None) == {
        "force": False,
        "delete": False,
        "target": None,
    }
    assert parse_refspec_intent("main:main") == {
        "force": False,
        "delete": False,
        "target": "main",
    }
    assert parse_refspec_intent("+main:main") == {
        "force": True,
        "delete": False,
        "target": "main",
    }
    assert parse_refspec_intent(":refs/heads/release") == {
        "force": False,
        "delete": True,
        "target": "refs/heads/release",
    }
    assert parse_refspec_intent("+:refs/heads/release")["delete"] is True


@pytest.fixture
def repo_with_remote(repo: Path, workspace_root: Path):
    remote = workspace_root / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


@pytest.mark.asyncio
async def test_a_forcing_refspec_is_gated_like_the_force_flag(
    repo_with_remote, manager: GitManager
):
    repo, remote = repo_with_remote
    await manager.push("s1", "app", remote="origin", refspec="main:main")
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "commit", "-qam", "published")
    await manager.push("s1", "app", remote="origin", refspec="main:main")
    before = git(remote, "rev-parse", "refs/heads/main").strip()
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    write(repo / "f.txt", "divergent\n")
    git(repo, "commit", "-qam", "divergent")

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.push("s1", "app", remote="origin", refspec="+main:main")
    assert excinfo.value.data["operation"] == "force_push"
    assert excinfo.value.data["impact"]["force"] is True
    assert git(remote, "rev-parse", "refs/heads/main").strip() == before

    forced = await manager.push(
        "s1",
        "app",
        remote="origin",
        refspec="+main:main",
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert forced["updates"][0]["status"] == "forced_update"


@pytest.mark.asyncio
async def test_a_deleting_refspec_requires_confirmation(
    repo_with_remote, manager: GitManager
):
    repo, remote = repo_with_remote
    await manager.push("s1", "app", remote="origin", refspec="main:refs/heads/release")
    assert git(remote, "rev-parse", "refs/heads/release").strip()

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.push("s1", "app", remote="origin", refspec=":refs/heads/release")
    assert excinfo.value.data["operation"] == "delete_remote_ref"
    assert excinfo.value.data["impact"]["delete"] is True
    assert excinfo.value.data["impact"]["target_ref"] == "refs/heads/release"
    # Still there: the refusal really refused.
    assert git(remote, "rev-parse", "refs/heads/release").strip()

    deleted = await manager.push(
        "s1",
        "app",
        remote="origin",
        refspec=":refs/heads/release",
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert deleted["updates"][0]["status"] == "deleted"
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/release"],
        cwd=str(remote),
        capture_output=True,
    )
    assert probe.returncode != 0


@pytest.mark.asyncio
async def test_force_with_lease_is_not_downgraded_to_force(
    repo_with_remote, manager: GitManager
):
    """``--force-with-lease`` must actually lease.

    The remote moves behind our back; a plain force would overwrite it,
    a lease must refuse.
    """
    repo, remote = repo_with_remote
    await manager.push("s1", "app", remote="origin", refspec="main:main")
    await manager.fetch("s1", "app", remote="origin")

    other = repo.parent.parent / "other"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True
    )
    git(other, "config", "user.email", "o@example.com")
    git(other, "config", "user.name", "Other")
    write(other / "f.txt", "remote moved\n")
    git(other, "commit", "-qam", "remote work")
    git(other, "push", "-q", "origin", "main")
    remote_head = git(remote, "rev-parse", "refs/heads/main").strip()

    write(repo / "f.txt", "local work\n")
    git(repo, "commit", "-qam", "local work")

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.push(
            "s1", "app", remote="origin", refspec="main:main", force_with_lease=True
        )
    leased = await manager.push(
        "s1",
        "app",
        remote="origin",
        refspec="main:main",
        force_with_lease=True,
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert leased["ok"] is False
    assert leased["updates"][0]["status"] == "rejected"
    assert git(remote, "rev-parse", "refs/heads/main").strip() == remote_head

    # A plain force, by contrast, does overwrite it.
    with pytest.raises(GitConfirmationRequired) as forced_confirm:
        await manager.push(
            "s1", "app", remote="origin", refspec="main:main", force=True
        )
    forced = await manager.push(
        "s1",
        "app",
        remote="origin",
        refspec="main:main",
        force=True,
        confirmation_token=forced_confirm.value.data["confirmation_token"],
    )
    assert forced["ok"] is True
    assert git(remote, "rev-parse", "refs/heads/main").strip() != remote_head


# ---------------------------------------------------------------------------
#  Review round: remotes are names, never URLs (D6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_remote_url_is_rejected(
    repo_with_remote, tmp_path, manager: GitManager
):
    repo, _ = repo_with_remote
    outside = init_repo(tmp_path / "outside")
    write(outside / "secret.txt", "TOP SECRET\n")
    git(outside, "add", "-A")
    git(outside, "commit", "-qm", "outside")

    for candidate in (
        str(outside),
        f"file://{outside}",
        "https://example.invalid/x.git",
    ):
        with pytest.raises(GitStateInvalid) as excinfo:
            await manager.fetch("s1", "app", remote=candidate)
        assert excinfo.value.data["reason"] == "unknown_remote"
        assert excinfo.value.data["supported"] == ["origin"]
    # Nothing from outside made it into the repository.
    assert "TOP SECRET" not in git(repo, "log", "--all", "-p")


@pytest.mark.asyncio
async def test_ext_urls_stay_blocked_even_with_repo_local_config(
    repo_with_remote, tmp_path, manager: GitManager
):
    """``ext::`` turns a remote URL into a shell command.

    The repository config is writable by code in the environment, so the block has to
    come from the command line, which outranks it.
    """
    repo, _ = repo_with_remote
    marker = tmp_path / "pwned"
    git(repo, "config", "protocol.ext.allow", "always")
    git(repo, "remote", "add", "evil", f"ext::sh -c touch% {marker}")

    with pytest.raises(GitOperationFailed):
        await manager.fetch("s1", "app", remote="evil")
    assert not marker.exists()


@pytest.mark.asyncio
async def test_a_refspec_without_a_remote_is_refused(
    repo_with_remote, manager: GitManager
):
    """``git fetch <x>`` reads its first positional as the repository."""
    for call in (manager.fetch, manager.pull, manager.push):
        with pytest.raises(GitStateInvalid) as excinfo:
            await call("s1", "app", refspec="main:main")
        assert excinfo.value.data["reason"] == "missing_remote"


# ---------------------------------------------------------------------------
#  Review round: serialization (D7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_hunk_staging_lands_every_change(
    repo: Path, manager: GitManager
):
    """A deck of parallel columns staging at once must not lose work.

    Unserialized, git's ``index.lock`` rejected several of these every
    run: correct (nothing corrupted) but useless.
    """
    names = [f"file{index}.txt" for index in range(12)]
    for name in names:
        write(repo / name, NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "many files")
    for name in names:
        write(repo / name, two_hunk_edit())

    diff = await manager.diff("s1", "app")
    by_path = {item["path"]: item for item in diff["files"]}
    results = await asyncio.gather(
        *[
            manager.stage(
                "s1",
                "app",
                hunks=[{"path": name, "hunk_id": by_path[name]["hunks"][0]["hunk_id"]}],
            )
            for name in names
        ]
    )
    assert len(results) == len(names)
    staged = git(repo, "diff", "--cached", "--name-only").split()
    assert sorted(staged) == sorted(names)
    cached = git(repo, "diff", "--cached")
    assert cached.count("CHANGED-TOP") == len(names)
    assert "CHANGED-BOTTOM" not in cached


@pytest.mark.asyncio
async def test_external_index_contention_is_reported_structurally(
    repo: Path, manager: GitManager
):
    """Contention from *outside* the process is classified, not parsed."""
    (repo / ".git" / "index.lock").write_text("")
    try:
        with pytest.raises(GitOperationFailed) as excinfo:
            await manager.stage("s1", "app", paths=["f.txt"])
        assert excinfo.value.data["reason"] == "index_locked"
    finally:
        (repo / ".git" / "index.lock").unlink()


# ---------------------------------------------------------------------------
#  Review round: parameters that used to be ignored (item 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inapplicable_parameters_are_typed_errors(
    repo: Path, manager: GitManager
):
    with pytest.raises(GitStateInvalid) as revs:
        await manager.diff("s1", "app", mode="worktree", from_rev="HEAD~1")
    assert revs.value.data["reason"] == "parameter_not_applicable"

    with pytest.raises(GitStateInvalid) as start:
        await manager.checkout("s1", "app", branch="main", start_point="HEAD")
    assert start.value.data["reason"] == "parameter_not_applicable"

    with pytest.raises(GitStateInvalid) as conflict:
        await manager.checkout("s1", "app", branch="main", create=True, detach=True)
    assert conflict.value.data["reason"] == "parameter_conflict"


@pytest.mark.asyncio
async def test_detach_and_start_point_reach_their_states(
    repo: Path, manager: GitManager
):
    """``detach`` is the only client-reachable route to a detached HEAD."""
    base = git(repo, "rev-parse", "HEAD").strip()
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "commit", "-qam", "second")

    second = git(repo, "rev-parse", "HEAD").strip()
    assert second != base

    detached = await manager.checkout("s1", "app", branch=base, detach=True)
    assert detached["head"]["detached"] is True
    assert detached["head"]["branch"] is None
    assert detached["head"]["oid"] == base

    # Back onto a branch whose tip is NOT ``base``, so the next assertion
    # can only hold if start_point was actually used — creating from the
    # current HEAD would land on ``second``.
    back = await manager.checkout("s1", "app", branch="main")
    assert back["head"]["oid"] == second

    created = await manager.checkout(
        "s1", "app", branch="from-base", create=True, start_point=base
    )
    assert created["head"]["branch"] == "from-base"
    assert created["head"]["oid"] == base
    assert git(repo, "rev-parse", "HEAD").strip() == base


@pytest.mark.asyncio
async def test_pathspecs_actually_filter_every_read(repo: Path, manager: GitManager):
    write(repo / "second.txt", NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "second file")
    write(repo / "f.txt", two_hunk_edit())
    write(repo / "second.txt", two_hunk_edit())

    status = await manager.status("s1", "app", paths=["f.txt"])
    assert [entry["path"] for entry in status["entries"]] == ["f.txt"]
    diff = await manager.diff("s1", "app", paths=["second.txt"])
    assert [item["path"] for item in diff["files"]] == ["second.txt"]
    log = await manager.log("s1", "app", paths=["second.txt"])
    assert [commit["subject"] for commit in log["commits"]] == ["second file"]


@pytest.mark.asyncio
async def test_commit_never_runs_repository_supplied_hooks(
    repo: Path, manager: GitManager
):
    """Hooks are repository-supplied code; an RPC must not execute them."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    marker = repo.parent / "hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)

    write(repo / "f.txt", two_hunk_edit())
    await manager.stage("s1", "app", paths=["f.txt"])
    committed = await manager.commit("s1", "app", "hooks stay out of it")
    assert committed["oid"] == git(repo, "rev-parse", "HEAD").strip()
    assert not marker.exists()


@pytest.mark.asyncio
async def test_submodule_pointers_are_detected_and_not_hunk_selectable(
    repo: Path, workspace_root: Path, manager: GitManager
):
    inner = init_repo(workspace_root / "inner")
    write(inner / "a.txt", "a\n")
    git(inner, "add", "-A")
    git(inner, "commit", "-qm", "inner")
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(inner),
            "sub",
        ],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    git(repo, "commit", "-qm", "add submodule")
    write(inner / "a.txt", "changed\n")
    git(inner, "commit", "-qam", "inner moved")
    subprocess.run(
        ["git", "-C", "sub", "pull", "-q", "--no-rebase"],
        cwd=str(repo),
        check=False,
        capture_output=True,
    )

    diff = await manager.diff("s1", "app")
    entry = next(item for item in diff["files"] if item["path"] == "sub")
    assert entry["submodule"] is True
    assert entry["hunk_selectable"] is False


# ---------------------------------------------------------------------------
#  Review round: clamps are visible (item 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clamped_parameters_are_reported(repo: Path, manager: GitManager):
    listing = await manager.list_repositories("s1", max_depth=99)
    assert listing["max_depth"] == 4
    assert listing["max_depth_clamped"] is True
    assert listing["truncated"] is True

    honest = await manager.list_repositories("s1", max_depth=2)
    assert honest["max_depth"] == 2
    assert honest["max_depth_clamped"] is False
    assert honest["truncated"] is False

    write(repo / "f.txt", two_hunk_edit())
    diff = await manager.diff("s1", "app", context_lines=9999)
    assert diff["context_lines"] == 64
    assert diff["context_lines_clamped"] is True

    log = await manager.log("s1", "app", max_count=10_000)
    assert log["max_count"] == 1000
    assert log["truncated"] is True


# ---------------------------------------------------------------------------
#  Review round: conflicts carry content (item 8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_stages_carry_content_for_a_three_way_editor(
    workspace_root: Path, manager: GitManager
):
    """Under git's default ``merge`` style the file has no base section.

    The stage blobs supply it, so base/ours/theirs are all available
    without a separate blob-read RPC.
    """
    make_conflict(workspace_root / "conflict")
    conflicts = await manager.conflicts("s1", "conflict")
    (conflict,) = conflicts["conflicts"]
    assert conflict["regions"][0]["base"] is None
    assert conflict["stages"]["base"]["content"] == "l1\nl2\nl3\n"
    assert conflict["stages"]["ours"]["content"] == "l1\nMAIN\nl3\n"
    assert conflict["stages"]["theirs"]["content"] == "l1\nFEATURE\nl3\n"
    assert conflict["stages"]["ours"]["content_truncated"] is False
    assert conflicts["content_truncated"] is False


@pytest.mark.asyncio
async def test_oversized_conflict_stages_are_flagged_not_truncated_silently(
    workspace_root: Path,
):
    target = init_repo(workspace_root / "conflict")
    big = "".join(f"line {index}\n" for index in range(4000))
    write(target / "c.txt", big)
    git(target, "add", "-A")
    git(target, "commit", "-qm", "base")
    git(target, "checkout", "-q", "-b", "feature")
    write(target / "c.txt", big.replace("line 0\n", "FEATURE\n"))
    git(target, "commit", "-qam", "feature")
    git(target, "checkout", "-q", "main")
    write(target / "c.txt", big.replace("line 0\n", "MAIN\n"))
    git(target, "commit", "-qam", "main")
    git(target, "merge", "feature", check=False)

    manager = make_manager(
        HostWorkspace(root=str(workspace_root)), max_conflict_blob_bytes=64
    )
    conflicts = await manager.conflicts("s1", "conflict")
    (conflict,) = conflicts["conflicts"]
    assert conflict["stages"]["ours"]["content"] is None
    assert conflict["stages"]["ours"]["content_truncated"] is True


# ---------------------------------------------------------------------------
#  Review round: head-scope staging (item 9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hunks_from_the_head_view_can_be_staged(repo: Path, manager: GitManager):
    """``mode:"head"`` is the "all my changes" view a UI opens on."""
    write(repo / "f.txt", two_hunk_edit())
    head_diff = await manager.diff("s1", "app", mode="head")
    hunks = head_diff["files"][0]["hunks"]
    assert len(hunks) == 2

    # Without saying which view they came from, the ids do not resolve —
    # and the error says exactly that.
    with pytest.raises(GitStateInvalid) as excinfo:
        await manager.stage(
            "s1", "app", hunks=[{"path": "f.txt", "hunk_id": hunks[0]["hunk_id"]}]
        )
    assert excinfo.value.data["reason"] == "hunk_not_found"
    assert excinfo.value.data["scope"] == "worktree"

    await manager.stage(
        "s1",
        "app",
        mode="head",
        hunks=[{"path": "f.txt", "hunk_id": hunks[0]["hunk_id"]}],
    )
    staged = git(repo, "diff", "--cached")
    assert "CHANGED-TOP" in staged and "CHANGED-BOTTOM" not in staged

    with pytest.raises(GitStateInvalid) as bad_mode:
        await manager.stage(
            "s1",
            "app",
            mode="staged",
            hunks=[{"path": "f.txt", "hunk_id": hunks[1]["hunk_id"]}],
        )
    assert bad_mode.value.data["reason"] == "unknown_mode"
    assert bad_mode.value.data["supported"] == ["worktree", "head"]


# ---------------------------------------------------------------------------
#  Review round: published project sources (item 2b)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_published_sources_are_labelled_and_gaps_are_visible(
    workspace_root: Path,
):
    for name in ("frontend", "backend"):
        target = init_repo(workspace_root / name)
        write(target / "a.txt", "a\n")
        git(target, "add", "-A")
        git(target, "commit", "-qm", "init")

    sources = [
        {"kind": "local-git", "mountName": "frontend", "repoUrl": "git@x:/frontend"},
        {"kind": "local-git", "mountName": "elsewhere", "path": "/opt/elsewhere"},
    ]

    async def source_provider(environment_id: str):
        assert environment_id == "s1"
        return sources

    manager = make_manager(
        HostWorkspace(root=str(workspace_root)), source_provider=source_provider
    )
    listing = await manager.list_repositories("s1")
    by_repo = {item["repo"]: item for item in listing["repositories"]}
    assert by_repo["frontend"]["source"]["mountName"] == "frontend"
    # A repository that is not a published source is labelled as such.
    assert by_repo["backend"]["source"] is None
    # A published source outside the workspace is reported, not hidden.
    assert listing["unreachable_sources"] == [
        {
            "mount_name": "elsewhere",
            "kind": "local-git",
            "path": "/opt/elsewhere",
            "repo_url": None,
            "reason": "not_in_workspace",
        }
    ]


@pytest.mark.asyncio
async def test_plain_local_source_is_not_misreported_as_outside_workspace(
    workspace_root: Path,
):
    sources = [
        {
            "id": "scratch-source",
            "kind": "local",
            "mountName": "scratch",
            "path": str(workspace_root),
        }
    ]

    async def source_provider(_environment_id: str):
        return sources

    listing = await make_manager(
        HostWorkspace(root=str(workspace_root)), source_provider=source_provider
    ).list_repositories("host-chat")

    assert listing["repositories"] == []
    assert listing["unreachable_sources"] == []
    assert listing["sources"][0]["id"] == "scratch-source"


@pytest.mark.asyncio
async def test_materialized_devbox_source_is_not_inferred_unreachable_from_git_discovery():
    class DevboxWorkspace:
        root = "/workspace"

        async def list_dir(self, path=".", **_kwargs):
            if path == ".":
                return [
                    SimpleNamespace(
                        name="frontend",
                        path="frontend",
                        is_dir=True,
                        is_symlink=False,
                    )
                ]
            return []

        async def is_dir(self, path):
            return path in {".", "frontend"}

        def contains_path(self, path):
            return path == "/workspace" or path.startswith("/workspace/")

    source = {
        "id": "source-frontend",
        "kind": "git-remote",
        "mountName": "frontend",
        "repoUrl": "https://example.com/frontend.git",
    }

    async def source_provider(_environment_id: str):
        return [source]

    listing = await make_manager(
        DevboxWorkspace(), source_provider=source_provider
    ).list_repositories("devbox-environment")

    assert listing["repositories"] == []
    assert listing["unreachable_sources"] == []


@pytest.mark.asyncio
async def test_mounted_host_sources_are_discovered_by_logical_repo_id(tmp_path: Path):
    layout = tmp_path / "layout"
    sources = []
    for name in ("frontend", "backend"):
        target = init_repo(tmp_path / name)
        write(target / "identity.txt", name)
        git(target, "add", "-A")
        git(target, "commit", "-qm", "init")
        sources.append(
            {
                "id": f"source-{name}",
                "kind": "local-git",
                "mountName": name,
                "path": str(target),
                "gitDir": git(target, "rev-parse", "--absolute-git-dir").strip(),
                "gitCommonDir": git(
                    target, "rev-parse", "--path-format=absolute", "--git-common-dir"
                ).strip(),
            }
        )
    workspace = MountedHostWorkspace(str(layout), sources)

    async def source_provider(_environment_id: str):
        return sources

    listing = await make_manager(
        workspace, source_provider=source_provider
    ).list_repositories("host-environment")

    assert [repository["repo"] for repository in listing["repositories"]] == [
        "backend",
        "frontend",
    ]
    assert listing["unreachable_sources"] == []
    assert {repository["source"]["id"] for repository in listing["repositories"]} == {
        "source-backend",
        "source-frontend",
    }
    assert all(
        "gitDir" not in repository["source"]
        and "gitCommonDir" not in repository["source"]
        for repository in listing["repositories"]
    )


@pytest.mark.asyncio
async def test_mounted_host_git_worktree_authorizes_declared_external_metadata(
    tmp_path: Path,
):
    primary = init_repo(tmp_path / "primary")
    write(primary / "tracked.txt", "primary\n")
    git(primary, "add", "-A")
    git(primary, "commit", "-qm", "init")
    checkout = tmp_path / "checkout"
    git(primary, "worktree", "add", "-q", "-b", "feature", str(checkout))
    source = {
        "id": "source-checkout",
        "kind": "local-git",
        "mountName": "checkout",
        "path": str(checkout),
        "gitDir": git(checkout, "rev-parse", "--absolute-git-dir").strip(),
        "gitCommonDir": git(
            checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).strip(),
    }
    workspace = MountedHostWorkspace(str(tmp_path / "layout"), [source])

    async def source_provider(_environment_id: str):
        return [source]

    manager = make_manager(workspace, source_provider=source_provider)
    listing = await manager.list_repositories("host-environment")

    assert [repository["repo"] for repository in listing["repositories"]] == [
        "checkout"
    ]
    assert listing["unreachable_sources"] == []
    assert (await manager.status("host-environment", "checkout"))["head"][
        "branch"
    ] == "feature"
    assert not await workspace.exists(".omni-git/checkout/git-dir/config")

    write(checkout / "tracked.txt", "changed in worktree\n")
    diff = await manager.diff("host-environment", "checkout", mode="worktree")
    hunk = diff["files"][0]["hunks"][0]
    await manager.stage(
        "host-environment",
        "checkout",
        hunks=[{"path": "tracked.txt", "hunk_id": hunk["hunk_id"]}],
    )
    assert "changed in worktree" in git(checkout, "diff", "--cached")


@pytest.mark.asyncio
async def test_source_descriptors_never_echo_credentials(workspace_root: Path):
    """A launcher may publish a credential-bearing repoUrl.

    This surface echoes source descriptors back, so it must not become the
    place a token first leaves the server.
    """
    target = init_repo(workspace_root / "frontend")
    write(target / "a.txt", "a\n")
    git(target, "add", "-A")
    git(target, "commit", "-qm", "init")

    sources = [
        {
            "kind": "git-remote",
            "mountName": "frontend",
            "repoUrl": "https://user:s3cr3t@example.com/frontend.git",
        },
        {
            "kind": "git-remote",
            "mountName": "elsewhere",
            "repoUrl": "https://ghp_tokenvalue@example.com/other.git",
        },
    ]

    async def source_provider(environment_id: str):
        assert environment_id == "s1"
        return sources

    manager = make_manager(
        HostWorkspace(root=str(workspace_root)), source_provider=source_provider
    )
    listing = await manager.list_repositories("s1")
    payload = json.dumps(listing)
    assert "s3cr3t" not in payload
    assert "ghp_tokenvalue" not in payload
    assert (
        listing["repositories"][0]["source"]["repoUrl"]
        == "https://example.com/frontend.git"
    )
    assert (
        listing["unreachable_sources"][0]["repo_url"] == "https://example.com/other.git"
    )
    # The provider's own dicts are untouched — redaction is on the way out.
    assert sources[0]["repoUrl"] == "https://user:s3cr3t@example.com/frontend.git"


@pytest.mark.asyncio
async def test_source_policy_is_selected_by_explicit_environment():
    class EmptyWorkspace:
        root = "/workspace"

        async def list_dir(self, path=".", **kwargs):
            return []

        async def is_dir(self, path):
            return False

        def contains_path(self, path):
            return False

    sources_by_environment = {
        "environment-published": [
            {"kind": "local-git", "mountName": "frontend", "writable": True}
        ],
        "environment-unpublished": [],
    }

    async def source_provider(environment_id: str):
        return sources_by_environment[environment_id]

    manager = make_manager(EmptyWorkspace(), source_provider=source_provider)
    published = await manager.list_repositories("environment-published")
    unpublished = await manager.list_repositories("environment-unpublished")

    assert published["repositories"] == []
    assert published["sources"][0]["mountName"] == "frontend"
    assert published["unreachable_sources"][0]["mount_name"] == "frontend"
    assert unpublished["repositories"] == []
    assert unpublished["sources"] == []


# ---------------------------------------------------------------------------
#  Review round 2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_forcing_push_refspec_in_repository_config_is_gated(
    repo_with_remote, manager: GitManager
):
    """``remote.<name>.push`` is a refspec the *parameters* never mention.

    Repository config lives inside the workspace, so the environment can write
    it; predicting git's decision from the request alone let a forced
    update through with no token.
    """
    repo, remote = repo_with_remote
    await manager.push("s1", "app", remote="origin", refspec="main:refs/heads/rel")
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "commit", "-qam", "published")
    await manager.push("s1", "app", remote="origin", refspec="main:refs/heads/rel")
    before = git(remote, "rev-parse", "refs/heads/rel").strip()

    git(repo, "reset", "-q", "--hard", "HEAD~1")
    write(repo / "f.txt", "divergent\n")
    git(repo, "commit", "-qam", "divergent")
    # The force now lives only in config.
    git(repo, "config", "remote.origin.push", "+refs/heads/main:refs/heads/rel")

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.push("s1", "app", remote="origin")
    data = excinfo.value.data
    assert data["operation"] == "force_push"
    assert data["impact"]["force"] is True
    assert {item["target_ref"] for item in data["impact"]["planned_updates"]} == {
        "refs/heads/rel"
    }
    # Refused means refused.
    assert git(remote, "rev-parse", "refs/heads/rel").strip() == before

    forced = await manager.push(
        "s1", "app", remote="origin", confirmation_token=data["confirmation_token"]
    )
    assert forced["updates"][0]["status"] == "forced_update"
    assert git(remote, "rev-parse", "refs/heads/rel").strip() != before


@pytest.mark.asyncio
async def test_a_mirror_remote_cannot_delete_refs_without_confirmation(
    repo_with_remote, manager: GitManager
):
    """``remote.<name>.mirror`` turns a bare push into delete + force."""
    repo, remote = repo_with_remote
    await manager.push("s1", "app", remote="origin", refspec="main:refs/heads/rel")
    await manager.push("s1", "app", remote="origin", refspec="main:refs/heads/main")
    assert git(remote, "rev-parse", "refs/heads/rel").strip()

    git(repo, "config", "remote.origin.mirror", "true")
    git(repo, "config", "remote.origin.fetch", "+refs/*:refs/*")
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "commit", "-qam", "local")

    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.push("s1", "app", remote="origin")
    data = excinfo.value.data
    assert data["operation"] == "delete_remote_ref"
    assert data["impact"]["delete"] is True
    assert "refs/heads/rel" in {
        item["target_ref"] for item in data["impact"]["planned_updates"]
    }
    # Nothing was deleted by the refusal.
    assert git(remote, "rev-parse", "refs/heads/rel").strip()

    await manager.push(
        "s1", "app", remote="origin", confirmation_token=data["confirmation_token"]
    )
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/rel"],
        cwd=str(remote),
        capture_output=True,
    )
    assert probe.returncode != 0


@pytest.mark.asyncio
async def test_an_ordinary_push_still_needs_no_confirmation(
    repo_with_remote, manager: GitManager
):
    """The dry-run gate must not turn every push into a confirmation."""
    repo, remote = repo_with_remote
    created = await manager.push("s1", "app", remote="origin", refspec="main:main")
    assert created["ok"] is True
    write(repo / "f.txt", two_hunk_edit())
    git(repo, "commit", "-qam", "more")
    fast_forward = await manager.push("s1", "app", remote="origin", refspec="main:main")
    assert fast_forward["ok"] is True
    assert fast_forward["updates"][0]["status"] == "fast_forward"


def test_local_remote_path_separates_local_from_networked():
    assert local_remote_path("/abs/outside") == "/abs/outside"
    assert local_remote_path("file:///abs/outside") == "/abs/outside"
    assert local_remote_path("../sibling") == "../sibling"
    for networked in (
        "https://example.com/x.git",
        "ssh://host/x.git",
        "git://host/x.git",
        "git@host:path/x.git",
        "ext::sh -c whatever",
    ):
        assert local_remote_path(networked) is None
    assert local_remote_path("") is None


def test_redact_remote_url_drops_userinfo():
    assert redact_remote_url("https://user:tok@host/x.git") == "https://host/x.git"
    # A bare token in the userinfo is indistinguishable from a username,
    # so it goes too.
    assert (
        redact_remote_url("https://ghp_secret@github.com/o/r.git")
        == "https://github.com/o/r.git"
    )
    assert redact_remote_url("ssh://git@host/x") == "ssh://host/x"
    # scp-like form has nowhere to put a password; left intact.
    assert redact_remote_url("git@host:path/x.git") == "git@host:path/x.git"
    assert redact_remote_url("https://host/x.git") == "https://host/x.git"
    assert redact_remote_url("/local/path") == "/local/path"
    assert redact_remote_url(None) is None


@pytest.mark.asyncio
async def test_a_local_remote_outside_the_workspace_is_refused(
    repo_with_remote, tmp_path: Path, manager: GitManager
):
    """Environment-writable repo config makes the name check insufficient."""
    repo, _ = repo_with_remote
    outside = init_repo(tmp_path / "outside")
    write(outside / "secret.txt", "SECRET-VALUE\n")
    git(outside, "add", "-A")
    git(outside, "commit", "-qm", "outside")

    git(repo, "remote", "add", "leak", str(outside))
    git(repo, "remote", "add", "leak-url", f"file://{outside}")
    for name in ("leak", "leak-url"):
        with pytest.raises(GitStateInvalid) as excinfo:
            await manager.fetch("s1", "app", remote=name)
        assert excinfo.value.data["reason"] == "remote_outside_workspace"
    assert "SECRET-VALUE" not in git(repo, "log", "--all", "-p")


@pytest.mark.asyncio
async def test_a_network_remote_is_not_refused_by_the_location_check(
    repo_with_remote, manager: GitManager
):
    """The check is scoped to local paths; it must not blanket-refuse."""
    repo, _ = repo_with_remote
    git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    workspace = HostWorkspace(root=str(repo.parent))
    assert (
        await manager._resolve_remote(workspace, str(repo), "app", "upstream", None)
        == "upstream"
    )


@pytest.mark.asyncio
async def test_a_conflicted_path_that_escapes_the_workspace_stays_typed(
    workspace_root: Path, tmp_path: Path, manager: GitManager
):
    """A symlinked conflicted path must not surface as an untyped -32603.

    ``WorkspaceEscapeError`` is a ``WorkspaceError``, not an ``OSError``,
    so it slipped past the read guard and echoed the absolute workspace
    root back to the client.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET-VALUE\n")
    target = make_conflict(workspace_root / "conflict")
    (target / "c.txt").unlink()
    os.symlink(outside / "secret.txt", target / "c.txt")

    conflicts = await manager.conflicts("s1", "conflict")
    (conflict,) = conflicts["conflicts"]
    # Stage identities and contents still come from git's object store…
    assert set(conflict["stages"]) == {"base", "ours", "theirs"}
    # …but the escaping working file yields no regions, and no leak.
    assert conflict["regions"] == []
    assert conflict["regions_available"] is False
    assert "SECRET-VALUE" not in json.dumps(conflicts)
    assert str(workspace_root) not in json.dumps(conflicts)


@pytest.mark.asyncio
async def test_the_lock_registry_does_not_grow_with_wire_input(
    repo: Path, manager: GitManager
):
    """The lock key comes from the resolved repository, never the request."""
    for index in range(50):
        with pytest.raises(GitRpcError):
            await manager.stage("s1", f"no-such-repo-{index}", paths=["f.txt"])
    assert manager._repo_locks == {}

    write(repo / "f.txt", two_hunk_edit())
    await manager.stage("s1", "app", paths=["f.txt"])
    # Released locks are pruned rather than accumulating one per caller.
    assert manager._repo_locks == {}


@pytest.mark.asyncio
async def test_two_environments_sharing_one_workspace_do_not_race(
    repo: Path, workspace_root: Path
):
    """Several environments on one workspace is a supported configuration.

    Keying the lock on ``(environment, repo)`` gave them independent locks and
    they raced git's ``index.lock``; the key is the physical git dir.
    """

    async def resolver(environment_id: str):
        return HostWorkspace(root=str(workspace_root))

    manager = GitManager(workspace_resolver=resolver)
    names = [f"file{index}.txt" for index in range(24)]
    for name in names:
        write(repo / name, NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "many")
    for name in names:
        write(repo / name, two_hunk_edit())

    diff = await manager.diff("s0", "app")
    by_path = {item["path"]: item for item in diff["files"]}
    results = await asyncio.gather(
        *[
            manager.stage(
                f"environment-{index % 6}",
                "app",
                hunks=[{"path": name, "hunk_id": by_path[name]["hunks"][0]["hunk_id"]}],
            )
            for index, name in enumerate(names)
        ]
    )
    assert len(results) == len(names)
    assert sorted(git(repo, "diff", "--cached", "--name-only").split()) == sorted(names)
    # One physical repository, one lock — regardless of how many environments.
    assert manager._repo_locks == {}


def test_index_lock_failures_are_classified_from_the_path_not_the_prose():
    """The signal is the *filename*, which no locale translates."""
    from omniagents.workspace.types import ExecResult

    locked = ExecResult(
        stdout="",
        stderr=(
            "fatal: Impossible de créer '/w/app/.git/index.lock' : "
            "Le fichier existe déjà."
        ),
        exit_code=128,
        wall_time_seconds=0.0,
    )
    other = ExecResult(
        stdout="",
        stderr="fatal: pathspec 'nope' did not match",
        exit_code=1,
        wall_time_seconds=0.0,
    )
    assert GitManager._is_index_lock_failure(locked) is True
    assert GitManager._is_index_lock_failure(other) is False


@pytest.mark.asyncio
async def test_diff_include_untracked_synthesizes_added_files(
    repo: Path, manager: GitManager
):
    """Untracked files surface as whole-file additions (a read-only
    ``--no-index`` diff against /dev/null) — git itself never shows
    them, and clients want "new file, all + lines", not a stub."""
    write(repo / "brand_new.txt", "alpha\nbeta\n")
    (repo / "empty.txt").write_text("")

    # Default: unchanged behavior — untracked files stay invisible.
    plain = await manager.diff("s1", "app", mode="worktree")
    assert [f["path"] for f in plain["files"]] == []

    result = await manager.diff("s1", "app", mode="worktree", include_untracked=True)
    by_path = {f["path"]: f for f in result["files"]}
    entry = by_path["brand_new.txt"]
    assert entry["change"] == "added"
    assert entry["added_lines"] == 2
    assert entry["deleted_lines"] == 0
    origins = [line["origin"] for hunk in entry["hunks"] for line in hunk["lines"]]
    assert origins == ["add", "add"]
    # Empty untracked file: present, no hunks, zero additions.
    assert by_path["empty.txt"]["hunks"] == []
    assert by_path["empty.txt"]["added_lines"] == 0

    # Tracked modifications still coexist with the synthesized entries.
    write(repo / "f.txt", two_hunk_edit())
    combined = await manager.diff("s1", "app", mode="worktree", include_untracked=True)
    paths = [f["path"] for f in combined["files"]]
    assert "f.txt" in paths and "brand_new.txt" in paths


@pytest.mark.asyncio
async def test_diff_include_untracked_rejected_off_worktree(
    repo: Path, manager: GitManager
):
    with pytest.raises(GitStateInvalid):
        await manager.diff("s1", "app", mode="staged", include_untracked=True)
