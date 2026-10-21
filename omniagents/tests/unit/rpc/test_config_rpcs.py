"""get_config / validate_config / write_config through AgentService:
end-to-end behavior, authorization posture, hot-apply, and adversarial
secret read-back attempts across every RPC read path."""

import json
from pathlib import Path

import pytest
import yaml

from omniagents.execution import AgentHostResources
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.auth import (
    AuthorizationDenied,
    AuthorizationPolicy,
    GLOBAL_MUTATING_METHODS,
    GLOBAL_SAFE_METHODS,
)
from omniagents.core.providers.auth.base import UserIdentity

PRODUCT = "cfgrpc"
SECRET_KEY = "security.providers.auth.token"


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "xdg"))  # Windows config root
    monkeypatch.delenv("OMNIAGENTS_PROJECT_OVERLAYS", raising=False)
    monkeypatch.delenv("OMNIAGENTS_LEGACY_QUERY_AUTH", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    manifest = root / "project.yml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "product": {"name": PRODUCT},
                "security": {"safety_mode": "recommended"},
            }
        ),
        encoding="utf-8",
    )
    return manifest


def make_service(manifest: Path) -> AgentService:
    spec = AgentSpec(
        name="Config Test Agent",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
        security_config={"safety_mode": "recommended"},
        manifest_path=str(manifest),
    )
    return AgentService(
        spec, AgentHostResources.create("test-unit-rpc-config_rpcs-host")
    )


@pytest.mark.asyncio
async def test_get_config_returns_layers_and_fields(project):
    service = make_service(project)
    result = await service.get_config()
    layer_names = [layer["name"] for layer in result["layers"]]
    assert layer_names == ["defaults", "project", "user", "managed", "environment"]
    keys = {field["key"] for field in result["fields"]}
    assert "security.safety_mode" in keys
    field = next(f for f in result["fields"] if f["key"] == "security.safety_mode")
    assert field["effective_layer"] == "project"
    assert field["value"] == "recommended"


@pytest.mark.asyncio
async def test_validate_config_dry_run_does_not_write(project):
    service = make_service(project)
    result = await service.validate_config({"security.safety_mode": "bogus"})
    assert result["valid"] is False
    assert result["errors"][0]["code"] == "invalid_value"
    # Dry-run touched nothing.
    described = await service.get_config()
    field = next(f for f in described["fields"] if f["key"] == "security.safety_mode")
    assert field["value"] == "recommended"
    assert service._layered_config_store().user_write_target().is_file() is False


@pytest.mark.asyncio
async def test_write_config_hot_applies_session_fields_to_live_spec(project):
    service = make_service(project)
    result = await service.write_config({"security.safety_mode": "enforced"})
    assert result["ok"] is True
    assert result["reload"]["session"] == ["security.safety_mode"]
    assert result["restart_required"] is False
    # The disable_tool precedent: the live spec dict is updated so the
    # next agent build (next run in any loaded session) enforces it.
    assert service.spec.security_config["safety_mode"] == "enforced"

    # Clearing reverts the live spec to the lower-layer effective value.
    result = await service.write_config({"security.safety_mode": None})
    assert result["ok"] is True
    assert service.spec.security_config["safety_mode"] == "recommended"


@pytest.mark.asyncio
async def test_write_config_batch_rolls_back_and_spec_untouched(project):
    service = make_service(project)
    result = await service.write_config(
        {
            "security.safety_mode": "enforced",
            "security.audit.retention_days": -1,
        }
    )
    assert result["ok"] is False
    assert result["written"] == []
    assert service.spec.security_config["safety_mode"] == "recommended"
    assert service._layered_config_store().user_write_target().is_file() is False


@pytest.mark.asyncio
async def test_secret_cannot_be_read_back_through_any_rpc(project):
    service = make_service(project)
    secret = "hunter2-rpc-secret"
    write_result = await service.write_config({SECRET_KEY: secret})
    assert write_result["ok"] is True
    assert secret not in json.dumps(write_result)

    # Every read path: effective values, provenance, validation echoes.
    assert secret not in json.dumps(await service.get_config())
    assert secret not in json.dumps(
        await service.validate_config({SECRET_KEY: "new-value"})
    )
    assert secret not in json.dumps(
        await service.validate_config({"security.audit.retention_days": secret})
    )
    # The field is reported as set, write-only.
    field = next(
        f for f in (await service.get_config())["fields"] if f["key"] == SECRET_KEY
    )
    assert field["secret"] is True
    assert field["is_set"] is True
    assert "value" not in field


# ---------------------------------------------------------------------------
# Authorization posture
# ---------------------------------------------------------------------------


def test_config_methods_are_classified():
    assert "get_config" in GLOBAL_SAFE_METHODS
    assert "validate_config" in GLOBAL_SAFE_METHODS
    assert "write_config" in GLOBAL_MUTATING_METHODS


@pytest.mark.asyncio
async def test_write_config_denied_for_principals_in_enforced_mode(project):
    service = make_service(project)
    service._authorization_policy = AuthorizationPolicy(enforced=True)
    principal = UserIdentity(user_id="alice")

    # Reads and dry-run validation are globally safe.
    await service.authorize_rpc(principal, "get_config", {})
    await service.authorize_rpc(principal, "validate_config", {"updates": {}})

    # Server-level config writes mutate state for every user — refused
    # for individual principals, exactly like disable_tool.
    with pytest.raises(AuthorizationDenied):
        await service.authorize_rpc(
            principal, "write_config", {"updates": {"tracing.provider": "x"}}
        )


@pytest.mark.asyncio
async def test_write_config_allowed_for_admin_principal_in_enforced_mode(project):
    service = make_service(project)
    service._authorization_policy = AuthorizationPolicy(enforced=True)
    principal = UserIdentity(user_id="agent_host_control", roles=["admin"])

    await service.authorize_rpc(
        principal, "write_config", {"updates": {"tracing.provider": "x"}}
    )


@pytest.mark.asyncio
async def test_write_config_allowed_in_single_user_mode(project):
    service = make_service(project)
    service._authorization_policy = AuthorizationPolicy(enforced=False)
    await service.authorize_rpc(
        None, "write_config", {"updates": {"tracing.provider": "x"}}
    )
