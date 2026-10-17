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

    monkeypatch.setattr(guardian, "_build_prompt", prompt)
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
