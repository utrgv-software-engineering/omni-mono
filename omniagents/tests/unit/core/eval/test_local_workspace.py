"""Tests for LocalWorkspaceEnvironment staging and teardown."""

import asyncio
import subprocess
from pathlib import Path

from omniagents.core.eval.local_workspace import (
    LocalWorkspaceEnvironment,
    copy_fixture_respecting_gitignore,
)


def _env(tmp_path, **kwargs):
    return LocalWorkspaceEnvironment(
        fixtures_dir=tmp_path / "fixtures",
        workspace_cache_dir=tmp_path / "cache",
        **kwargs,
    )


def test_setup_stages_fixture_agents_md_skills_and_git(tmp_path):
    fixture = tmp_path / "fixtures" / "demo"
    fixture.mkdir(parents=True)
    (fixture / "main.py").write_text("print('hi')\n")

    env = _env(tmp_path, skills_subdir=".prod/skills")
    ctx = asyncio.run(
        env.setup(
            {
                "fixture": "demo",
                "agents_md_content": "# Guidelines\n",
                "skills": {"deploy": "---\nname: deploy\n---\nBody\n"},
            }
        )
    )

    ws = Path(ctx["workspace_root"])
    assert (ws / "main.py").read_text() == "print('hi')\n"
    assert (ws / "AGENTS.md").read_text() == "# Guidelines\n"
    assert (ws / ".prod" / "skills" / "deploy" / "SKILL.md").exists()
    assert (ws / ".git").is_dir()
    assert ctx["fixture"] == "demo"
    assert ctx["fixture_src_dir"] == str(fixture)
    assert env.get_env_vars(ctx)["PWD"] == str(ws)

    asyncio.run(env.teardown(ctx))
    assert not ws.exists()


def test_teardown_keeps_workspace_when_env_set(tmp_path, monkeypatch):
    env = _env(tmp_path, keep_tempdir_env="TEST_KEEP_WS")
    ctx = asyncio.run(env.setup({}))
    ws = Path(ctx["workspace_root"])
    monkeypatch.setenv("TEST_KEEP_WS", "1")

    asyncio.run(env.teardown(ctx))
    assert ws.exists()

    monkeypatch.delenv("TEST_KEEP_WS")
    asyncio.run(env.teardown(ctx))
    assert not ws.exists()


def test_fixture_copy_respects_gitignore(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / ".gitignore").write_text("node_modules/\n")
    (src / "kept.txt").write_text("kept")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "big.js").write_text("x" * 10)

    dst = tmp_path / "dst"
    copy_fixture_respecting_gitignore(src, dst)

    assert (dst / "kept.txt").exists()
    assert not (dst / "node_modules").exists()


def test_fixture_copy_falls_back_without_git(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    (src / "a.txt").write_text("a")

    dst = tmp_path / "dst"
    copy_fixture_respecting_gitignore(src, dst)
    # src is inside this repo's tree? No — tmp_path is outside; git -C fails,
    # copytree fallback used.
    assert (dst / "a.txt").exists()
