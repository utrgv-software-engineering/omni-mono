"""Tests for derived skill-root resolution (product identity + serve state)."""

from pathlib import Path

import pytest

from omniagents.core.config.dirs import product_config_dir
from omniagents.core.product import (
    product_info_from_mapping,
    reset_product_info,
    set_product_info,
)
from omniagents.core.runtime import serve_state
from omniagents.core.skills import (
    default_skill_roots,
    set_skill_roots_provider,
    skill_roots,
    source_skill_roots,
)


@pytest.fixture(autouse=True)
def _reset():
    set_skill_roots_provider(None)
    serve_state.reset()
    reset_product_info()
    yield
    set_skill_roots_provider(None)
    serve_state.reset()
    reset_product_info()


# ----------------------------- default derivation ------------------------ #


def test_default_roots_derive_from_framework_identity(tmp_path):
    roots = skill_roots(str(tmp_path))
    assert roots[0] == tmp_path / ".omniagents" / "skills"
    assert roots[-1] == product_config_dir() / "skills"
    assert roots[-1].parts[-2] == "omniagents"


def test_default_roots_derive_from_installed_product(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    set_product_info(
        product_info_from_mapping({"slug": "omni_code", "config_dir": "omni_code"})
    )
    roots = skill_roots(str(tmp_path / "ws"))
    assert roots == [
        tmp_path / "ws" / ".omni_code" / "skills",
        tmp_path / "config" / "omni_code" / "skills",
    ]


def test_default_roots_fall_back_to_invocation_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    roots = default_skill_roots(None)
    assert roots[0] == tmp_path.resolve() / ".omniagents" / "skills"


def test_installed_provider_replaces_derivation(tmp_path):
    set_skill_roots_provider(lambda workspace_root: [tmp_path / "only"])
    assert skill_roots(str(tmp_path)) == [tmp_path / "only"]


def test_failing_provider_degrades_to_derived_default(tmp_path):
    def boom(workspace_root):
        raise RuntimeError("boom")

    set_skill_roots_provider(boom)
    roots = skill_roots(str(tmp_path))
    assert roots[0] == tmp_path / ".omniagents" / "skills"


# ----------------------------- source roots ------------------------------ #


def test_source_roots_empty_outside_serve_session(tmp_path):
    assert source_skill_roots(str(tmp_path)) == []


def test_source_roots_resolve_mounts_against_manifest_root():
    serve_state.set_manifest_root("/workspace")
    serve_state.set_sources(
        [
            {"kind": "local-git", "mountName": "app", "path": "/host/app"},
            {"kind": "git-remote", "mountName": "lib", "repoUrl": "u"},
        ]
    )
    # No live sandbox session (host mode): local sources also contribute
    # their host path.
    assert source_skill_roots() == [
        Path("/workspace/app/skills"),
        Path("/host/app/skills"),
        Path("/workspace/lib/skills"),
    ]


def test_source_roots_skip_host_paths_when_sandbox_live():
    serve_state.set_manifest_root("/workspace")
    serve_state.set_sandbox_session(object())
    serve_state.set_sources(
        [{"kind": "local-git", "mountName": "app", "path": "/host/app"}]
    )
    assert source_skill_roots() == [Path("/workspace/app/skills")]


def test_source_roots_infer_manifest_root_from_mounted_workspace():
    serve_state.set_sources(
        [{"kind": "local", "mountName": "app", "path": "/host/app"}]
    )
    # Workspace root IS a mount: the manifest root is its parent.
    roots = source_skill_roots("/tmp/session/app")
    assert roots == [
        Path("/tmp/session/app/skills"),
        Path("/host/app/skills"),
    ]


def test_source_roots_dedupe():
    serve_state.set_manifest_root("/workspace")
    serve_state.set_sources(
        [
            {"kind": "local", "mountName": "app", "path": "/host/app"},
            {"kind": "local-git", "mountName": "app", "path": "/host/app"},
        ]
    )
    assert source_skill_roots() == [
        Path("/workspace/app/skills"),
        Path("/host/app/skills"),
    ]


def test_default_roots_include_source_roots_between_workspace_and_global(tmp_path):
    serve_state.set_manifest_root("/workspace")
    serve_state.set_sources(
        [{"kind": "local-git", "mountName": "app", "path": "/host/app"}]
    )
    roots = default_skill_roots(str(tmp_path))
    assert roots[0] == tmp_path / ".omniagents" / "skills"
    assert Path("/workspace/app/skills") in roots
    assert roots[-1] == product_config_dir() / "skills"
