import pytest

from omniagents.execution import AgentHostResources


def test_catalog_registers_explicit_workspace_materialization(tmp_path):
    resources = AgentHostResources.create("catalog-host")
    source = {"kind": "local-git", "path": "/source/repo"}

    workspace = resources.provisioning.register_workspace(
        workspace_id="workspace-1",
        owner_user_id="alice",
        materialization_path=str(tmp_path),
        snapshot_ref="snapshot-1",
        sources=[source],
    )

    assert resources.workspaces.get("workspace-1") is workspace
    assert resources.provisioning.workspace_directory(workspace) == tmp_path.resolve()
    assert workspace.sources == (source,)


def test_catalog_workspace_registration_is_strictly_idempotent(tmp_path):
    resources = AgentHostResources.create("catalog-host")
    kwargs = {
        "workspace_id": "workspace-1",
        "owner_user_id": "alice",
        "materialization_path": str(tmp_path),
    }

    first = resources.provisioning.register_workspace(**kwargs)
    second = resources.provisioning.register_workspace(**kwargs)

    assert second is first
    with pytest.raises(ValueError, match="different materialization path"):
        resources.provisioning.register_workspace(
            **{**kwargs, "materialization_path": str(tmp_path / "other")}
        )


def test_catalog_profiles_are_owner_scoped_and_validated(tmp_path):
    resources = AgentHostResources.create("catalog-host")
    path = tmp_path / "devbox.yml"

    registered = resources.provisioning.register_profile(
        "devbox", {"kind": "path", "path": str(path)}, owner_user_id="alice"
    )
    resources.provisioning.register_profile(
        "devbox", {"kind": "default", "project_id": "project-b"}, owner_user_id="bob"
    )

    assert registered == {"kind": "path", "path": str(path.resolve())}
    assert resources.provisioning.list_profiles(owner_user_id="alice") == {
        "devbox": registered
    }
    assert resources.provisioning.profile("devbox", owner_user_id="bob") == {
        "kind": "default",
        "project_id": "project-b",
    }
    with pytest.raises(KeyError, match="not registered"):
        resources.provisioning.profile("devbox", owner_user_id="carol")
    with pytest.raises(ValueError, match="accepts only"):
        resources.provisioning.register_profile(
            "bad",
            {"kind": "path", "path": str(path), "project_id": "unexpected"},
            owner_user_id="alice",
        )


def test_catalog_does_not_infer_unregistered_workspace_directory():
    resources = AgentHostResources.create("catalog-host")
    workspace = resources.provisioning.register_workspace(
        workspace_id="workspace-1",
        owner_user_id=None,
        materialization_path="/explicit/root",
    )
    other_resources = AgentHostResources.create("other-host")
    other_resources.workspaces.register(workspace)

    with pytest.raises(KeyError, match="no materialization path"):
        other_resources.provisioning.workspace_directory(workspace)
