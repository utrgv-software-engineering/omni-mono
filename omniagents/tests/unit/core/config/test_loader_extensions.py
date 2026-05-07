"""Loader tests for the four SDK 0.16 surface additions:

1. agent_tools: YAML block
2. is_enabled callables on tools/handoffs/agent_tools
3. model_provider: block (responses_websocket opt-in)
4. model_settings.reasoning_content_replay
"""

import textwrap

import pytest

from omniagents.core.config.loader import load_agent_spec_from_yaml


def _write(path, content):
    path.write_text(textwrap.dedent(content))


# -- 1. agent_tools -----------------------------------------------------------


def test_loader_parses_agent_tools_yaml(tmp_path):
    sub_path = tmp_path / "sub.yaml"
    _write(
        sub_path,
        """
        name: SubAgent
        instructions: Be a sub
        """,
    )
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        f"""
        name: MainAgent
        instructions: Be main
        agent_tools:
          - yaml: {sub_path.name}
            tool_name: ask_sub
            tool_description: Consult the sub
            max_turns: 5
        """,
    )

    spec = load_agent_spec_from_yaml(str(main_path))
    assert len(spec.agent_tool_configs) == 1
    cfg = spec.agent_tool_configs[0]
    assert cfg.tool_name == "ask_sub"
    assert cfg.tool_description == "Consult the sub"
    assert cfg.max_turns == 5
    assert cfg.yaml_path == str(sub_path.resolve())


def test_loader_parses_inline_agent_tools(tmp_path):
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        agent_tools:
          - inline:
              name: InlineSub
              instructions: do thing
            tool_name: ask_inline
        """,
    )

    spec = load_agent_spec_from_yaml(str(main_path))
    assert len(spec.agent_tool_configs) == 1
    cfg = spec.agent_tool_configs[0]
    assert cfg.tool_name == "ask_inline"
    assert cfg.inline_spec is not None
    assert cfg.inline_spec.name == "InlineSub"


# -- 2. is_enabled on handoffs and agent_tools --------------------------------


def test_loader_resolves_is_enabled_predicate_dotted(tmp_path):
    sub_path = tmp_path / "sub.yaml"
    _write(sub_path, "name: Sub\ninstructions: x")
    main_path = tmp_path / "main.yaml"
    # Reference a known-importable callable as the predicate.
    _write(
        main_path,
        f"""
        name: Main
        instructions: y
        handoffs:
          - yaml: {sub_path.name}
            is_enabled: omniagents.core.predicates.discovery:resolve_predicate
        agent_tools:
          - yaml: {sub_path.name}
            tool_name: ask_sub
            is_enabled: omniagents.core.predicates.discovery:resolve_predicate
        """,
    )

    spec = load_agent_spec_from_yaml(str(main_path))
    from omniagents.core.predicates.discovery import resolve_predicate as rp

    assert spec.handoff_configs[0].is_enabled is rp
    assert spec.agent_tool_configs[0].is_enabled is rp


def test_loader_resolves_is_enabled_via_predicates_dir(tmp_path):
    # Stand up a predicates/ subfolder next to the YAML.
    pdir = tmp_path / "predicates"
    pdir.mkdir()
    (pdir / "preds.py").write_text(
        textwrap.dedent(
            """
            from omniagents.core.predicates.discovery import is_enabled_predicate

            @is_enabled_predicate
            def gate_open(ctx, agent):
                return True
            """
        )
    )
    sub_path = tmp_path / "sub.yaml"
    _write(sub_path, "name: Sub\ninstructions: x")
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        f"""
        name: Main
        instructions: y
        agent_tools:
          - yaml: {sub_path.name}
            tool_name: ask
            is_enabled: gate_open
        """,
    )

    spec = load_agent_spec_from_yaml(str(main_path))
    cfg = spec.agent_tool_configs[0]
    assert callable(cfg.is_enabled)
    assert cfg.is_enabled.__name__ == "gate_open"


# -- 3. model_provider --------------------------------------------------------


def test_loader_parses_model_provider_block(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "sk-from-env")
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: M
        instructions: x
        model_provider:
          type: openai
          api_key: ${MY_TEST_KEY}
          use_responses_websocket: true
          websocket_options:
            ping_interval: 30
        """,
    )

    spec = load_agent_spec_from_yaml(str(main_path))
    assert spec.model_provider_config is not None
    assert spec.model_provider_config["type"] == "openai"
    assert spec.model_provider_config["use_responses_websocket"] is True
    assert spec.model_provider_config["api_key"] == "sk-from-env"
    assert spec.model_provider_config["websocket_options"]["ping_interval"] == 30


# -- 4. reasoning_content_replay ---------------------------------------------


def test_loader_routes_reasoning_replay_to_provider_config(tmp_path):
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: M
        instructions: x
        model_settings:
          reasoning_content_replay:
            enabled: true
        """,
    )

    spec = load_agent_spec_from_yaml(str(main_path))
    # Should have been lifted out of model_settings (so ModelSettings wouldn't choke).
    assert spec.model_settings is None or "reasoning_content_replay" not in (
        spec.model_settings or {}
    )
    # And folded into model_provider_config.
    assert spec.model_provider_config is not None
    assert spec.model_provider_config["reasoning_content_replay"] == {"enabled": True}
