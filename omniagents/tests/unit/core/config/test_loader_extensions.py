"""Loader tests for the four SDK 0.16 surface additions:

1. agent_tools: YAML block
2. is_enabled callables on tools/handoffs/agent_tools
3. model_provider: block (responses_websocket opt-in)
4. model_settings.reasoning_content_replay
"""

import textwrap

import pytest

from omniagents.core.config.loader import (
    collect_config_problems,
    load_agent_spec_from_yaml,
)


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


def test_loader_parses_agent_tools_by_name(tmp_path):
    """`agent: <name>` resolves against agents_map to the same yaml_path
    a `yaml: <path>` entry would produce."""
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
        """
        name: MainAgent
        instructions: Be main
        agent_tools:
          - agent: SubAgent
            tool_name: ask_sub
            tool_description: Consult the sub
            max_turns: 5
        """,
    )

    spec = load_agent_spec_from_yaml(
        str(main_path),
        agents_map={"SubAgent": sub_path.resolve()},
    )
    assert len(spec.agent_tool_configs) == 1
    cfg = spec.agent_tool_configs[0]
    assert cfg.tool_name == "ask_sub"
    assert cfg.tool_description == "Consult the sub"
    assert cfg.max_turns == 5
    assert cfg.yaml_path == str(sub_path.resolve())


def test_loader_parses_agent_tools_bare_string(tmp_path):
    """`agent_tools: [explorer]` is shorthand for `[{agent: explorer}]`."""
    sub_path = tmp_path / "sub.yaml"
    _write(
        sub_path,
        """
        name: explorer
        instructions: Explore
        """,
    )
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        agent_tools:
          - explorer
        """,
    )

    spec = load_agent_spec_from_yaml(
        str(main_path),
        agents_map={"explorer": sub_path.resolve()},
    )
    assert len(spec.agent_tool_configs) == 1
    cfg = spec.agent_tool_configs[0]
    # tool_name not specified — left None so SDK Agent.as_tool() defaults
    # it to the sub-agent's name (per AgentToolConfig.tool_name docstring).
    assert cfg.tool_name is None
    assert cfg.yaml_path == str(sub_path.resolve())


def test_loader_agent_tools_unknown_name_skips(tmp_path):
    """Unknown agent name produces a clear warning and skips the entry."""
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        agent_tools:
          - agent: nonexistent
        """,
    )

    with collect_config_problems() as problems:
        spec = load_agent_spec_from_yaml(
            str(main_path),
            agents_map={"explorer": tmp_path / "explorer.yaml"},
        )
    assert spec.agent_tool_configs == []
    reported = "\n".join(problems)
    assert "nonexistent" in reported
    assert "explorer" in reported  # available agents listed


def test_loader_agent_tools_no_registry_skips(tmp_path):
    """`agent: <name>` without an agents_map produces a clear warning,
    so callers loading standalone YAML get an actionable error."""
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        agent_tools:
          - agent: explorer
        """,
    )

    with collect_config_problems() as problems:
        spec = load_agent_spec_from_yaml(str(main_path))  # no agents_map
    assert spec.agent_tool_configs == []
    reported = "\n".join(problems)
    assert "agent: explorer" in reported
    assert "agents_map" in reported


def test_loader_parses_handoffs_by_name(tmp_path):
    """`agent: <name>` in handoffs resolves against agents_map to the same
    yaml_path a `yaml: <path>` entry would produce."""
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
        """
        name: MainAgent
        instructions: Be main
        handoffs:
          - agent: SubAgent
            tool_name_override: hand_to_sub
            tool_description_override: Switch to the sub
        """,
    )

    spec = load_agent_spec_from_yaml(
        str(main_path),
        agents_map={"SubAgent": sub_path.resolve()},
    )
    assert len(spec.handoff_configs) == 1
    cfg = spec.handoff_configs[0]
    assert cfg.tool_name_override == "hand_to_sub"
    assert cfg.tool_description_override == "Switch to the sub"
    assert cfg.yaml_path == str(sub_path.resolve())


def test_loader_parses_handoffs_bare_string(tmp_path):
    """`handoffs: [explorer]` is shorthand for `[{agent: explorer}]`."""
    sub_path = tmp_path / "sub.yaml"
    _write(
        sub_path,
        """
        name: explorer
        instructions: Explore
        """,
    )
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        handoffs:
          - explorer
        """,
    )

    spec = load_agent_spec_from_yaml(
        str(main_path),
        agents_map={"explorer": sub_path.resolve()},
    )
    assert len(spec.handoff_configs) == 1
    cfg = spec.handoff_configs[0]
    assert cfg.tool_name_override is None
    assert cfg.yaml_path == str(sub_path.resolve())


def test_loader_handoffs_unknown_name_skips(tmp_path):
    """Unknown handoff agent name produces a clear warning and skips."""
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        handoffs:
          - agent: nonexistent
        """,
    )

    with collect_config_problems() as problems:
        spec = load_agent_spec_from_yaml(
            str(main_path),
            agents_map={"explorer": tmp_path / "explorer.yaml"},
        )
    assert spec.handoff_configs == []
    reported = "\n".join(problems)
    assert "nonexistent" in reported
    assert "explorer" in reported


def test_loader_handoffs_no_registry_skips(tmp_path):
    """`agent: <name>` in handoffs without an agents_map produces a clear warning."""
    main_path = tmp_path / "main.yaml"
    _write(
        main_path,
        """
        name: MainAgent
        instructions: Be main
        handoffs:
          - agent: explorer
        """,
    )

    with collect_config_problems() as problems:
        spec = load_agent_spec_from_yaml(str(main_path))  # no agents_map
    assert spec.handoff_configs == []
    reported = "\n".join(problems)
    assert "agent: explorer" in reported
    assert "agents_map" in reported


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
    (pdir / "preds.py").write_text(textwrap.dedent("""
            from omniagents.core.predicates.discovery import is_enabled_predicate

            @is_enabled_predicate
            def gate_open(ctx, agent):
                return True
            """))
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


def test_an_agent_keeps_its_own_name_beside_the_agents_it_calls(tmp_path):
    """The agents it names (as tools, or to hand off to) are not its name: a
    foreman that called mobile_build last was named mobile_build in its traces."""
    for name in ("triage", "mobile_build"):
        _write(
            tmp_path / f"{name}.yaml",
            f"""
        name: {name}
        instructions: x
        """,
        )
    main_path = tmp_path / "foreman.yaml"
    _write(
        main_path,
        """
        name: foreman
        instructions: Orchestrate
        agent_tools:
          - agent: triage
          - agent: mobile_build
        handoffs:
          - agent: triage
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(main_path),
        agents_map={
            n: (tmp_path / f"{n}.yaml").resolve() for n in ("triage", "mobile_build")
        },
    )
    assert spec.name == "foreman"
    assert len(spec.agent_tool_configs) == 2
