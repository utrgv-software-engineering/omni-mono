"""Tests for omniagents.core.eval.mcp_fixture."""

from __future__ import annotations

import json
from pathlib import Path

from omniagents.core.eval.mcp_fixture import UserMcpFixtureStager


class TestUserMcpFixtureStager:
    def test_creates_isolated_xdg_layout(self, tmp_path: Path):
        stager = UserMcpFixtureStager(tmp_path, product_config_name="my_product")
        assert stager.xdg_root == tmp_path / "_xdg_config"
        assert stager.config_dir == tmp_path / "_xdg_config" / "my_product"
        assert stager.config_dir.is_dir()
        assert stager.mcp_json_path == stager.config_dir / "mcp.json"

    def test_custom_xdg_subdir(self, tmp_path: Path):
        stager = UserMcpFixtureStager(
            tmp_path, product_config_name="p", xdg_subdir="xdg"
        )
        assert stager.xdg_root == tmp_path / "xdg"

    def test_write_mcp_json(self, tmp_path: Path):
        stager = UserMcpFixtureStager(tmp_path, product_config_name="my_product")
        path = stager.write_mcp_json(
            {
                "my-server": {
                    "type": "stdio",
                    "command": "node",
                    "args": ["/abs/cli.js", "--db-path", "/abs/db.sqlite"],
                }
            }
        )
        assert path == stager.mcp_json_path
        payload = json.loads(path.read_text())
        assert payload == {
            "mcpServers": {
                "my-server": {
                    "type": "stdio",
                    "command": "node",
                    "args": ["/abs/cli.js", "--db-path", "/abs/db.sqlite"],
                }
            }
        }

    def test_stage_file_copies_existing_fixture(self, tmp_path: Path):
        src = tmp_path / "fixture.db"
        src.write_bytes(b"db-bytes")
        stager = UserMcpFixtureStager(tmp_path / "ws", product_config_name="p")
        dest = stager.stage_file(src, "projects.db")
        assert dest == stager.config_dir / "projects.db"
        assert dest.read_bytes() == b"db-bytes"

    def test_stage_file_returns_planned_path_for_missing_fixture(self, tmp_path: Path):
        stager = UserMcpFixtureStager(tmp_path, product_config_name="p")
        dest = stager.stage_file(tmp_path / "nope.db", "projects.db")
        assert dest == stager.config_dir / "projects.db"
        assert not dest.exists()  # server initializes it on first open
        dest_none = stager.stage_file(None, "other.db")
        assert dest_none == stager.config_dir / "other.db"

    def test_stage_tree_copies_existing_dir(self, tmp_path: Path):
        src = tmp_path / "pages"
        (src / "sub").mkdir(parents=True)
        (src / "a.md").write_text("A")
        (src / "sub" / "b.md").write_text("B")
        stager = UserMcpFixtureStager(tmp_path / "ws", product_config_name="p")
        dest = stager.stage_tree(src, "projects")
        assert (dest / "a.md").read_text() == "A"
        assert (dest / "sub" / "b.md").read_text() == "B"

    def test_stage_tree_creates_empty_dir_when_fixture_missing(self, tmp_path: Path):
        stager = UserMcpFixtureStager(tmp_path, product_config_name="p")
        dest = stager.stage_tree(None, "projects")
        assert dest.is_dir()
        assert list(dest.iterdir()) == []

    def test_env_vars_points_xdg_at_staged_root(self, tmp_path: Path):
        stager = UserMcpFixtureStager(tmp_path, product_config_name="p")
        assert stager.env_vars() == {"XDG_CONFIG_HOME": str(stager.xdg_root)}

    def test_staged_config_visible_via_user_mcp_discovery(
        self, tmp_path: Path, monkeypatch
    ):
        """End-to-end with the framework's user-MCP loader: mcp.json in the
        staged XDG root is what discovery finds once XDG_CONFIG_HOME from
        env_vars() is applied."""
        stager = UserMcpFixtureStager(tmp_path, product_config_name="my_product")
        stager.write_mcp_json(
            {"srv": {"type": "stdio", "command": "echo", "args": ["hi"]}}
        )
        for key, value in stager.env_vars().items():
            monkeypatch.setenv(key, value)

        from omniagents.core.config.user_mcp import get_user_config_dir

        assert get_user_config_dir("my_product") == stager.config_dir
