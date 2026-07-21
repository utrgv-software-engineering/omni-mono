"""Tests for declarative eval-environment construction (evaluation.environment)."""

import asyncio
import json
from pathlib import Path

import pytest

from omniagents.core.eval.container_routing import ContainerRoutingMixin
from omniagents.core.eval.environment import EnvironmentContext, EvalEnvironment
from omniagents.core.eval.environment_config import (
    ConfiguredWorkspaceEnvironment,
    build_environment,
    register_environment_type,
)
from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment


def _declaration(tmp_path, **overrides):
    cfg = {
        "type": "local_workspace",
        "fixtures_dir": "fixtures/workspaces",
        "workspace_root": str(tmp_path / "cache"),
        "skills_subdir": ".prod/skills",
        "keep_tempdir_env": "PROD_KEEP",
        "tempdir_prefix": "prod_eval_",
    }
    cfg.update(overrides)
    return cfg


# ── builder / registry ───────────────────────────────────────────────


def test_missing_type_raises(tmp_path):
    with pytest.raises(ValueError, match="requires a 'type'"):
        build_environment({"fixtures_dir": "x"}, eval_dir=tmp_path)


def test_class_path_form_is_gone(tmp_path):
    # The old `class:` mechanism is not accepted — declarations without
    # a registered `type` fail loudly.
    with pytest.raises(ValueError, match="requires a 'type'"):
        build_environment(
            {"class": "environments.devbox_env.DevboxEvalEnvironment"},
            eval_dir=tmp_path,
        )


def test_unknown_type_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown evaluation environment type"):
        build_environment({"type": "nope"}, eval_dir=tmp_path)


def test_unknown_knob_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown keys"):
        build_environment(
            {"type": "local_workspace", "fixture_dir": "typo"}, eval_dir=tmp_path
        )


def test_registered_custom_type_builds(tmp_path):
    class CustomEnv(EvalEnvironment):
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def setup(self, scenario):
            return EnvironmentContext(session_id="s")

        async def teardown(self, context):
            pass

    register_environment_type(
        "custom_test_env",
        lambda cfg, *, project_root, eval_dir: CustomEnv(
            **cfg, project_root=project_root, eval_dir=eval_dir
        ),
    )
    env = build_environment(
        {"type": "custom_test_env", "knob": 3},
        project_root=tmp_path,
        eval_dir=tmp_path / "evaluations",
    )
    assert isinstance(env, CustomEnv)
    assert env.kwargs == {
        "knob": 3,
        "project_root": tmp_path,
        "eval_dir": tmp_path / "evaluations",
    }


# ── local_workspace knobs ────────────────────────────────────────────


def test_local_workspace_knobs(tmp_path):
    env = build_environment(_declaration(tmp_path), eval_dir=tmp_path / "evaluations")

    assert isinstance(env, ConfiguredWorkspaceEnvironment)
    assert isinstance(env, LocalWorkspaceEnvironment)
    assert env.fixtures_dir == tmp_path / "evaluations" / "fixtures" / "workspaces"
    assert env.workspace_cache_dir == tmp_path / "cache"
    assert env.skills_subdir == ".prod/skills"
    assert env.keep_tempdir_env == "PROD_KEEP"
    assert env.tempdir_prefix == "prod_eval_"
    # No container routing declared → mixin stays unarmed.
    assert env.profile_resolver is None
    assert env.default_profile_path is None
    assert env.default_profile is None
    assert env.default_image is None
    assert env.mcp_fixture is None


def test_local_workspace_defaults_apply(tmp_path):
    env = build_environment({"type": "local_workspace"}, eval_dir=tmp_path)
    assert env.fixtures_dir is None
    assert env.workspace_cache_dir == (
        Path.home() / ".cache" / "omniagents" / "eval-workspaces"
    )
    assert env.skills_subdir == ".omniagents/skills"
    assert env.keep_tempdir_env == "OMNIAGENTS_EVAL_KEEP_TEMPDIR"
    assert env.tempdir_prefix == "omniagents_eval_"


def test_absolute_fixtures_dir_and_tilde_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    env = build_environment(
        {
            "type": "local_workspace",
            "fixtures_dir": str(tmp_path / "abs-fixtures"),
            "workspace_root": "~/ws-root",
        },
        eval_dir=tmp_path / "evaluations",
    )
    assert env.fixtures_dir == tmp_path / "abs-fixtures"
    assert env.workspace_cache_dir == tmp_path / "ws-root"


# ── env map expansion ────────────────────────────────────────────────


def test_env_map_expansion(tmp_path):
    env = build_environment(
        _declaration(
            tmp_path,
            env={
                "PROD_WORKSPACE_ROOT": "${workspace}",
                "TRUSTED": "${workspace_root}",
                "EMPTY": "",
                "LITERAL": "${unknown_token}",
            },
        ),
        eval_dir=tmp_path / "evaluations",
    )
    ctx = EnvironmentContext(session_id="s", data={"workspace_root": "/tmp/ws1"})
    got = env.get_env_vars(ctx)
    assert got["PROD_WORKSPACE_ROOT"] == "/tmp/ws1"
    assert got["TRUSTED"] == str(tmp_path / "cache")
    assert got["EMPTY"] == ""
    assert got["LITERAL"] == "${unknown_token}"  # unknown tokens stay visible
    assert got["PWD"] == "/tmp/ws1"  # base workspace env vars still present


# ── container routing composition ────────────────────────────────────


def test_container_routing_product_resolver(tmp_path):
    from omniagents.core.sandbox.profiles import resolve_product_profile

    env = build_environment(
        _declaration(
            tmp_path,
            container_routing={
                "profile_resolver": "product",
                "default_profile": "profiles/devbox.yml",
            },
        ),
        eval_dir=tmp_path / "evaluations",
    )
    assert isinstance(env, ContainerRoutingMixin)
    assert env.profile_resolver is resolve_product_profile
    assert env.default_profile_path == (
        tmp_path / "evaluations" / "profiles" / "devbox.yml"
    )
    assert env.default_profile is None
    assert env.default_image is None


def test_container_routing_dotted_resolver_and_fallbacks(tmp_path):
    env = build_environment(
        _declaration(
            tmp_path,
            container_routing={
                "profile_resolver": "omniagents.core.sandbox.profiles.resolve_profile",
                "profile": "profiles/pinned.yml",
                "image": "python:3.12-slim",
            },
        ),
        eval_dir=tmp_path / "evaluations",
    )
    from omniagents.core.sandbox.profiles import resolve_profile

    assert env.profile_resolver is resolve_profile
    assert env.default_profile == "profiles/pinned.yml"
    assert env.default_image == "python:3.12-slim"


def test_container_routing_unknown_key_raises(tmp_path):
    with pytest.raises(ValueError, match="container_routing: unknown keys"):
        build_environment(
            _declaration(tmp_path, container_routing={"resolver": "product"}),
            eval_dir=tmp_path,
        )


# ── mcp fixture staging ──────────────────────────────────────────────


def _mcp_declaration(tmp_path, cli_path, **overrides):
    block = {
        "scenario_key": "db_fixture",
        "dbs_dir": "fixtures/dbs",
        "config_name": "prod",
        "server_name": "prod-projects",
        "command": {"env_var": "PROD_MCP_NODE", "default": "node"},
        "cli": {"env_var": "PROD_MCP_CLI", "default": str(cli_path)},
        "args": ["${cli}", "--db-path", "${db_path}", "--pages-dir", "${pages_dir}"],
        "db_dest": "projects.db",
        "pages_dest": "projects",
    }
    block.update(overrides)
    return _declaration(tmp_path, mcp_fixture=block)


def test_mcp_fixture_requires_core_keys(tmp_path):
    with pytest.raises(ValueError, match="requires 'config_name'"):
        build_environment(
            _declaration(
                tmp_path,
                mcp_fixture={"dbs_dir": "fixtures/dbs", "server_name": "x"},
            ),
            eval_dir=tmp_path,
        )


def test_mcp_fixture_staging(tmp_path, monkeypatch):
    eval_dir = tmp_path / "evaluations"
    dbs = eval_dir / "fixtures" / "dbs"
    dbs.mkdir(parents=True)
    (dbs / "seeded.db").write_bytes(b"sqlite-bytes")
    (dbs / "seeded.pages").mkdir()
    (dbs / "seeded.pages" / "note.md").write_text("hello\n")
    cli = tmp_path / "cli.js"
    cli.write_text("// cli\n")
    monkeypatch.delenv("PROD_MCP_CLI", raising=False)
    monkeypatch.delenv("PROD_MCP_NODE", raising=False)

    env = build_environment(_mcp_declaration(tmp_path, cli), eval_dir=eval_dir)
    mf = env.mcp_fixture
    assert mf.dbs_dir == dbs
    assert mf.config_name == "prod"
    assert mf.server_name == "prod-projects"
    assert mf.cli_env_var == "PROD_MCP_CLI"
    assert mf.cli_default == cli
    assert mf.command_env_var == "PROD_MCP_NODE"
    assert mf.command_default == "node"
    assert mf.db_dest == "projects.db"
    assert mf.pages_dest == "projects"

    ctx = asyncio.run(env.setup({"db_fixture": "seeded"}))
    try:
        ws = Path(ctx["workspace_root"])
        db_path = Path(ctx["db_path"])
        cfg_dir = Path(ctx["mcp_config_dir"])
        assert db_path == ws / "_xdg_config" / "prod" / "projects.db"
        assert db_path.read_bytes() == b"sqlite-bytes"
        assert (cfg_dir / "projects" / "note.md").read_text() == "hello\n"

        payload = json.loads((cfg_dir / "mcp.json").read_text())
        server = payload["mcpServers"]["prod-projects"]
        assert server["type"] == "stdio"
        assert server["command"] == "node"
        assert server["args"] == [
            str(cli),
            "--db-path",
            str(db_path),
            "--pages-dir",
            str(cfg_dir / "projects"),
        ]

        got = env.get_env_vars(ctx)
        assert got["XDG_CONFIG_HOME"] == str(ws / "_xdg_config")
        assert ctx["db_fixture"] == "seeded"
    finally:
        asyncio.run(env.teardown(ctx))


def test_mcp_fixture_skipped_without_scenario_key(tmp_path):
    cli = tmp_path / "cli.js"
    cli.write_text("// cli\n")
    env = build_environment(
        _mcp_declaration(tmp_path, cli), eval_dir=tmp_path / "evaluations"
    )
    ctx = asyncio.run(env.setup({}))
    try:
        assert ctx["db_fixture"] is None
        assert ctx["db_path"] is None
        assert ctx["mcp_config_dir"] is None
        assert "XDG_CONFIG_HOME" not in env.get_env_vars(ctx)
        assert not (Path(ctx["workspace_root"]) / "_xdg_config").exists()
    finally:
        asyncio.run(env.teardown(ctx))


def test_mcp_fixture_cli_env_override_wins(tmp_path, monkeypatch):
    default_cli = tmp_path / "default-cli.js"
    default_cli.write_text("// default\n")
    override_cli = tmp_path / "override-cli.js"
    override_cli.write_text("// override\n")
    monkeypatch.setenv("PROD_MCP_CLI", str(override_cli))

    env = build_environment(
        _mcp_declaration(tmp_path, default_cli), eval_dir=tmp_path / "evaluations"
    )
    assert env.mcp_fixture.resolve_cli() == override_cli


def test_mcp_fixture_cli_env_override_must_be_file(tmp_path, monkeypatch):
    default_cli = tmp_path / "default-cli.js"
    default_cli.write_text("// default\n")
    monkeypatch.setenv("PROD_MCP_CLI", str(tmp_path / "missing.js"))

    env = build_environment(
        _mcp_declaration(tmp_path, default_cli), eval_dir=tmp_path / "evaluations"
    )
    # No silent fallback to the default when the override is broken.
    with pytest.raises(RuntimeError, match="does not point to a file"):
        env.mcp_fixture.resolve_cli()


def test_mcp_fixture_missing_cli_fails_at_setup(tmp_path, monkeypatch):
    monkeypatch.delenv("PROD_MCP_CLI", raising=False)
    env = build_environment(
        _mcp_declaration(tmp_path, tmp_path / "never-built.js"),
        eval_dir=tmp_path / "evaluations",
    )
    with pytest.raises(RuntimeError, match="not available"):
        asyncio.run(env.setup({"db_fixture": "seeded"}))


def test_mcp_fixture_command_env_override(tmp_path, monkeypatch):
    cli = tmp_path / "cli.js"
    cli.write_text("// cli\n")
    env = build_environment(
        _mcp_declaration(tmp_path, cli), eval_dir=tmp_path / "evaluations"
    )
    monkeypatch.setenv("PROD_MCP_NODE", "/opt/node22/bin/node")
    assert env.mcp_fixture.resolve_command() == "/opt/node22/bin/node"
    monkeypatch.delenv("PROD_MCP_NODE")
    assert env.mcp_fixture.resolve_command() == "node"
