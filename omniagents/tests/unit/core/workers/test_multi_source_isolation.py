import asyncio
import os
import subprocess

import pytest

from omniagents.core.workers import (
    IsolationError,
    MultiSourceWorktreeIsolation,
    WorkerSource,
    WorkerWorkspace,
)
from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "file.txt").write_text("content")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", "init"], check=True, env=env
    )


def _branches(repo) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "worker/*"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def scratch_workspace(tmp_path):
    token = set_workspace(
        HostWorkspace(root="/", scratch_root=str(tmp_path / "scratch"))
    )
    yield
    reset_workspace(token)


def test_multi_source_creation_branch_names_and_layout(tmp_path, scratch_workspace):
    parent = tmp_path / "workspace"
    parent.mkdir()
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_repo(repo_a)
    _init_repo(repo_b)
    plain = tmp_path / "docs"
    plain.mkdir()
    (plain / "notes.txt").write_text("notes")

    sources = [
        WorkerSource(name="repo-a", host_path=str(repo_a), git=True),
        WorkerSource(name="repo-b", host_path=str(repo_b), git=True),
        WorkerSource(name="docs", host_path=str(plain), git=False),
    ]
    strategy = MultiSourceWorktreeIsolation(lambda: sources)

    assert asyncio.run(strategy.ready(str(parent))) is True
    ws = asyncio.run(
        strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
    )

    # Shared worker root named after the parent workspace + worker id.
    assert os.path.basename(ws.root) == "workspace-worker-pip"
    # Every source mounted by name under the worker root.
    assert os.path.isfile(os.path.join(ws.root, "repo-a", "file.txt"))
    assert os.path.isfile(os.path.join(ws.root, "repo-b", "file.txt"))
    assert os.path.isfile(os.path.join(ws.root, "docs", "notes.txt"))

    # Per-source branch naming: worker/<parent>-worker-<id>/<name>.
    assert "worker/workspace-worker-pip/repo-a" in _branches(repo_a)
    assert "worker/workspace-worker-pip/repo-b" in _branches(repo_b)

    meta = ws.metadata
    assert meta["worktree_name"] == "workspace-worker-pip"
    assert meta["parent_workspace"] == str(parent)
    assert meta["worktree_branch"] == "worker/workspace-worker-pip/repo-a"
    infos = {info["name"]: info for info in meta["sources"]}
    assert infos["repo-a"]["git"] is True
    assert infos["repo-a"]["worktree_branch"] == "worker/workspace-worker-pip/repo-a"
    assert infos["repo-a"]["source_path"] == str(repo_a)
    assert infos["docs"]["git"] is False
    assert "worktree_branch" not in infos["docs"]

    preamble = strategy.task_preamble(ws)
    assert ws.root in preamble
    assert "worker branch" in preamble

    assert asyncio.run(strategy.cleanup("pip", ws)) is True


def test_rollback_on_midway_failure(tmp_path, scratch_workspace):
    parent = tmp_path / "workspace"
    parent.mkdir()
    repo_a = tmp_path / "repo-a"
    _init_repo(repo_a)
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    sources = [
        WorkerSource(name="repo-a", host_path=str(repo_a), git=True),
        WorkerSource(name="broken", host_path=str(not_a_repo), git=True),
    ]
    strategy = MultiSourceWorktreeIsolation(lambda: sources)

    with pytest.raises(IsolationError):
        asyncio.run(
            strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
        )

    # First source's worktree and branch were rolled back...
    assert "worker/workspace-worker-pip/repo-a" not in _branches(repo_a)
    worktrees = subprocess.run(
        ["git", "-C", str(repo_a), "worktree", "list"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "workspace-worker-pip" not in worktrees
    # ...and the shared worker root was removed.
    scratch = tmp_path / "scratch"
    assert not (scratch / "worktrees" / "workspace-worker-pip").exists()


def test_cleanup_from_metadata_only(tmp_path, scratch_workspace):
    parent = tmp_path / "workspace"
    parent.mkdir()
    repo_a = tmp_path / "repo-a"
    _init_repo(repo_a)

    sources = [WorkerSource(name="repo-a", host_path=str(repo_a), git=True)]
    ws = asyncio.run(
        MultiSourceWorktreeIsolation(lambda: sources).prepare(
            worker_id="pip", parent_workspace_root=str(parent)
        )
    )

    # A fresh strategy instance (different provider state) must clean up
    # purely from the workspace metadata.
    fresh = MultiSourceWorktreeIsolation(lambda: [])
    rehydrated = WorkerWorkspace(root=ws.root, metadata=dict(ws.metadata))
    assert asyncio.run(fresh.cleanup("pip", rehydrated)) is True
    assert not os.path.exists(ws.root)
    assert "worker/workspace-worker-pip/repo-a" not in _branches(repo_a)


def test_collision_suffix(tmp_path, scratch_workspace):
    parent = tmp_path / "workspace"
    parent.mkdir()
    repo_a = tmp_path / "repo-a"
    _init_repo(repo_a)
    sources = [WorkerSource(name="repo-a", host_path=str(repo_a), git=True)]
    strategy = MultiSourceWorktreeIsolation(lambda: sources)

    ws1 = asyncio.run(
        strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
    )
    ws2 = asyncio.run(
        strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
    )
    assert ws1.root != ws2.root
    assert ws2.root.endswith("-2")
    assert ws2.metadata["worktree_branch"] == "worker/workspace-worker-pip-2/repo-a"
    asyncio.run(strategy.cleanup("pip", ws1))
    asyncio.run(strategy.cleanup("pip", ws2))


def test_falls_back_to_single_repo_without_sources(tmp_path, scratch_workspace):
    parent = tmp_path / "proj"
    _init_repo(parent)
    strategy = MultiSourceWorktreeIsolation(lambda: [])

    assert asyncio.run(strategy.ready(str(parent))) is True
    ws = asyncio.run(
        strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
    )
    # Single-repo semantics: worktree of the parent itself, single branch.
    assert ws.metadata["worktree_branch"] == "worker/proj-worker-pip"
    assert "sources" not in ws.metadata
    assert os.path.isfile(os.path.join(ws.root, "file.txt"))
    assert asyncio.run(strategy.cleanup("pip", ws)) is True
    assert "worker/" not in _branches(parent)


def test_not_ready_when_sources_have_no_git(tmp_path, scratch_workspace):
    parent = tmp_path / "workspace"
    parent.mkdir()
    plain = tmp_path / "docs"
    plain.mkdir()
    strategy = MultiSourceWorktreeIsolation(
        lambda: [WorkerSource(name="docs", host_path=str(plain), git=False)]
    )
    assert asyncio.run(strategy.ready(str(parent))) is False


def test_dirty_detection_across_sources(tmp_path, scratch_workspace):
    parent = tmp_path / "workspace"
    parent.mkdir()
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_repo(repo_a)
    _init_repo(repo_b)
    strategy = MultiSourceWorktreeIsolation(
        lambda: [
            WorkerSource(name="repo-a", host_path=str(repo_a), git=True),
            WorkerSource(name="repo-b", host_path=str(repo_b), git=True),
        ]
    )
    assert asyncio.run(strategy.is_parent_dirty(str(parent))) is False
    (repo_b / "file.txt").write_text("changed")
    assert asyncio.run(strategy.is_parent_dirty(str(parent))) is True


def test_registration_name_override(tmp_path):
    strategy = MultiSourceWorktreeIsolation(lambda: [], name="worktree")
    assert strategy.name == "worktree"
    assert MultiSourceWorktreeIsolation(lambda: []).name == "multi-source-worktree"
