"""Tests for the layered sandbox-profile resolver's parameterization.

Full behavioral coverage (merge semantics, services/terminal parsing,
allowlist/env injection) lives with the reference product's suite; these
pin the framework-level contract: config_dir/default_profile_path are
injected, layer order, and validation.
"""

from pathlib import Path
from textwrap import dedent

import pytest

from omniagents.core.sandbox.profiles import (
    NETWORK_ALLOWLIST_ENV,
    resolve_profile,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content))
    return path


def test_layer_order_default_then_user_then_project(tmp_path):
    default = _write(
        tmp_path / "pkg" / "default.yml",
        """
        client: {type: docker}
        options: {image: base:1}
        """,
    )
    cfg = tmp_path / "cfg"
    _write(cfg / "sandbox" / "default.yml", "options: {image: user:2}\n")
    _write(
        cfg / "projects" / "p1" / "sandbox.yml",
        "options: {image: project:3}\n",
    )

    resolved = resolve_profile(
        workspace_dir=tmp_path,
        config_dir=cfg,
        default_profile_path=default,
        project_id="p1",
    )
    assert resolved.raw["options"]["image"] == "project:3"
    assert resolved.raw["client"]["type"] == "docker"
    assert len(resolved.sources) == 3


def test_explicit_path_skips_user_layers(tmp_path):
    default = _write(tmp_path / "pkg" / "default.yml", "client: {type: docker}\n")
    cfg = tmp_path / "cfg"
    _write(cfg / "sandbox" / "default.yml", "options: {image: user:2}\n")
    explicit = _write(tmp_path / "explicit.yml", "options: {image: explicit:9}\n")

    resolved = resolve_profile(
        workspace_dir=tmp_path,
        config_dir=cfg,
        default_profile_path=default,
        explicit_path=explicit,
    )
    assert resolved.raw["options"]["image"] == "explicit:9"
    assert len(resolved.sources) == 2  # default + explicit; user layer skipped


def test_workspace_dir_substitution_and_network_injection(tmp_path):
    default = _write(
        tmp_path / "pkg" / "default.yml",
        """
        client: {type: unix_local}
        manifest:
          root: ${workspace_dir}
        """,
    )
    cfg = tmp_path / "cfg"
    _write(cfg / "network.json", '{"enabled": true, "allowlist": ["a.test", "b.test"]}')

    ws = tmp_path / "ws"
    ws.mkdir()
    resolved = resolve_profile(
        workspace_dir=ws, config_dir=cfg, default_profile_path=default
    )
    assert resolved.raw["manifest"]["root"] == str(ws.resolve())
    env = resolved.raw["manifest"]["environment"]["value"]
    assert env[NETWORK_ALLOWLIST_ENV] == "a.test,b.test"


def test_missing_client_type_raises(tmp_path):
    default = _write(tmp_path / "pkg" / "default.yml", "options: {image: x}\n")
    with pytest.raises(ValueError, match="client.type"):
        resolve_profile(
            workspace_dir=tmp_path,
            config_dir=tmp_path / "cfg",
            default_profile_path=default,
        )


def test_service_port_must_be_exposed(tmp_path):
    default = _write(
        tmp_path / "pkg" / "default.yml",
        """
        client: {type: docker}
        options: {exposed_ports: [8080]}
        services:
          web: {command: "serve", port: 9999}
        """,
    )
    with pytest.raises(ValueError, match="exposed_ports"):
        resolve_profile(
            workspace_dir=tmp_path,
            config_dir=tmp_path / "cfg",
            default_profile_path=default,
        )
