import asyncio
import os

import pytest

from omniagents.core.workers import (
    CopyIsolation,
    NoneIsolation,
    WorktreeIsolation,
    WorkerDefaults,
    WorkerWorkspace,
    get_isolation_strategy,
    isolation_strategy_names,
    register_isolation_strategy,
    set_worker_defaults,
    worker_defaults,
)
from omniagents.workspace import HostWorkspace, reset_workspace, set_workspace


@pytest.fixture(autouse=True)
def _reset_defaults():
    original = worker_defaults()
    yield
    set_worker_defaults(original)


def test_builtin_strategies_registered():
    names = isolation_strategy_names()
    assert "none" in names
    assert "copy" in names
    assert "worktree" in names
    assert isinstance(get_isolation_strategy("none"), NoneIsolation)
    assert isinstance(get_isolation_strategy("copy"), CopyIsolation)
    assert isinstance(get_isolation_strategy("worktree"), WorktreeIsolation)


def test_unknown_strategy_error_lists_registered():
    with pytest.raises(KeyError) as exc:
        get_isolation_strategy("bogus")
    assert "bogus" in str(exc.value)
    assert "none" in str(exc.value)


def test_register_custom_strategy():
    class Custom(NoneIsolation):
        name = "custom-test"

    register_isolation_strategy(Custom())
    assert get_isolation_strategy("custom-test").name == "custom-test"


def test_none_strategy_passthrough():
    strategy = NoneIsolation()
    ws = asyncio.run(strategy.prepare(worker_id="w1", parent_workspace_root="/x"))
    assert ws.root == "/x"
    assert ws.metadata == {}
    assert asyncio.run(strategy.ready("/x")) is True
    assert asyncio.run(strategy.is_parent_dirty("/x")) is False
    assert strategy.task_preamble(ws) is None


def test_copy_strategy_roundtrip(tmp_path):
    parent = tmp_path / "proj"
    parent.mkdir()
    (parent / "file.txt").write_text("content")
    scratch = tmp_path / "scratch"

    token = set_workspace(HostWorkspace(root="/", scratch_root=str(scratch)))
    try:
        strategy = CopyIsolation()
        assert asyncio.run(strategy.ready(str(parent))) is True
        ws = asyncio.run(
            strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
        )
        assert ws.root != str(parent)
        assert os.path.isfile(os.path.join(ws.root, "file.txt"))
        assert ws.metadata["parent_workspace"] == str(parent)
        assert "isolated copy" in strategy.task_preamble(ws)

        assert asyncio.run(strategy.cleanup("pip", ws)) is True
        assert not os.path.exists(ws.root)
    finally:
        reset_workspace(token)


def test_worker_defaults_set_get():
    def loader(model=None):
        return object(), {}

    set_worker_defaults(
        WorkerDefaults(spec_loader=loader, default_isolation="copy")
    )
    d = worker_defaults()
    assert d.spec_loader is loader
    assert d.default_isolation == "copy"
    assert d.history_loader is None


def test_worker_workspace_metadata_defaults():
    ws = WorkerWorkspace(root="/a")
    assert ws.metadata == {}


def _init_repo(path):
    import subprocess

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


def test_worktree_strategy_not_ready_outside_git(tmp_path):
    parent = tmp_path / "plain"
    parent.mkdir()
    token = set_workspace(HostWorkspace(root="/", scratch_root=str(tmp_path / "s")))
    try:
        assert asyncio.run(WorktreeIsolation().ready(str(parent))) is False
    finally:
        reset_workspace(token)


def test_worktree_strategy_roundtrip(tmp_path):
    import subprocess

    parent = tmp_path / "proj"
    parent.mkdir()
    _init_repo(parent)
    scratch = tmp_path / "scratch"

    token = set_workspace(HostWorkspace(root="/", scratch_root=str(scratch)))
    try:
        strategy = WorktreeIsolation()
        assert asyncio.run(strategy.ready(str(parent))) is True
        assert asyncio.run(strategy.is_parent_dirty(str(parent))) is False

        ws = asyncio.run(
            strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
        )
        assert ws.root != str(parent)
        assert os.path.isfile(os.path.join(ws.root, "file.txt"))
        assert ws.metadata["parent_workspace"] == str(parent)
        assert ws.metadata["worktree_branch"].startswith("worker/")
        preamble = strategy.task_preamble(ws)
        assert "git worktree" in preamble
        assert ws.metadata["worktree_branch"] in preamble

        branches = subprocess.run(
            ["git", "-C", str(parent), "branch", "--list", "worker/*"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ws.metadata["worktree_branch"] in branches

        # Dirty detection reflects uncommitted parent changes.
        (parent / "file.txt").write_text("changed")
        assert asyncio.run(strategy.is_parent_dirty(str(parent))) is True

        assert asyncio.run(strategy.cleanup("pip", ws)) is True
        assert not os.path.exists(ws.root)
        branches_after = subprocess.run(
            ["git", "-C", str(parent), "branch", "--list", "worker/*"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ws.metadata["worktree_branch"] not in branches_after
    finally:
        reset_workspace(token)


def test_worktree_strategy_collision_suffix(tmp_path):
    parent = tmp_path / "proj"
    parent.mkdir()
    _init_repo(parent)
    scratch = tmp_path / "scratch"

    token = set_workspace(HostWorkspace(root="/", scratch_root=str(scratch)))
    try:
        strategy = WorktreeIsolation()
        ws1 = asyncio.run(
            strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
        )
        ws2 = asyncio.run(
            strategy.prepare(worker_id="pip", parent_workspace_root=str(parent))
        )
        assert ws1.root != ws2.root
        assert ws2.root.endswith("-2")
        asyncio.run(strategy.cleanup("pip", ws1))
        asyncio.run(strategy.cleanup("pip", ws2))
    finally:
        reset_workspace(token)
