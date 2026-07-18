import asyncio
from types import SimpleNamespace

import omni_code.server_functions.compact as compact_mod


class FakeService:
    def __init__(self, model_name: str | None = None):
        self.spec = SimpleNamespace(model_name=model_name)
        self.status_calls: list[dict] = []

    async def set_client_status(
        self,
        message: str,
        *,
        session=None,
        run_id=None,
        show_spinner: bool = False,
    ) -> None:
        self.status_calls.append(
            {
                "message": message,
                "session": session,
                "run_id": run_id,
                "show_spinner": show_spinner,
            }
        )


class FakeSession:
    def __init__(self, session_id: str, *, active_model: str | None = None):
        self.id = session_id
        self.active_model = active_model
        self.model_config = None
        self.history: list[dict] = [{"role": "user", "content": "hello"}]

    def append_message(self, item: dict) -> None:
        self.history.append(item)


def test_compact_uses_runtime_model_id_for_custom_provider_prefix(monkeypatch):
    captured: dict = {}

    def fake_resolve_model_for_runtime(model_name=None):
        captured["resolve_arg"] = model_name
        return {
            "provider": "openai-compatible",
            "model": "gpt-4.1",
            "base_url": "http://localhost:11434/v1",
            "api_key": None,
        }

    async def fake_run_compaction_agent(*, model, runtime, prompt, session):
        captured["model"] = model
        captured["runtime"] = runtime
        captured["prompt"] = prompt
        captured["session_id"] = session.id
        return "summary"

    monkeypatch.setattr("omni_code.models.resolve_model_for_runtime", fake_resolve_model_for_runtime)
    monkeypatch.setattr(compact_mod, "_run_compaction_agent", fake_run_compaction_agent)

    service = FakeService(model_name="local/gpt-4.1")
    session = FakeSession("sess-1", active_model="local/gpt-4.1")

    result = asyncio.run(compact_mod.compact(service, session))

    assert result == "Context compacted."
    assert captured["resolve_arg"] == "local/gpt-4.1"
    assert captured["model"] == "gpt-4.1"
    assert captured["runtime"]["provider"] == "openai-compatible"
    assert captured["session_id"] == "sess-1"

    summary_item = session.history[-1]
    assert summary_item["omniagents"]["kind"] == "context_summary"


def test_run_compaction_agent_passes_model_provider_to_ephemeral_run(monkeypatch):
    """The compaction run delegates to the framework's run_agent_once with
    the provider/env resolved by build_model_provider. (RunConfig wiring
    inside run_agent_once is covered by the framework's own tests.)"""
    captured: dict = {}

    async def fake_run_agent_once(agent_yaml, prompt, **kwargs):
        captured["agent_yaml"] = str(agent_yaml)
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "ok"

    sentinel_provider = object()

    import omniagents.core.runtime as runtime_mod

    from omni_code import utility_runs

    monkeypatch.setattr(runtime_mod, "run_agent_once", fake_run_agent_once)
    monkeypatch.setattr(
        utility_runs,
        "build_model_provider",
        lambda runtime: (sentinel_provider, {"K": "v"}),
    )

    output = asyncio.run(
        compact_mod._run_compaction_agent(
            model="gpt-4.1",
            runtime={"provider": "openai-compatible", "model": "gpt-4.1"},
            prompt="hi",
            session=None,
        )
    )

    assert output == "ok"
    assert captured["agent_yaml"].endswith("compactor/agent.yml")
    assert captured["prompt"] == "hi"
    assert captured["model"] == "gpt-4.1"
    assert captured["model_provider"] is sentinel_provider
    assert captured["env_overrides"] == {"K": "v"}
    assert captured["workspace_root"]
