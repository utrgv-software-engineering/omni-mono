"""Tests for the EvalEnvironment base: concrete verify + run_command."""

import asyncio

from omniagents.core.eval.environment import (
    EnvironmentContext,
    EvalEnvironment,
    NoOpEnvironment,
)


class _WorkspaceEnv(EvalEnvironment):
    async def setup(self, scenario):
        raise NotImplementedError

    async def teardown(self, context):
        pass


def _ctx(workspace_root=None):
    data = {"workspace_root": str(workspace_root)} if workspace_root else {}
    return EnvironmentContext(session_id="s1", data=data)


def test_verify_no_workspace_root_keeps_noop_result():
    env = NoOpEnvironment()
    result = asyncio.run(env.verify(_ctx(), {"a.txt": {"exists": True}}))
    assert result == {"success": True, "results": {}}


def test_verify_exists_contains_not_contains(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def hello():\n    pass\n")

    env = _WorkspaceEnv()
    ctx = _ctx(tmp_path)

    result = asyncio.run(
        env.verify(
            ctx,
            {
                "src/main.py": {
                    "exists": True,
                    "contains": ["def hello"],
                    "not_contains": ["import os"],
                },
                "secrets.txt": {"exists": False},
            },
        )
    )
    assert result["success"] is True
    assert result["results"]["src/main.py"] == {"matched": True}
    assert result["results"]["secrets.txt"] == {"matched": True}


def test_verify_failures(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")

    env = _WorkspaceEnv()
    ctx = _ctx(tmp_path)

    result = asyncio.run(
        env.verify(
            ctx,
            {
                "a.py": {"contains": ["def main"], "not_contains": []},
                "missing.py": {"exists": True},
                "a.py2": {"exists": False},
            },
        )
    )
    assert result["success"] is False
    assert "missing pattern" in result["results"]["a.py"]["reason"]
    assert result["results"]["missing.py"]["reason"] == "file not found"
    assert result["results"]["a.py2"] == {"matched": True}

    result = asyncio.run(
        env.verify(ctx, {"a.py": {"not_contains": ["import os"]}})
    )
    assert result["success"] is False
    assert "forbidden pattern" in result["results"]["a.py"]["reason"]

    result = asyncio.run(env.verify(ctx, {"a.py": {"exists": False}}))
    assert result["success"] is False
    assert result["results"]["a.py"]["reason"] == "file should not exist"


def test_run_command_in_workspace(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    env = _WorkspaceEnv()

    code, out, err = env.run_command(_ctx(tmp_path), "cat hello.txt")
    assert code == 0
    assert out == "hi"

    code, out, err = env.run_command(_ctx(tmp_path), "false")
    assert code == 1


def test_run_command_without_workspace_errors():
    env = _WorkspaceEnv()
    code, out, err = env.run_command(_ctx(), "true")
    assert code == 127
    assert "workspace_root" in err


def test_run_command_timeout(tmp_path):
    env = _WorkspaceEnv()
    code, out, err = env.run_command(_ctx(tmp_path), "sleep 5", timeout=1)
    assert code == 124
    assert "timed out" in err
