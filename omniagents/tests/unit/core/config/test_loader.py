import os
import sys
import textwrap

import pytest

from omniagents.core.config.loader import (
    load_agent_spec_from_yaml,
    _parse_inline_agent_spec,
)


def write(path, content):
    path.write_text(textwrap.dedent(content))


def test_parse_inline_agent_spec_with_all_fields(tmp_path):
    base_dir = tmp_path
    tools_dir = base_dir / "tools"
    tools_dir.mkdir()
    write(
        tools_dir / "calc.py",
        """
        from omniagents.core.tools.discovery import function_tool

        @function_tool
        async def calc():
            return 42
        """,
    )
    write(base_dir / "inline.md", "Inline instructions")
    config = {
        "name": "Helper",
        "instructions_file": "inline.md",
        "welcome_text": "Welcome",
        "tools": ["calc"],
        "model": "gpt-4o-mini",
        "model_settings": {"temperature": 0.6},
        "max_turns": 3,
        "handoff_description": "Escalate when stuck",
        "reset_tool_choice": False,
        "tool_use_behavior": "stop_on_first_tool",
        "use_safe_agent": False,
        "safe_agent_options": {
            "skip_approvals": True,
            "halt_on_rejection": False,
            "safe_tool_names": ["calc"],
            "safe_tool_patterns": ["calc_.*"],
        },
    }
    existing_modules = set(sys.modules)
    try:
        spec = _parse_inline_agent_spec(config, base_dir, set())
    finally:
        for name in list(sys.modules):
            if name not in existing_modules and name.startswith("tools"):
                sys.modules.pop(name, None)
    assert spec.name == "Helper"
    assert spec.get_agent_instructions() == "Inline instructions"
    assert [tool.name for tool in spec.available_tools] == ["calc"]
    assert spec.model_name == "gpt-4o-mini"
    assert spec.model_settings == {"temperature": 0.6}
    assert spec.max_turns == 3
    assert spec.reset_tool_choice is False
    assert spec.tool_use_behavior == "stop_on_first_tool"
    assert spec.handoff_description == "Escalate when stuck"
    assert spec.use_safe_agent is False
    assert spec.safe_agent_options.skip_approvals is True
    assert spec.safe_agent_options.halt_on_rejection is False
    assert spec.safe_agent_options.safe_tool_names == ["calc"]
    assert spec.safe_agent_options.safe_tool_patterns == ["calc_.*"]
    assert spec.agent_slug == "helper"


def test_parse_inline_agent_spec_supports_nested_inline_handoffs(tmp_path):
    base_dir = tmp_path
    tools_dir = base_dir / "tools"
    tools_dir.mkdir()
    write(
        tools_dir / "alpha.py",
        """
        from omniagents.core.tools.discovery import function_tool

        @function_tool
        async def alpha():
            return "alpha"
        """,
    )
    config = {
        "name": "Parent",
        "instructions": "Parent inline",
        "tools": ["alpha"],
        "handoffs": [
            {
                "inline": {
                    "name": "Child",
                    "instructions": "Child inline",
                    "tools": ["alpha"],
                },
                "tool_description_override": "Nested inline",
            }
        ],
    }
    existing_modules = set(sys.modules)
    try:
        spec = _parse_inline_agent_spec(config, base_dir, set())
    finally:
        for name in list(sys.modules):
            if name not in existing_modules and name.startswith("tools"):
                sys.modules.pop(name, None)
    assert [tool.name for tool in spec.available_tools] == ["alpha"]
    assert len(spec.handoff_configs) == 1
    nested = spec.handoff_configs[0]
    assert nested.inline_spec is not None
    assert nested.inline_spec.name == "Child"
    assert nested.inline_spec.get_agent_instructions() == "Child inline"
    assert nested.inline_spec.available_tools[0].name == "alpha"
    assert nested.tool_description_override == "Nested inline"


def test_parse_inline_agent_spec_handles_invalid_tools_list(tmp_path, capsys):
    config = {"instructions": "Inline", "tools": "alpha"}
    spec = _parse_inline_agent_spec(config, tmp_path, set())
    captured = capsys.readouterr()
    assert "should be a list" in captured.out
    assert spec.available_tools == []


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
    assert spec.agent_slug.startswith("testagent")


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


def test_loader_handles_nested_env_substitution(tmp_path, monkeypatch):
    key = "NESTED_ENV_KEY"
    monkeypatch.setenv(key, "actual")
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        f"""
        name: NestedAgent
        instructions: Inline
        variables:
          mapping:
            inner: ${{{key}}}
          sequence:
            - first
            - ${{UNKNOWN_KEY:-fallback}}
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.context == {
        "mapping": {"inner": "actual"},
        "sequence": ["first", "fallback"],
    }


def test_loader_merges_settings_fields_with_project_defaults(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: SettingsAgent
        instructions: Inline
        settings:
          - key: override_me
            label: Agent Override
            default: agent
          - key: custom_setting
            label: Agent Custom
            secret: true
        """,
    )
    project_defaults = {
        "settings": [
            {"key": "project_setting", "label": "Project", "default": "project"},
            {"key": "override_me", "label": "Project Override", "default": "project"},
        ],
    }
    spec = load_agent_spec_from_yaml(str(yaml_path), project_defaults=project_defaults)
    fields = {field.key: field for field in spec.settings_fields}
    assert fields["override_me"].label == "Agent Override"
    assert fields["override_me"].default == "agent"
    assert fields["project_setting"].label == "Project"
    assert fields["project_setting"].default == "project"
    assert fields["custom_setting"].secret is True
    assert "openai_api_key" in fields


def test_loader_raises_with_custom_env_error(tmp_path, monkeypatch):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: ErrorAgent
        instructions: Inline
        variables:
          something: ${MISSING_KEY:?Custom message}
        """,
    )
    with pytest.raises(ValueError) as exc:
        load_agent_spec_from_yaml(str(yaml_path))
    assert "Custom message" in str(exc.value)


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
        """,
    )
    project_defaults = {
        "settings": [{"key": "project_setting", "label": "Project"}],
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
        assert spec.agent_slug.startswith("complexagent")
        keys = {field.key for field in spec.settings_fields}
        assert {
            "openai_base_url",
            "openai_api_key",
            "project_setting",
            "custom_setting",
        } <= keys
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
        inline_tools = [
            tool.name for tool in second_handoff.inline_spec.available_tools
        ]
        assert inline_tools == ["alpha_tool"]
    finally:
        for name in list(sys.modules):
            if name not in existing_modules and (
                name.startswith("guardrails")
                or name.startswith("server_functions")
                or name.startswith("extra_server_functions")
            ):
                sys.modules.pop(name, None)


def test_loader_warns_for_missing_tools(tmp_path, capsys):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: MissingTools
        instructions: Inline
        tools:
          - not_a_tool
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    captured = capsys.readouterr()
    assert "were not found" in captured.out
    assert "not_a_tool" in captured.out
    assert spec.available_tools == []


def test_loader_warns_for_missing_guardrails(tmp_path, capsys):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: MissingGuardrails
        instructions: Inline
        input_guardrails:
          - missing_input
        output_guardrails:
          - missing_output
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    captured = capsys.readouterr()
    assert "input guardrails listed" in captured.out
    assert "missing_input" in captured.out
    assert "output guardrails listed" in captured.out
    assert "missing_output" in captured.out
    assert spec.input_guardrails == []
    assert spec.output_guardrails == []


def test_loader_reports_yaml_syntax_errors(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text("name: [broken\n")
    with pytest.raises(ValueError) as exc:
        load_agent_spec_from_yaml(str(yaml_path))
    assert "Error parsing YAML file" in str(exc.value)


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
          identity_allowed_roles: [operator, Operator, operator]
        tools:
          - local_tool
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.remote_ws_url == "ws://example"
    assert spec.remote_auth_token_key == "REMOTE_TOKEN"
    assert spec.remote_identity_allowed_roles == ["operator", "Operator"]
    assert spec.available_tools == []
    assert spec.model_name is None
    assert spec.get_agent_instructions() == ""
    assert spec.handoff_configs == []


def test_loader_rejects_invalid_remote_identity_role_allowlist(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: RemoteAgent
        remote_agent:
          ws_url: ws://example
          identity_allowed_roles: admin
        """,
    )
    with pytest.raises(ValueError, match="identity_allowed_roles must be a list"):
        load_agent_spec_from_yaml(str(yaml_path))


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


def test_loader_reports_missing_instructions_file(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: MissingInstrAgent
        instructions_file: missing.txt
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    message = spec.get_agent_instructions()
    assert "Error: Instructions file" in message
    assert "missing.txt" in message


def test_loader_uses_default_instructions(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: DefaultInstrAgent
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.get_agent_instructions() == "You are a helpful assistant."


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


def test_loader_rejects_invalid_yaml_shape(tmp_path):
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        - unexpected_list
        """,
    )
    with pytest.raises(ValueError) as exc:
        load_agent_spec_from_yaml(str(yaml_path))
    assert "Invalid YAML format" in str(exc.value)


# --- Realtime Agent Configuration Tests ---


def test_load_realtime_config_from_yaml(tmp_path):
    """Test loading basic realtime configuration from YAML."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: VoiceAgent
        instructions: You are a voice assistant
        realtime_mode: true
        realtime_settings:
          voice: shimmer
          modalities: ["audio"]
          input_audio_format: pcm16
          output_audio_format: pcm16
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.realtime_mode is True
    assert spec.realtime_settings is not None
    assert spec.realtime_settings.voice == "shimmer"
    assert spec.realtime_settings.modalities == ["audio"]
    assert spec.realtime_settings.input_audio_format == "pcm16"
    assert spec.realtime_settings.output_audio_format == "pcm16"


def test_realtime_config_with_all_settings(tmp_path):
    """Test realtime configuration with all available settings."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: AdvancedVoiceAgent
        instructions: Advanced voice assistant
        realtime_mode: true
        realtime_settings:
          model_name: gpt-4o-mini-realtime-preview
          voice: alloy
          modalities: ["text", "audio"]
          input_audio_format: g711_ulaw
          output_audio_format: g711_alaw
          turn_detection:
            type: server_vad
            threshold: 0.5
            silence_duration_ms: 700
            prefix_padding_ms: 300
          input_audio_transcription:
            model: whisper-1
            language: en
          temperature: 0.7
          max_output_tokens: 1000
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.realtime_mode is True
    assert spec.realtime_settings is not None
    assert spec.realtime_settings.model_name == "gpt-4o-mini-realtime-preview"
    assert spec.realtime_settings.voice == "alloy"
    assert spec.realtime_settings.modalities == ["text", "audio"]
    assert spec.realtime_settings.input_audio_format == "g711_ulaw"
    assert spec.realtime_settings.output_audio_format == "g711_alaw"
    assert spec.realtime_settings.turn_detection == {
        "type": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 700,
        "prefix_padding_ms": 300,
    }
    assert spec.realtime_settings.input_audio_transcription == {
        "model": "whisper-1",
        "language": "en",
    }
    assert spec.realtime_settings.temperature == 0.7
    assert spec.realtime_settings.max_output_tokens == 1000


def test_realtime_config_with_defaults(tmp_path):
    """Test that realtime_mode=true with no settings uses defaults."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: DefaultVoiceAgent
        instructions: Default settings voice agent
        realtime_mode: true
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.realtime_mode is True
    assert spec.realtime_settings is not None
    assert spec.realtime_settings.model_name == "gpt-realtime"
    assert spec.realtime_settings.voice == "alloy"
    assert spec.realtime_settings.modalities == ["audio"]
    assert spec.realtime_settings.input_audio_format == "pcm16"
    assert spec.realtime_settings.output_audio_format == "pcm16"


def test_realtime_mode_false_no_settings(tmp_path):
    """Test that realtime_mode=false means no realtime settings."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: TextAgent
        instructions: Regular text agent
        realtime_mode: false
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.realtime_mode is False
    assert spec.realtime_settings is None


def test_realtime_mode_missing_defaults_to_false(tmp_path):
    """Test that missing realtime_mode defaults to False."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: RegularAgent
        instructions: No realtime specified
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.realtime_mode is False
    assert spec.realtime_settings is None


def test_realtime_config_with_env_vars(tmp_path, monkeypatch):
    """Test that realtime settings support environment variable substitution."""
    monkeypatch.setenv("VOICE_MODEL", "gpt-4o-realtime-preview")
    monkeypatch.setenv("VOICE_CHOICE", "echo")
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: EnvVoiceAgent
        instructions: Voice agent with env vars
        realtime_mode: true
        realtime_settings:
          model_name: ${VOICE_MODEL}
          voice: ${VOICE_CHOICE}
          temperature: 0.8
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.realtime_mode is True
    assert spec.realtime_settings.model_name == "gpt-4o-realtime-preview"
    assert spec.realtime_settings.voice == "echo"
    assert spec.realtime_settings.temperature == 0.8


def test_realtime_invalid_audio_format_uses_default(tmp_path, capsys):
    """Test that invalid audio formats fall back to defaults with warning."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: InvalidFormatAgent
        instructions: Agent with invalid audio format
        realtime_mode: true
        realtime_settings:
          input_audio_format: invalid_format
          output_audio_format: also_invalid
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    captured = capsys.readouterr()
    assert "Invalid input_audio_format" in captured.out
    assert "Invalid output_audio_format" in captured.out
    assert spec.realtime_settings.input_audio_format == "pcm16"
    assert spec.realtime_settings.output_audio_format == "pcm16"


def test_realtime_invalid_modalities_uses_default(tmp_path, capsys):
    """Test that invalid modalities fall back to defaults with warning."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: InvalidModalitiesAgent
        instructions: Agent with invalid modalities
        realtime_mode: true
        realtime_settings:
          modalities: ["invalid", "modality"]
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    captured = capsys.readouterr()
    assert "Invalid modalities" in captured.out
    assert spec.realtime_settings.modalities == ["audio"]


def test_realtime_mode_invalid_type_defaults_to_false(tmp_path, capsys):
    """Test that invalid realtime_mode type defaults to False with warning."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: InvalidModeAgent
        instructions: Agent with invalid mode type
        realtime_mode: "yes"
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    captured = capsys.readouterr()
    assert "should be a boolean" in captured.out
    assert spec.realtime_mode is False


def test_realtime_settings_invalid_type_uses_defaults(tmp_path, capsys):
    """Test that invalid realtime_settings type uses defaults with warning."""
    yaml_path = tmp_path / "agent.yaml"
    write(
        yaml_path,
        """
        name: InvalidSettingsAgent
        instructions: Agent with invalid settings type
        realtime_mode: true
        realtime_settings: "not a dict"
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    captured = capsys.readouterr()
    assert "should be a dictionary" in captured.out
    assert spec.realtime_settings is not None
    assert spec.realtime_settings.voice == "alloy"  # Default


def test_loader_sets_instruction_search_paths_from_instructions_file(tmp_path):
    """Search paths anchor at the instructions file's own directory."""
    agent_dir = tmp_path / "agents" / "primary"
    agent_dir.mkdir(parents=True)
    prompts_dir = agent_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "instructions.md").write_text("Hi", encoding="utf-8")
    yaml_path = agent_dir / "agent.yml"
    write(
        yaml_path,
        """
        name: SearchPathAgent
        instructions_file: prompts/instructions.md
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.instructions_search_paths == [
        prompts_dir,
        prompts_dir / "_shared",
        agent_dir / "_shared",
    ]


def test_loader_search_paths_include_project_instruction_fragments(tmp_path):
    """paths.instruction_fragments dirs are appended via project_defaults."""
    agent_dir = tmp_path / "agents" / "primary"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instructions.md").write_text("Hi", encoding="utf-8")
    fragments_dir = tmp_path / "fragments"
    yaml_path = agent_dir / "agent.yml"
    write(
        yaml_path,
        """
        name: FragmentAgent
        instructions_file: instructions.md
        """,
    )
    spec = load_agent_spec_from_yaml(
        str(yaml_path),
        project_defaults={"instruction_fragments": [str(fragments_dir)]},
    )
    assert spec.instructions_search_paths == [
        agent_dir,
        agent_dir / "_shared",
        tmp_path / "agents" / "_shared",
        fragments_dir,
    ]


def test_loader_renders_include_from_shared_sibling_dir(tmp_path):
    """End-to-end: instructions_file with {% include %} resolves agents/_shared."""
    import asyncio

    from omniagents.core.utils.jinja_instructions import process_instructions

    agent_dir = tmp_path / "agents" / "voice"
    shared_dir = tmp_path / "agents" / "_shared"
    agent_dir.mkdir(parents=True)
    shared_dir.mkdir(parents=True)
    (shared_dir / "core.md").write_text("Shared rules.", encoding="utf-8")
    (agent_dir / "instructions.md").write_text(
        "Voice agent.\n{% include 'core.md' %}", encoding="utf-8"
    )
    yaml_path = agent_dir / "agent.yml"
    write(
        yaml_path,
        """
        name: VoiceAgent
        instructions_file: instructions.md
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    renderer = process_instructions(
        spec.get_agent_instructions(),
        search_paths=spec.instructions_search_paths,
    )
    rendered = asyncio.run(renderer(None, None))
    assert rendered == "Voice agent.\nShared rules."


def test_loader_inline_instructions_search_paths_anchor_at_yaml_dir(tmp_path):
    yaml_path = tmp_path / "agent.yml"
    write(
        yaml_path,
        """
        name: InlineAgent
        instructions: Just do it
        """,
    )
    spec = load_agent_spec_from_yaml(str(yaml_path))
    assert spec.instructions_search_paths == [
        tmp_path,
        tmp_path / "_shared",
        tmp_path.parent / "_shared",
    ]
