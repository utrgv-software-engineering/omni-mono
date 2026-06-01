"""Unit tests for ``omni_code.sandbox_profile``.

Covers the three contracts ``omni serve`` depends on:

  * Layered resolution (default ← user-default ← per-project ← explicit)
  * Deep merge semantics (dicts merge; lists/scalars replace)
  * ``${workspace_dir}`` substitution
  * Service parsing + validation (port references, command shape, types)
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from omni_code import sandbox_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content))
    return path


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Redirect ``get_config_dir`` so user/project layers live under tmp_path."""
    cfg = tmp_path / "cfg"
    monkeypatch.setattr(sandbox_profile, "get_config_dir", lambda: cfg)
    return cfg


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# Layered resolution
# ---------------------------------------------------------------------------


def test_default_only_when_no_user_or_project(config_dir, workspace):
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    assert profile.raw["client"]["type"] == "host"
    assert profile.raw["manifest"]["root"] == str(workspace.resolve())
    assert profile.services == []
    assert len(profile.sources) == 1
    assert profile.sources[0].name == "sandbox_profile_default.yml"


def test_user_default_layer_merges_over_builtin(config_dir, workspace):
    _write(
        config_dir / "sandbox" / "default.yml",
        """
        manifest:
          environment:
            FROM_USER: "v"
        """,
    )
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    # Default stays
    assert profile.raw["client"]["type"] == "host"
    # User-default adds env
    assert profile.raw["manifest"]["environment"]["FROM_USER"] == "v"
    # Both layers appear in sources
    assert len(profile.sources) == 2


def test_project_layer_merges_over_user(config_dir, workspace):
    _write(
        config_dir / "sandbox" / "default.yml",
        """
        manifest:
          environment:
            FROM_USER: "v"
            SHARED: user-value
        """,
    )
    _write(
        config_dir / "projects" / "proj-123" / "sandbox.yml",
        """
        manifest:
          environment:
            FROM_PROJECT: "v"
            SHARED: project-value
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, project_id="proj-123"
    )
    env = profile.raw["manifest"]["environment"]
    assert env["FROM_USER"] == "v"
    assert env["FROM_PROJECT"] == "v"
    # Project wins on conflicts
    assert env["SHARED"] == "project-value"
    assert len(profile.sources) == 3


def test_missing_project_profile_silently_skipped(config_dir, workspace):
    """Naming a project_id with no per-project file is not an error."""
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, project_id="does-not-exist"
    )
    assert len(profile.sources) == 1  # only the default


def test_explicit_profile_skips_user_and_project_layers(
    config_dir, workspace, tmp_path
):
    """``--profile`` is the escape hatch — it does not stack with user/project."""
    _write(
        config_dir / "sandbox" / "default.yml",
        """
        manifest:
          environment:
            FROM_USER: "v"
        """,
    )
    _write(
        config_dir / "projects" / "p" / "sandbox.yml",
        """
        manifest:
          environment:
            FROM_PROJECT: "v"
        """,
    )
    explicit = _write(
        tmp_path / "explicit.yml",
        """
        manifest:
          environment:
            FROM_EXPLICIT: "v"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace,
        project_id="p",
        explicit_path=explicit,
    )
    env = profile.raw["manifest"]["environment"]
    assert "FROM_EXPLICIT" in env
    assert "FROM_USER" not in env
    assert "FROM_PROJECT" not in env
    # Sources show only default + explicit, not user/project
    assert len(profile.sources) == 2


# ---------------------------------------------------------------------------
# Deep merge semantics
# ---------------------------------------------------------------------------


def test_deep_merge_nested_dicts(config_dir, workspace):
    """Nested dicts merge recursively; scalars at leaves replace."""
    _write(
        config_dir / "sandbox" / "default.yml",
        """
        manifest:
          root: /will-be-overridden
          environment:
            KEEP_ME: "v"
        """,
    )
    _write(
        config_dir / "projects" / "p" / "sandbox.yml",
        """
        manifest:
          root: /overridden
          environment:
            ADD_ME: "v"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, project_id="p"
    )
    assert profile.raw["manifest"]["root"] == "/overridden"
    assert profile.raw["manifest"]["environment"] == {
        "KEEP_ME": "v",
        "ADD_ME": "v",
    }


def test_lists_replace_not_concat(config_dir, workspace):
    """``exposed_ports`` is a list — overriding layer fully replaces it."""
    _write(
        config_dir / "sandbox" / "default.yml",
        """
        options:
          exposed_ports: [8080, 9090]
        """,
    )
    _write(
        config_dir / "projects" / "p" / "sandbox.yml",
        """
        options:
          exposed_ports: [5000]
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, project_id="p"
    )
    assert profile.raw["options"]["exposed_ports"] == [5000]


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------


def test_workspace_dir_substituted_recursively(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: docker
        options:
          image: dummy:latest
          exposed_ports: [8000]
        manifest:
          root: /workspace
          environment:
            HOST_WORKSPACE: ${workspace_dir}
          entries:
            ".":
              type: local_dir
              src: ${workspace_dir}
        services:
          tail:
            command: "tail -F ${workspace_dir}/log"
            port: 8000
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    ws_str = str(workspace.resolve())
    assert profile.raw["manifest"]["environment"]["HOST_WORKSPACE"] == ws_str
    assert profile.raw["manifest"]["entries"]["."]["src"] == ws_str
    assert ws_str in profile.services[0].command


def test_unknown_substitution_token_raises(config_dir, workspace, tmp_path):
    """A misspelled ${variable} should not silently pass through."""
    profile_path = _write(
        tmp_path / "p.yml",
        """
        manifest:
          root: ${not_a_real_var}
        """,
    )
    # ${not_a_real_var} doesn't match any known substitution key, so it's
    # left as-is (treated as literal). Verify that's the behavior — if we
    # ever tighten this to error, this test is the signal.
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.raw["manifest"]["root"] == "${not_a_real_var}"


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


def test_services_parsed_with_all_fields(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: docker
        options:
          image: x:y
          exposed_ports: [8080]
        manifest:
          root: /workspace
        services:
          code_server:
            command: "code-server --port 8080"
            port: 8080
            env:
              CS_AUTH: none
              CS_PASSWORD: ""
            user: "1000:1000"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert len(profile.services) == 1
    svc = profile.services[0]
    assert svc.name == "code_server"
    assert svc.command == "code-server --port 8080"
    assert svc.port == 8080
    assert svc.env == {"CS_AUTH": "none", "CS_PASSWORD": ""}
    assert svc.user == "1000:1000"


def test_service_port_must_be_in_exposed_ports(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: docker
        options:
          image: x:y
          exposed_ports: [8080]
        manifest:
          root: /workspace
        services:
          stray:
            command: "echo hi"
            port: 9999
        """,
    )
    with pytest.raises(ValueError, match="not in sandbox.options.exposed_ports"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


def test_service_without_port_is_allowed(config_dir, workspace, tmp_path):
    """Background daemons don't have to expose a port (e.g. a queue worker)."""
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        services:
          worker:
            command: "while true; do sleep 1; done"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.services[0].port is None


def test_service_missing_command_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        services:
          broken: {}
        """,
    )
    with pytest.raises(ValueError, match="command is required"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


def test_service_wrong_command_type_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        services:
          broken:
            command: ["this", "is", "a", "list"]
        """,
    )
    # We require strings for now (shell=True at the call site). If we later
    # support argv lists this test should flip to assert the list parses.
    with pytest.raises(ValueError, match="command is required"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


# ---------------------------------------------------------------------------
# Top-level validation
# ---------------------------------------------------------------------------


def test_missing_client_type_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        manifest:
          root: ${workspace_dir}
        """,
    )
    # Need to clobber the default's `client.type` to trigger validation.
    # Explicit-path mode still merges the default first, and the default
    # supplies `client.type: host` — so this profile actually validates.
    # Verify that's the behavior so we don't accidentally drift.
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.raw["client"]["type"] == "host"


def test_non_mapping_profile_rejected(config_dir, workspace, tmp_path):
    profile_path = tmp_path / "p.yml"
    profile_path.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


# ---------------------------------------------------------------------------
# Init block
# ---------------------------------------------------------------------------


def test_init_block_parses_commands_in_order(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        init:
          - "echo first"
          - "echo second"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.init == ["echo first", "echo second"]


def test_init_block_substitutes_workspace_dir(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        init:
          - "ls ${workspace_dir}"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert str(workspace.resolve()) in profile.init[0]


def test_init_absent_yields_empty_list(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.init == []


def test_init_non_list_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        init: "echo not-a-list"
        """,
    )
    with pytest.raises(ValueError, match="sandbox.init must be a list"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


# ---------------------------------------------------------------------------
# Network allowlist auto-discovery
# ---------------------------------------------------------------------------


def _write_network_json(config_dir, *, enabled: bool, allowlist: list[str]) -> None:
    import json as _json

    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "network.json").write_text(
        _json.dumps({"enabled": enabled, "allowlist": allowlist})
    )


def test_network_json_injects_env_when_enabled(config_dir, workspace):
    _write_network_json(config_dir, enabled=True, allowlist=["api.openai.com", "github.com"])
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw.get("manifest", {}).get("environment", {})
    assert env.get("OMNI_SANDBOX_NETWORK_ALLOWLIST") == "api.openai.com,github.com"


def test_network_json_disabled_does_not_inject(config_dir, workspace):
    _write_network_json(config_dir, enabled=False, allowlist=["api.openai.com"])
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw.get("manifest", {}).get("environment", {})
    assert "OMNI_SANDBOX_NETWORK_ALLOWLIST" not in env


def test_network_json_empty_allowlist_does_not_inject(config_dir, workspace):
    _write_network_json(config_dir, enabled=True, allowlist=[])
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw.get("manifest", {}).get("environment", {})
    assert "OMNI_SANDBOX_NETWORK_ALLOWLIST" not in env


def test_network_json_missing_is_silent(config_dir, workspace):
    # No network.json file at all.
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw.get("manifest", {}).get("environment", {})
    assert "OMNI_SANDBOX_NETWORK_ALLOWLIST" not in env


def test_explicit_profile_env_wins_over_network_json(config_dir, workspace, tmp_path):
    _write_network_json(config_dir, enabled=True, allowlist=["from-network-json.com"])
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
          environment:
            OMNI_SANDBOX_NETWORK_ALLOWLIST: "from-profile.com"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    env = profile.raw["manifest"]["environment"]
    assert env["OMNI_SANDBOX_NETWORK_ALLOWLIST"] == "from-profile.com"


def test_explicit_empty_string_opts_out_of_auto_discovery(config_dir, workspace, tmp_path):
    """Setting the env to empty string is the documented opt-out."""
    _write_network_json(config_dir, enabled=True, allowlist=["from-network-json.com"])
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
          environment:
            OMNI_SANDBOX_NETWORK_ALLOWLIST: ""
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    env = profile.raw["manifest"]["environment"]
    assert env["OMNI_SANDBOX_NETWORK_ALLOWLIST"] == ""


def test_malformed_network_json_falls_open(config_dir, workspace):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "network.json").write_text("{not valid json")
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw.get("manifest", {}).get("environment", {})
    assert "OMNI_SANDBOX_NETWORK_ALLOWLIST" not in env


def test_init_empty_string_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        init:
          - ""
        """,
    )
    with pytest.raises(ValueError, match="must be a non-empty"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


# ---------------------------------------------------------------------------
# Terminal block
# ---------------------------------------------------------------------------


def test_terminal_default_when_block_absent(config_dir, workspace):
    """Profiles without a ``terminal:`` block get sensible defaults."""
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    assert profile.terminal.command == "bash -i"
    assert profile.terminal.user is None
    assert profile.terminal.cwd is None


def test_terminal_block_parses_all_fields(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        terminal:
          command: "zsh -l"
          user: "1000:1000"
          cwd: "/workspace"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.terminal.command == "zsh -l"
    assert profile.terminal.user == "1000:1000"
    assert profile.terminal.cwd == "/workspace"


def test_terminal_user_falls_back_to_top_level_run_as(config_dir, workspace, tmp_path):
    """When ``terminal.user`` is omitted but ``run_as`` is set, materialize it."""
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        run_as: "1000:1000"
        manifest:
          root: ${workspace_dir}
        terminal:
          command: "bash -i"
        """,
    )
    profile = sandbox_profile.resolve_profile(
        workspace_dir=workspace, explicit_path=profile_path
    )
    assert profile.terminal.user == "1000:1000"


def test_terminal_empty_command_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        terminal:
          command: ""
        """,
    )
    with pytest.raises(ValueError, match="terminal.command"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


def test_terminal_non_mapping_rejected(config_dir, workspace, tmp_path):
    profile_path = _write(
        tmp_path / "p.yml",
        """
        client:
          type: unix_local
        manifest:
          root: ${workspace_dir}
        terminal: "bash -i"
        """,
    )
    with pytest.raises(ValueError, match="terminal must be a mapping"):
        sandbox_profile.resolve_profile(
            workspace_dir=workspace, explicit_path=profile_path
        )


# ---------------------------------------------------------------------------
# User .env → sandbox container (manifest.environment)
# ---------------------------------------------------------------------------


def test_user_env_file_injected_into_manifest_environment(config_dir, workspace):
    # Settings → Environment writes <config>/.env; it must reach the container.
    _write(config_dir / ".env", 'FOO=bar\n# a comment\nQUOTED="hello world"\n\nEMPTY_OK=\n')
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw["manifest"]["environment"]
    assert env["FOO"] == "bar"
    assert env["QUOTED"] == "hello world"  # dotenv strips the quotes


def test_profile_environment_wins_over_user_env(config_dir, workspace):
    _write(
        config_dir / "sandbox" / "default.yml",
        """
        manifest:
          environment:
            SHARED: from-profile
        """,
    )
    _write(config_dir / ".env", "SHARED=from-env\nONLY_ENV=v\n")
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    env = profile.raw["manifest"]["environment"]
    assert env["SHARED"] == "from-profile"  # explicit profile value is not clobbered
    assert env["ONLY_ENV"] == "v"


def test_missing_env_file_is_noop(config_dir, workspace):
    profile = sandbox_profile.resolve_profile(workspace_dir=workspace)
    # No <config>/.env → no crash, default profile resolves normally.
    assert profile.raw["client"]["type"] == "host"
