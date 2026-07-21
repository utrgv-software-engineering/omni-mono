import os

import pytest


@pytest.mark.asyncio
async def test_run_init_agent_uses_model_provider_and_restores_env(monkeypatch, tmp_path):
    import omni_code.init_command as slash
    from omniagents.core.models import model_store

    models = model_store()
    import omni_code.utility_runs as utility_runs
    import omniagents.core.config.loader as loader
    import omniagents.core.agents.builder as builder
    from agents import Runner

    # resolve_model_runtime is the framework's now; it reads the store
    # instance, so that's what tests patch (not the module-level alias).
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

    monkeypatch.setattr(utility_runs, "build_model_provider", lambda _runtime: (dummy_provider, {"OPENAI_API_KEY": "sk-temp"}))

    prior = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "sk-original"

    def fake_run_streamed(_agent, _input, *, context=None, max_turns=10, run_config=None, **_kwargs):
        assert run_config is not None
        assert run_config.model_provider is dummy_provider
        assert os.environ.get("OPENAI_API_KEY") == "sk-temp"

        class FakeResult:
            async def stream_events(self):
                return
                yield  # pragma: no cover — makes this an async generator

            def final_output_as(self, _type):
                return "Created AGENTS.md"

        return FakeResult()

    monkeypatch.setattr(Runner, "run_streamed", fake_run_streamed)

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


@pytest.mark.asyncio
async def test_run_init_agent_passes_workspace_root_via_run_context(
    monkeypatch, tmp_path
):
    """The init agent's tools (apply_patch / write_file / read_file)
    resolve relative paths against ctx.context["workspace_root"]. Under
    strict mode (omniagents v0.6.48+) a missing value raises
    WorkspaceRootRequiredError. This test pins the contract: the
    slash-command flow must pass workspace_root via the runner's
    context= kwarg, sourced from $PWD (or cwd as fallback)."""
    import omni_code.init_command as slash
    from omniagents.core.models import model_store

    models = model_store()
    import omni_code.utility_runs as utility_runs
    import omniagents.core.config.loader as loader
    import omniagents.core.agents.builder as builder
    from agents import Runner

    monkeypatch.setattr(
        models,
        "resolve_model_for_runtime",
        lambda model_name=None: {"model": "gpt-test", "model_settings": {}},
    )

    class FakeSpec:
        def __init__(self):
            self.model_name = None
            self.model_settings = None

    monkeypatch.setattr(
        loader, "load_agent_spec_from_yaml", lambda *_a, **_k: FakeSpec()
    )

    async def fake_build_agent(
        *, settings, mcp_servers, spec, session=None, approval_callback=None, _loading_stack=None
    ):
        return object()

    monkeypatch.setattr(builder, "_default_build_agent", fake_build_agent)
    monkeypatch.setattr(
        utility_runs, "build_model_provider", lambda _runtime: (None, {})
    )

    captured: dict = {}

    def fake_run_streamed(_agent, _input, *, context=None, max_turns=10, run_config=None, **_kwargs):
        captured["context"] = context

        class FakeResult:
            async def stream_events(self):
                return
                yield  # pragma: no cover — makes this an async generator

            def final_output_as(self, _type):
                return "Created AGENTS.md"

        return FakeResult()

    monkeypatch.setattr(Runner, "run_streamed", fake_run_streamed)

    # Asymmetric: PWD points at tmp_path, NOT the test runner cwd. If the
    # slash command silently picked up some other path (e.g. the server
    # process's cwd), captured["context"] would not match tmp_path.
    monkeypatch.setenv("PWD", str(tmp_path))
    (tmp_path / "AGENTS.md").write_text("# Repository Guidelines\n", encoding="utf-8")

    await slash.run_init_agent(max_turns=5)

    assert captured["context"] is not None, (
        "Runner.run was called without context= — workspace_root not propagated"
    )
    assert isinstance(captured["context"], dict)
    assert captured["context"].get("workspace_root") == str(tmp_path), (
        f"workspace_root mismatch: got {captured['context'].get('workspace_root')!r}, "
        f"expected {str(tmp_path)!r}"
    )


@pytest.mark.asyncio
async def test_run_init_agent_falls_back_to_cwd_when_pwd_missing(
    monkeypatch, tmp_path
):
    """If $PWD is unset, the slash command should anchor workspace_root
    to Path.cwd() rather than passing None (which would raise
    WorkspaceRootRequiredError downstream when init_agent's apply_patch
    tries to write AGENTS.md)."""
    import omni_code.init_command as slash
    from omniagents.core.models import model_store

    models = model_store()
    import omni_code.utility_runs as utility_runs
    import omniagents.core.config.loader as loader
    import omniagents.core.agents.builder as builder
    from agents import Runner
    from pathlib import Path

    monkeypatch.setattr(
        models,
        "resolve_model_for_runtime",
        lambda model_name=None: {"model": "gpt-test", "model_settings": {}},
    )

    class FakeSpec:
        def __init__(self):
            self.model_name = None
            self.model_settings = None

    monkeypatch.setattr(
        loader, "load_agent_spec_from_yaml", lambda *_a, **_k: FakeSpec()
    )

    async def fake_build_agent(
        *, settings, mcp_servers, spec, session=None, approval_callback=None, _loading_stack=None
    ):
        return object()

    monkeypatch.setattr(builder, "_default_build_agent", fake_build_agent)
    monkeypatch.setattr(
        utility_runs, "build_model_provider", lambda _runtime: (None, {})
    )

    captured: dict = {}

    def fake_run_streamed(_agent, _input, *, context=None, max_turns=10, run_config=None, **_kwargs):
        captured["context"] = context

        class FakeResult:
            async def stream_events(self):
                return
                yield  # pragma: no cover — makes this an async generator

            def final_output_as(self, _type):
                return "Created AGENTS.md"

        return FakeResult()

    monkeypatch.setattr(Runner, "run_streamed", fake_run_streamed)

    monkeypatch.delenv("PWD", raising=False)

    # cd into tmp_path so Path.cwd() returns it deterministically.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# Repository Guidelines\n", encoding="utf-8")

    await slash.run_init_agent(max_turns=5)

    assert captured["context"] is not None
    assert captured["context"].get("workspace_root") == str(tmp_path)
