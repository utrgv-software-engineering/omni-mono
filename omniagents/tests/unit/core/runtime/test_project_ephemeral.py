"""Tests for the project-aware ephemeral run layer
(omniagents.core.runtime.run_project_agent_once).

Covers: agent-yaml resolution through a manifest (explicit project_path,
paths.agents override, installed-product fallback via
ProductInfo.manifest_path, failure modes) and the run wiring
(model_settings stamp, instructions swap, runtime -> provider adaptation,
pass-through to run_agent_once).
"""

from textwrap import dedent
from types import SimpleNamespace

import pytest

from omniagents.core.product import (
    bootstrap_product,
    reset_product_info,
    set_product_info,
    DEFAULT_PRODUCT_INFO,
)
from omniagents.core.runtime import (
    resolve_project_agent_yaml,
    run_project_agent_once,
)


@pytest.fixture(autouse=True)
def _fresh_identity():
    reset_product_info()
    yield
    reset_product_info()


def _make_project(tmp_path, *, agents_dir="agents", paths_block=None, key="util"):
    agent_yaml = tmp_path / agents_dir / key / "agent.yml"
    agent_yaml.parent.mkdir(parents=True, exist_ok=True)
    agent_yaml.write_text(
        dedent(
            f"""
            name: {key}
            instructions: You are a spec-level utility agent.
            tools: []
            """
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "project.yml"
    body = "name: proj_eph\nproduct:\n  slug: proj_eph\n"
    if paths_block is not None:
        body += paths_block
    manifest.write_text(body, encoding="utf-8")
    return manifest, agent_yaml


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolve_with_explicit_manifest_path(tmp_path):
    manifest, agent_yaml = _make_project(tmp_path)
    assert resolve_project_agent_yaml("util", manifest) == agent_yaml
    # A directory containing project.yml works too.
    assert resolve_project_agent_yaml("util", tmp_path) == agent_yaml


def test_resolve_honors_paths_agents(tmp_path):
    manifest, agent_yaml = _make_project(
        tmp_path,
        agents_dir="custom/agent_home",
        paths_block="paths:\n  agents: custom/agent_home\n",
    )
    assert resolve_project_agent_yaml("util", manifest) == agent_yaml


def test_resolve_searches_multiple_agents_dirs(tmp_path):
    manifest, agent_yaml = _make_project(
        tmp_path,
        agents_dir="extra",
        paths_block="paths:\n  agents: [agents, extra]\n",
    )
    assert resolve_project_agent_yaml("util", manifest) == agent_yaml


def test_resolve_falls_back_to_installed_product_manifest(tmp_path):
    manifest, agent_yaml = _make_project(tmp_path)
    bootstrap_product(manifest)
    assert resolve_project_agent_yaml("util") == agent_yaml


def test_resolve_without_manifest_anywhere_raises():
    # Default identity carries no manifest_path.
    set_product_info(DEFAULT_PRODUCT_INFO)
    with pytest.raises(ValueError, match="project_path"):
        resolve_project_agent_yaml("util")


def test_resolve_unknown_agent_names_candidates(tmp_path):
    manifest, _ = _make_project(tmp_path)
    with pytest.raises(FileNotFoundError, match="nope"):
        resolve_project_agent_yaml("nope", manifest)


def test_resolve_missing_manifest_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="project manifest"):
        resolve_project_agent_yaml("util", tmp_path / "absent")


# ---------------------------------------------------------------------------
# Run wiring
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, output):
        self._output = output

    async def stream_events(self):
        if False:  # pragma: no cover
            yield None

    def final_output_as(self, _type):
        return self._output


def _patch_run_stack(monkeypatch, captured):
    import agents as agents_mod
    import omniagents.core.agents.builder as builder_mod

    async def fake_build(*, settings, mcp_servers, spec, session):
        captured["built_spec"] = spec
        return "BUILT"

    def fake_run_streamed(agent, prompt, *, context, max_turns, run_config):
        captured["prompt"] = prompt
        captured["context"] = context
        captured["max_turns"] = max_turns
        captured["run_config"] = run_config
        return _FakeStream("project answer")

    monkeypatch.setattr(builder_mod, "_default_build_agent", fake_build)
    monkeypatch.setattr(
        agents_mod.Runner, "run_streamed", staticmethod(fake_run_streamed)
    )


@pytest.mark.asyncio
async def test_run_project_agent_once_plain_path_no_overrides(
    monkeypatch, tmp_path
):
    """Without model_settings/instructions the spec is loaded from the
    resolved yaml by run_agent_once itself."""
    manifest, _ = _make_project(tmp_path)
    captured = {}
    _patch_run_stack(monkeypatch, captured)

    out = await run_project_agent_once(
        "util",
        "do it",
        project_path=manifest,
        model="override-model",
        workspace_root="/explicit",
        max_turns=4,
    )
    assert out == "project answer"
    assert captured["prompt"] == "do it"
    assert captured["max_turns"] == 4
    assert captured["context"]["workspace_root"] == "/explicit"
    spec = captured["built_spec"]
    assert spec.model_name == "override-model"
    assert spec.get_agent_instructions().strip() == (
        "You are a spec-level utility agent."
    )


@pytest.mark.asyncio
async def test_run_project_agent_once_stamps_settings_and_instructions(
    monkeypatch, tmp_path
):
    manifest, _ = _make_project(tmp_path)
    captured = {}
    _patch_run_stack(monkeypatch, captured)

    out = await run_project_agent_once(
        "util",
        "summarize",
        project_path=manifest,
        model_settings={"store": False},
        instructions="CODE-OWNED PROMPT",
    )
    assert out == "project answer"
    spec = captured["built_spec"]
    assert spec.model_settings == {"store": False}
    assert spec.get_agent_instructions() == "CODE-OWNED PROMPT"


@pytest.mark.asyncio
async def test_run_project_agent_once_empty_settings_not_stamped(
    monkeypatch, tmp_path
):
    manifest, _ = _make_project(tmp_path)
    captured = {}
    _patch_run_stack(monkeypatch, captured)

    await run_project_agent_once(
        "util", "p", project_path=manifest, model_settings={}
    )
    # Empty dict means "nothing to stamp": the spec keeps its own value
    # (the loader's default is None for a spec that declares none).
    assert captured["built_spec"].model_settings is None


@pytest.mark.asyncio
async def test_run_project_agent_once_adapts_runtime_to_provider(
    monkeypatch, tmp_path
):
    manifest, _ = _make_project(tmp_path)
    captured = {}
    _patch_run_stack(monkeypatch, captured)

    sentinel_provider = object()
    seen = {}

    def fake_provider_from_runtime(runtime):
        seen["runtime"] = runtime
        return sentinel_provider, {"FAKE_KEY": "v"}

    import omniagents.core.models as models_mod

    monkeypatch.setattr(
        models_mod, "provider_from_runtime", fake_provider_from_runtime
    )

    await run_project_agent_once(
        "util", "p", project_path=manifest, runtime={"provider": "openai"}
    )
    assert seen["runtime"] == {"provider": "openai"}
    assert captured["run_config"].model_provider is sentinel_provider


@pytest.mark.asyncio
async def test_run_project_agent_once_explicit_provider_wins_over_runtime(
    monkeypatch, tmp_path
):
    manifest, _ = _make_project(tmp_path)
    captured = {}
    _patch_run_stack(monkeypatch, captured)

    def boom(_runtime):  # pragma: no cover - must not be called
        raise AssertionError("runtime adaptation must be skipped")

    import omniagents.core.models as models_mod

    monkeypatch.setattr(models_mod, "provider_from_runtime", boom)

    explicit = object()
    await run_project_agent_once(
        "util",
        "p",
        project_path=manifest,
        runtime={"provider": "openai"},
        model_provider=explicit,
        env_overrides={},
    )
    assert captured["run_config"].model_provider is explicit


@pytest.mark.asyncio
async def test_run_project_agent_once_session_workspace_fallback(
    monkeypatch, tmp_path
):
    manifest, _ = _make_project(tmp_path)
    captured = {}
    _patch_run_stack(monkeypatch, captured)

    session = SimpleNamespace(context={"workspace_root": "/from/session"})
    await run_project_agent_once("util", "p", project_path=manifest, session=session)
    assert captured["context"]["workspace_root"] == "/from/session"
