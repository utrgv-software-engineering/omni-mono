import json
from types import SimpleNamespace

import pytest

from omniagents.core.models import ModelConfigStore
from omniagents.core.models.catalog_api import apply_session_model
from omniagents.core.models.store import PROVIDER_CONNECTION_KEYS


def make_store(tmp_path):
    store = ModelConfigStore(config_dir=lambda: tmp_path, single_provider=True)
    store.save_config(
        {
            "default": "company/a",
            "provider_scope_version": 1,
            "providers": {
                "company": {
                    "type": "openai-compatible",
                    "base_url": "https://company.invalid/v1",
                    "api_key": "company-key",
                    "models": {"a": {"model": "a"}, "b": {"model": "b"}},
                },
                "personal": {
                    "type": "openai-compatible",
                    "base_url": "https://personal.invalid/v1",
                    "api_key": "personal-key",
                    "models": {"c": {"model": "c"}},
                },
            },
        }
    )
    return store


@pytest.mark.parametrize("field", sorted(PROVIDER_CONNECTION_KEYS))
def test_rejects_connection_overrides_on_write_and_legacy_resolution(tmp_path, field):
    store = make_store(tmp_path)
    previous = store.config_path().read_text()
    config = store.load_config()
    config["providers"]["company"]["models"]["a"][field] = "secret-not-for-errors"
    with pytest.raises(ValueError, match="provider-only") as exc:
        store.save_config(config)
    assert "secret-not-for-errors" not in str(exc.value)
    assert store.config_path().read_text() == previous
    store.config_path().write_text(json.dumps(config))
    with pytest.raises(ValueError, match="provider-only"):
        store.resolve_model_for_runtime("company/a")


def test_rejects_other_provider_at_rpc_and_runtime_boundary(tmp_path):
    store = make_store(tmp_path)
    session = SimpleNamespace(active_model="company/a", model_config=None)
    result = apply_session_model(store, session, "personal/c")
    assert not result["ok"]
    assert result["reasons"][0]["code"] == "provider_mismatch"
    assert session.active_model == "company/a"
    with pytest.raises(ValueError, match="outside"):
        store.resolve_model_for_runtime("personal/c")
    assert apply_session_model(store, session, "company/b")["ok"]
    assert session.model_config["base_url"] == "https://company.invalid/v1"


def test_switch_reconciles_existing_session_without_retaining_old_connection(tmp_path):
    store = make_store(tmp_path)
    session = SimpleNamespace(
        active_model="company/b",
        model_config=store.get_model_config("company/b"),
        reasoning_effort="high",
    )
    config = store.load_config()
    config["default"] = "personal/c"
    store.save_config(config)
    store.reconcile_session_model(session)
    assert session.active_model == "personal/c"
    assert session.model_config["api_key"] == "personal-key"
    assert session.model_config["base_url"] == "https://personal.invalid/v1"
    assert session.reasoning_effort is None


def test_legacy_cross_provider_session_needs_confirmation(tmp_path):
    store = make_store(tmp_path)
    config = store.load_config()
    config.pop("provider_scope_version")
    store.save_config(config)
    session = SimpleNamespace(active_model="personal/c", model_config=None)
    with pytest.raises(ValueError, match="Confirm"):
        store.reconcile_session_model(session)
    assert session.active_model == "personal/c"


def test_missing_selected_model_never_uses_product_fallback(tmp_path):
    store = make_store(tmp_path)
    config = store.load_config()
    config["default"] = "company/deleted"
    store.save_config(config)
    with pytest.raises(ValueError, match="unavailable"):
        store.resolve_model_for_runtime()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["approval", "workflow"])
async def test_review_uses_session_model_and_provider_together(
    tmp_path, monkeypatch, kind
):
    from omniagents.core import models
    from omniagents.core.agents import guardian, plan_review
    from agents import Runner

    store = make_store(tmp_path)
    monkeypatch.setattr(models, "model_store", lambda: store)
    monkeypatch.setattr(
        "omniagents.core.agents.approval_policy.guardian_model_ref",
        lambda service: "personal/c",
    )
    monkeypatch.setattr(
        "omniagents.core.agents.workflow_policy.workflow_guardian_model_ref",
        lambda service: "personal/c",
    )

    async def prompt(*args):
        return "Review synthetic input"

    monkeypatch.setattr(guardian, "build_review_request", prompt)
    monkeypatch.setattr(plan_review, "_build_prompt", prompt)
    monkeypatch.setattr(plan_review, "_bound_workspace_root", lambda: None)
    captured = []

    class Result:
        async def stream_events(self):
            if False:
                yield None

        def final_output_as(self, typ):
            return (
                json.dumps(
                    {
                        "risk_level": "low",
                        "user_authorization": "high",
                        "outcome": "allow",
                        "rationale": "test",
                    }
                )
                if kind == "approval"
                else json.dumps(
                    {
                        "criteria_met": "satisfied",
                        "evidence": "shown",
                        "outcome": "accept",
                        "rationale": "test",
                    }
                )
            )

    def run(agent, *args, run_config=None, **kwargs):
        resolved = run_config.model_provider.get_model(agent.model)
        captured.append((resolved.model, str(resolved._client.base_url)))
        return Result()

    monkeypatch.setattr(Runner, "run_streamed", run)
    session = SimpleNamespace(
        id="test",
        active_model="company/b",
        model_config=store.get_model_config("company/b"),
        reasoning_effort=None,
        context={},
    )
    service = SimpleNamespace(spec=SimpleNamespace(model_name="a"))
    if kind == "approval":
        await guardian._review(
            service,
            session,
            guardian.GuardianRequest(kind="tool", tool_name="read", arguments="{}"),
        )
    else:
        await plan_review._review(
            service,
            session,
            plan_review.PlanReviewRequest(
                step_id="1", subject="test", description="test", exit_criteria="test"
            ),
        )
    assert captured == [("b", "https://company.invalid/v1/")]


def test_litellm_connection_is_owned_by_provider(tmp_path, monkeypatch):
    import sys
    from omniagents.core.models.runtime import provider_from_runtime

    store = make_store(tmp_path)
    config = store.load_config()
    config["providers"]["company"] = {
        "type": "litellm",
        "routing_prefix": "anthropic",
        "api_key": "provider-key",
        "base_url": "https://company.invalid",
        "models": {"a": {"model": "anthropic/claude-example"}},
    }
    store.save_config(config)
    runtime = store.resolve_model_for_runtime()
    # Inspect the actual provider adapter without needing the optional LiteLLM dependency.
    monkeypatch.setitem(
        sys.modules,
        "agents.extensions.models.litellm_model",
        SimpleNamespace(
            LitellmModel=lambda model, **kwargs: SimpleNamespace(model=model, **kwargs)
        ),
    )
    provider, env = provider_from_runtime(runtime)
    model = provider.get_model(runtime["model"])
    assert model.api_key == "provider-key"
    assert model.base_url == "https://company.invalid"
    assert env == {}
    with pytest.raises(ValueError, match="routing prefix"):
        provider.get_model("openai/gpt-example")
    config["providers"]["company"]["models"]["a"]["model"] = "openai/gpt-example"
    store.save_config(config)
    with pytest.raises(ValueError, match="routing prefix"):
        store.resolve_model_for_runtime()
    del config["providers"]["company"]["routing_prefix"]
    store.save_config(config)
    with pytest.raises(ValueError, match="routing_prefix"):
        store.resolve_model_for_runtime()


def test_cli_provider_switch_requires_each_confirmation(tmp_path, monkeypatch):
    from omniagents.core.models.cli import run_model_command

    store = make_store(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(ValueError, match="cancelled"):
        run_model_command(["default", "personal/c"], store=store, prog="omni")
    assert store.selected_provider() == "company"
    run_model_command(
        ["default", "personal/c", "--confirm-provider-change"], store=store, prog="omni"
    )
    assert store.selected_provider() == "personal"
    with pytest.raises(ValueError, match="cancelled"):
        run_model_command(["default", "company/a"], store=store, prog="omni")
    assert store.selected_provider() == "personal"


def test_cli_add_default_cannot_bypass_confirmation(tmp_path, monkeypatch):
    from omniagents.core.models.cli import run_model_command

    store = make_store(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    with pytest.raises(ValueError, match="cancelled"):
        run_model_command(
            ["add", "--name", "personal/new", "--model", "new", "--default"],
            store=store,
            prog="omni",
        )
    assert store.get_model_config("personal/new") is None
    assert store.selected_provider() == "company"


@pytest.mark.asyncio
async def test_run_snapshot_keeps_helpers_and_inherited_tasks_on_starting_provider(
    tmp_path, monkeypatch
):
    import asyncio
    from omniagents.core import models
    from omniagents.core.models.runtime import resolve_service_model_runtime

    store = make_store(tmp_path)
    monkeypatch.setattr(models, "_ACTIVE_STORE", store)
    session = SimpleNamespace(
        active_model="company/b", model_config=None, reasoning_effort=None
    )
    scope, token = models.bind_run_models(store.snapshot_for_run(session))
    release = asyncio.Event()

    async def inherited():
        await release.wait()
        return models.model_store().selected_provider()

    try:
        child = asyncio.create_task(inherited())
        config = store.load_config()
        config["default"] = "personal/c"
        config["providers"]["company"]["base_url"] = "https://changed.invalid"
        store.save_config(config)
        # Even a concurrent picker mutation must not reroute a helper call.
        session.active_model = "personal/c"
        ref, runtime = resolve_service_model_runtime(None, session)
        assert ref == "company/b"
        assert runtime["base_url"] == "https://company.invalid/v1"
        assert models.model_store().selected_provider() == "company"
        # A drainer created in the old context sees current config once it ends.
        scope.active = False
        release.set()
        assert await child == "personal"
    finally:
        release.set()
        models.reset_run_models(token)
    next_run = store.snapshot_for_run(session)
    assert next_run._run_model_ref == "personal/c"


@pytest.mark.asyncio
async def test_catalog_without_session_is_scoped(tmp_path):
    from omniagents.core.agents.service import AgentService

    store = make_store(tmp_path)
    service = SimpleNamespace(_model_catalog_store=lambda: store)
    catalog = await AgentService.list_models(service)
    assert {m["provider"]["name"] for m in catalog["models"]} == {"company"}


@pytest.mark.asyncio
async def test_ink_provider_switch_requires_confirmation_and_rejects_stale_choices(
    tmp_path, monkeypatch
):
    from omniagents.builtin.server_functions import model_picker

    store = make_store(tmp_path)
    monkeypatch.setattr(model_picker, "model_store", lambda: store)
    choices = await model_picker.list_model_providers()
    assert choices["current"] == "company"
    assert {entry["name"] for entry in choices["providers"]} == {"company", "personal"}
    assert "company-key" not in json.dumps(choices)
    assert "base_url" not in json.dumps(choices)
    result = await model_picker.switch_provider("personal/c", "company", False)
    assert not result["ok"]
    assert store.selected_provider() == "company"
    result = await model_picker.switch_provider("personal/missing", "company", True)
    assert not result["ok"]
    result = await model_picker.switch_provider("personal/c", "company", True)
    assert result == {"ok": True, "provider": "personal", "model": "personal/c"}
    assert store.selected_provider() == "personal"
    assert store.load_config()["provider_scope_version"] == 1
    result = await model_picker.switch_provider("company/a", "company", True)
    assert not result["ok"]
    assert "changed" in result["error"]
    assert store.selected_provider() == "personal"


@pytest.mark.asyncio
async def test_provider_picker_ignores_voice_default_from_previous_provider(
    tmp_path, monkeypatch
):
    from omniagents.builtin.server_functions import model_picker

    store = make_store(tmp_path)
    config = store.load_config()
    config["providers"]["personal"]["models"]["voice"] = {
        "model": "voice",
        "realtime": True,
    }
    config["voice_default"] = "personal/voice"
    store.save_config(config)
    monkeypatch.setattr(model_picker, "model_store", lambda: store)
    choices = await model_picker.list_model_providers()
    assert choices["current"] == "company"
    assert {row["name"] for row in choices["providers"]} == {"company", "personal"}
    assert store.get_voice_default_model_name() is None
    assert store.get_voice_default_model_config() is None
    assert store.resolve_voice_settings_for_realtime() == {}
    with pytest.raises(ValueError, match="outside"):
        store.resolve_model_for_runtime("personal/voice")
    result = await model_picker.switch_provider("personal/c", "company", True)
    assert result["ok"]
    assert store.get_voice_default_model_name() == "personal/voice"
    # A saved voice preference is retained for when its provider is selected.
    assert store.load_config()["voice_default"] == "personal/voice"
