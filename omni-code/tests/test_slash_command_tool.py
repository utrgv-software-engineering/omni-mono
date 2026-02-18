import os

import pytest


@pytest.mark.asyncio
async def test_run_init_agent_uses_model_provider_and_restores_env(monkeypatch, tmp_path):
    import tools.slash_command as slash
    import omni_code.models as models
    import omniagents.core.config.loader as loader
    import omniagents.core.agents.builder as builder
    from agents import Runner

    monkeypatch.setattr(models, "load_models_config", lambda: {})
    monkeypatch.setattr(
        models,
        "resolve_model_for_runtime",
        lambda model_name=None: {
            "provider": "openai",
            "model": "gpt-test",
            "api_key": "sk-test",
            "base_url": "https://api.openai.com/v1",
            "model_settings": {"store": False},
        },
    )

    captured = {}

    class FakeSpec:
        def __init__(self):
            self.model_name = None
            self.model_settings = None

    monkeypatch.setattr(loader, "load_agent_spec_from_yaml", lambda *_args, **_kwargs: FakeSpec())

    async def fake_build_agent(*, settings, mcp_servers, spec, session=None, approval_callback=None, _loading_stack=None):
        captured["spec"] = spec
        return object()

    monkeypatch.setattr(builder, "_default_build_agent", fake_build_agent)

    dummy_provider = object()

    monkeypatch.setattr(slash, "_build_model_provider", lambda _runtime: (dummy_provider, {"OPENAI_API_KEY": "sk-temp"}))

    prior = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-original"

    async def fake_run(_agent, _input, *, max_turns=10, run_config=None, **_kwargs):
        assert run_config is not None
        assert run_config.model_provider is dummy_provider
        assert os.environ.get("OPENAI_API_KEY") == "sk-temp"

        class FakeResult:
            def final_output_as(self, _type):
                return "Created AGENTS.md"

        return FakeResult()

    monkeypatch.setattr(Runner, "run", fake_run)

    try:
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# Repository Guidelines\n", encoding="utf-8")
        monkeypatch.setenv("PWD", str(tmp_path))

        output = await slash.run_init_agent(max_turns=5)
        assert output.startswith("Created AGENTS.md")
        assert "```markdown" in output
        assert "# Repository Guidelines" in output
        assert captured["spec"].model_name == "gpt-test"
        assert captured["spec"].model_settings == {"store": False}
    finally:
        assert os.environ.get("OPENAI_API_KEY") == "sk-original"
        if prior is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = prior
