"""Tests for product- and environment-derived skill-root resolution."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from omniagents.core.config.dirs import product_config_dir
from omniagents.core.product import (
    product_info_from_mapping,
    reset_product_info,
    set_product_info,
)
from omniagents.core.skills import (
    default_skill_roots,
    set_skill_roots_provider,
    skill_roots,
    source_skill_roots,
)
from omniagents.execution import (
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    WorkspaceResource,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@pytest.fixture(autouse=True)
def _reset():
    set_skill_roots_provider(None)
    reset_product_info()
    yield
    set_skill_roots_provider(None)
    reset_product_info()


@contextmanager
def _routed_sources(sources, *, kind=EnvironmentKind.HOST, root="/workspace"):
    resource = WorkspaceResource("workspace-1", sources=tuple(sources))
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="environment-1",
        workspace_id=resource.workspace_id,
        kind=kind,
        state=EnvironmentState.READY,
        generation=1,
    )
    workspace = Mock()
    workspace.root = root
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="load_skill",
                resource_scope=ResourceScope.RUN,
                execution_requirements=ExecutionRequirement.WORKSPACE_READ,
            ),
            principal=None,
            workspace=resource,
            environment=descriptor,
            environment_lease=EnvironmentLease(descriptor, workspace),
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


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


def test_source_roots_empty_outside_routed_run(tmp_path):
    assert source_skill_roots(str(tmp_path)) == []


def test_host_source_roots_use_declared_local_paths_and_workspace_mounts():
    with _routed_sources(
        [
            {"kind": "local-git", "mountName": "app", "path": "/host/app"},
            {"kind": "git-remote", "mountName": "lib", "repoUrl": "u"},
        ]
    ):
        assert source_skill_roots() == [
            Path("/host/app/skills"),
            Path("/workspace/lib/skills"),
        ]


def test_devbox_source_roots_ignore_host_paths():
    with _routed_sources(
        [{"kind": "local-git", "mountName": "app", "path": "/host/app"}],
        kind=EnvironmentKind.DEVBOX,
    ):
        assert source_skill_roots() == [Path("/workspace/app/skills")]


def test_source_roots_dedupe():
    with _routed_sources(
        [
            {"kind": "local", "mountName": "app", "path": "/host/app"},
            {"kind": "local-git", "mountName": "app", "path": "/host/app"},
        ]
    ):
        assert source_skill_roots() == [Path("/host/app/skills")]


def test_default_roots_include_source_roots_between_workspace_and_global(tmp_path):
    with _routed_sources(
        [{"kind": "local-git", "mountName": "app", "path": "/host/app"}]
    ):
        roots = default_skill_roots(str(tmp_path))
        assert roots[0] == tmp_path / ".omniagents" / "skills"
        assert Path("/host/app/skills") in roots
        assert roots[-1] == product_config_dir() / "skills"
