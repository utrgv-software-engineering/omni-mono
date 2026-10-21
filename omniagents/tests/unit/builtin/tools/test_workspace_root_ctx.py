"""workspace_root_from_ctx keeps the active workspace's path syntax."""

from __future__ import annotations

import ntpath
import posixpath
from types import SimpleNamespace

import pytest

from omniagents.builtin.tools._workspace import workspace_root_from_ctx


def _ctx(root: str) -> SimpleNamespace:
    return SimpleNamespace(context={"workspace_root": root})


@pytest.mark.parametrize(
    ("path_module", "root", "expected"),
    [
        # A sandbox root stays POSIX even when the runtime's os.path is not.
        (posixpath, "/workspace/", "/workspace"),
        # A Windows host root stays a Windows path even when the runtime's
        # os.path is POSIX.
        (ntpath, "C:\\proj\\", "C:\\proj"),
    ],
)
def test_workspace_root_uses_the_workspace_path_syntax(
    monkeypatch, path_module, root, expected
):
    monkeypatch.setattr(
        "omniagents.workspace.current_workspace",
        lambda: SimpleNamespace(path_module=path_module),
    )
    assert workspace_root_from_ctx(_ctx(root)) == expected


def test_missing_workspace_root_is_none():
    assert workspace_root_from_ctx(SimpleNamespace(context={})) is None


def test_unbound_workspace_falls_back_to_host_syntax(monkeypatch, tmp_path):
    from omniagents.workspace.types import WorkspaceNotBoundError

    def unbound():
        raise WorkspaceNotBoundError()

    monkeypatch.setattr("omniagents.workspace.current_workspace", unbound)
    assert workspace_root_from_ctx(_ctx(str(tmp_path))) == str(tmp_path)
