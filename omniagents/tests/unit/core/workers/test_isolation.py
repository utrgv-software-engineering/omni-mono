import asyncio
import os

import pytest

from omniagents.core.workers import (
    CopyIsolation,
    NoneIsolation,
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
    assert isinstance(get_isolation_strategy("none"), NoneIsolation)
    assert isinstance(get_isolation_strategy("copy"), CopyIsolation)


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
