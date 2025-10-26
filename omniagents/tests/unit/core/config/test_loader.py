import os
import sys
import textwrap

import pytest

from omniagents.core.config.loader import load_agent_spec_from_yaml


def write(path, content):
    path.write_text(textwrap.dedent(content))


def test_loader_reads_instructions_file(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    instr_path = tmp_path / "instr.txt"
    instr_path.write_text("Do things")
    write(
        yaml_path,
        """
        name: TestAgent
        instructions_file: instr.txt
        max_turns: 7
        welcome_text: hi
        model: gpt-4o-mini
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.name == "TestAgent"
    assert spec.max_turns == 7
    assert spec.welcome_text == "hi"
    assert spec.model_name == "gpt-4o-mini"
    assert spec.get_agent_instructions() == "Do things"
    assert spec.client_storage_prefix.startswith("testagent")


def test_loader_substitutes_env_vars_using_dotenv(tmp_path, monkeypatch):
    secret_key = "TESTING_ENV_SECRET"
    fallback_key = "TESTING_ENV_DEFAULT"
    monkeypatch.delenv(secret_key, raising=False)
    monkeypatch.delenv(fallback_key, raising=False)
    write(tmp_path / ".env", f"{secret_key}=from_dotenv\n")
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        f"""
        name: EnvAgent
        instructions: Inline
        variables:
          secret: ${{{secret_key}}}
          fallback: ${{{fallback_key}:-fallback_value}}
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.context == {"secret": "from_dotenv", "fallback": "fallback_value"}
    os.environ.pop(secret_key, None)


def test_loader_requires_env_vars(tmp_path, monkeypatch):
    required_key = "REQUIRED_ENV_KEY"
    monkeypatch.delenv(required_key, raising=False)
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        f"""
        name: EnvAgent
        instructions: Inline
        variables:
          needed: ${{{required_key}}}
        """,
    )
    with pytest.raises(ValueError) as exc:
        load_agent_spec_from_yaml(str(yaml_path))
    assert required_key in str(exc.value)


def test_loader_discovers_assets_and_applies_config(tmp_path):
    tools_dir = tmp_path / "tools"
    extra_tools_dir = tmp_path / "extra_tools"
    guardrails_dir = tmp_path / "guardrails"
    server_functions_dir = tmp_path / "server_functions"
    extra_server_functions_dir = tmp_path / "extra_server_functions"
    for path in (
        tools_dir,
        extra_tools_dir,
        guardrails_dir,
        server_functions_dir,
        extra_server_functions_dir,
    ):
        path.mkdir()
    write(
        tools_dir / "primary.py",
        """
        from omniagents.core.tools.discovery import function_tool

        @function_tool
        async def alpha_tool():
            return "alpha"
        """,
    )
    write(
        extra_tools_dir / "extra.py",
        """
        from omniagents.core.tools.discovery import function_tool

        @function_tool
        async def beta_tool():
            return "beta"
        """,
    )
    write(
        guardrails_dir / "custom_guardrails.py",
        """
        from agents.guardrail import GuardrailFunctionOutput, input_guardrail, output_guardrail

        @input_guardrail(name="input_checker")
        def input_checker(context, agent, value):
            return GuardrailFunctionOutput(output_info="ok", tripwire_triggered=False)

        @output_guardrail(name="output_checker")
        def output_checker(context, agent, value):
            return GuardrailFunctionOutput(output_info="ok", tripwire_triggered=False)
        """,
    )
    write(
        server_functions_dir / "primary.py",
        """
        from omniagents.core.server_functions.discovery import server_function

        @server_function()
        def register_alpha(value: int) -> int:
            return value + 1
        """,
    )
    write(
        extra_server_functions_dir / "extra.py",
        """
        from omniagents.core.server_functions.discovery import server_function

        @server_function()
        def beta_function(text: str) -> str:
            return text.upper()
        """,
    )
    handoff_path = tmp_path / "handoff.yaml"
    write(
        handoff_path,
        """
        name: HandoffAgent
        instructions: Handoff instructions
        """,
    )
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: ComplexAgent
        model: gpt-4o-mini
        instructions: Inline instructions
        label: Label
        description: Something
        use_safe_agent: false
        page_title_suffix: Beta
        context_window: 1234
        tools:
          - alpha_tool
          - beta_tool
        additional_tools_dirs:
          - extra_tools
        input_guardrails:
          - input_checker
        output_guardrails:
          - output_checker
        server_functions:
          - register_alpha
          - beta_function
        additional_server_functions_dirs:
          - extra_server_functions
        handoffs:
          - yaml: handoff.yaml
            tool_name_override: ExternalOverride
          - inline:
              name: InlineAgent
              instructions: Inline handoff
              tools:
                - alpha_tool
            tool_description_override: InlineOverride
        safe_agent_options:
          skip_approvals: true
          halt_on_rejection: false
          safe_tool_names:
            - alpha_tool
          safe_tool_patterns:
            - beta.*
        mcp_servers:
          - name: primary
            type: http
            params:
              url: http://svc
            options:
              retry: true
          - name: invalid
            type: ""
        mcp_config:
          endpoint: http://mcp
        settings:
          - key: custom_setting
            label: Custom
        model_settings:
          temperature: "warm"
        tool_use_behavior:
          stop_at_tool_names:
            - alpha_tool
        output_schema:
          properties:
            foo:
              type: string
        output_type_strict: false
        prompt:
          id: sample_prompt
          version: "1"
          variables:
            foo: 1
        icon_map:
          alpha_tool: pin
        theme_color: "#FF0000"
        """,
    )
    project_defaults = {
        "settings": [{"key": "project_setting", "label": "Project"}],
        "theme_color": "#00FF00",
        "layout": "tabs",
        "tracing": {"enabled": True},
    }
    existing_modules = set(sys.modules)
    try:
        spec = load_agent_spec_from_yaml(
            str(yaml_path), project_defaults=project_defaults
        )
        tool_names = [tool.name for tool in spec.available_tools]
        assert tool_names == ["alpha_tool", "beta_tool"]
        input_names = [guard.get_name() for guard in spec.input_guardrails]
        output_names = [guard.get_name() for guard in spec.output_guardrails]
        assert input_names == ["input_checker"]
        assert output_names == ["output_checker"]
        assert spec.model_name == "gpt-4o-mini"
        assert spec.use_safe_agent is False
        assert spec.safe_agent_options.skip_approvals is True
        assert spec.safe_agent_options.halt_on_rejection is False
        assert spec.safe_agent_options.safe_tool_names == ["alpha_tool"]
        assert spec.safe_agent_options.safe_tool_patterns == ["beta.*"]
        assert spec.tool_use_behavior == {"stop_at_tool_names": ["alpha_tool"]}
        assert spec.output_type_strict is False
        assert getattr(spec.output_type, "__annotations__", {}) == {"foo": str}
        assert spec.prompt == {
            "id": "sample_prompt",
            "version": "1",
            "variables": {"foo": 1},
        }
        assert spec.icon_map == {"alpha_tool": "pin"}
        assert spec.theme_color == "#FF0000"
        assert spec.layout == "tabs"
        assert spec.page_title_suffix == "Beta"
        assert spec.context_window == 1234
        assert spec.tracing_config == {"enabled": True}
        assert spec.mcp_config == {"endpoint": "http://mcp"}
        assert len(spec.mcp_servers) == 1
        server = spec.mcp_servers[0]
        assert server.name == "primary"
        assert server.type == "http"
        assert server.params == {"url": "http://svc"}
        assert server.options == {"retry": True}
        assert spec.model_settings is None
        assert spec.get_agent_instructions() == "Inline instructions"
        assert spec.description == "Something"
        assert spec.label == "Label"
        assert spec.client_storage_prefix.startswith("complexagent")
        assert spec.agent_slug == "complexagent"
        keys = {field.key for field in spec.settings_fields}
        assert {"openai_base_url", "openai_api_key", "project_setting", "custom_setting"} <= keys
        assert spec.register_server_functions is not None
        class Service:
            def __init__(self):
                self.calls = []

            def register_server_function(self, name, func, **meta):
                self.calls.append((name, func, meta))

        service = Service()
        spec.register_server_functions(service)
        registered = {name for name, _, _ in service.calls}
        assert registered == {"register_alpha", "beta_function"}
        assert len(spec.handoff_configs) == 2
        first_handoff = spec.handoff_configs[0]
        second_handoff = spec.handoff_configs[1]
        assert first_handoff.yaml_path == str(handoff_path.resolve())
        assert first_handoff.tool_name_override == "ExternalOverride"
        assert second_handoff.inline_spec.name == "InlineAgent"
        assert second_handoff.tool_description_override == "InlineOverride"
        inline_tools = [tool.name for tool in second_handoff.inline_spec.available_tools]
        assert inline_tools == ["alpha_tool"]
    finally:
        for name in list(sys.modules):
            if name not in existing_modules and (
                name.startswith("guardrails")
                or name.startswith("server_functions")
                or name.startswith("extra_server_functions")
            ):
                sys.modules.pop(name, None)


def test_loader_handles_remote_agent_config(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: RemoteAgent
        instructions: Should be ignored
        remote_agent:
          ws_url: ws://example
          auth_token_env: REMOTE_TOKEN
        tools:
          - local_tool
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.remote_ws_url == "ws://example"
    assert spec.remote_auth_token_key == "REMOTE_TOKEN"
    assert spec.available_tools == []
    assert spec.model_name is None
    assert spec.get_agent_instructions() == ""
    assert spec.handoff_configs == []


def test_loader_skips_missing_handoff_yaml(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: BaseAgent
        instructions: Inline
        handoffs:
          - yaml: missing.yaml
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.handoff_configs == []


def test_loader_detects_circular_reference(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: CircularAgent
        instructions: Inline
        """,
    )
    stack = {yaml_path.resolve()}
    with pytest.raises(ValueError):
        load_agent_spec_from_yaml(str(yaml_path), stack)
