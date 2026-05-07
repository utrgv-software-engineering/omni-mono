try:
    from .agents.specs import AgentSpec, SafeAgentOptions, SettingField  # type: ignore
except Exception:
    AgentSpec = SafeAgentOptions = SettingField = None  # type: ignore

try:
    from .config.loader import load_agent_spec_from_yaml  # type: ignore
except Exception:
    load_agent_spec_from_yaml = None  # type: ignore

try:
    from .tools.discovery import function_tool  # type: ignore
except Exception:
    function_tool = None  # type: ignore

try:
    from .server_functions.discovery import (  # type: ignore
        server_function,
        discover_server_functions_in_dir,
        build_server_functions_registrar,
        make_server_functions_registrar_from_dir,
        register_server_functions_from_dir,
    )
except Exception:
    server_function = None  # type: ignore
    discover_server_functions_in_dir = None  # type: ignore
    build_server_functions_registrar = None  # type: ignore
    make_server_functions_registrar_from_dir = None  # type: ignore
    register_server_functions_from_dir = None  # type: ignore

try:
    from .runtime_hooks.discovery import (  # type: ignore
        runtime_hook,
        discover_runtime_hooks_in_dir,
        build_runtime_hooks_registrar,
        register_runtime_hooks_from_dir,
    )
except Exception:
    runtime_hook = None  # type: ignore
    discover_runtime_hooks_in_dir = None  # type: ignore
    build_runtime_hooks_registrar = None  # type: ignore
    register_runtime_hooks_from_dir = None  # type: ignore

try:
    from omniagents.builtin.tools.artifacts import display_artifact  # type: ignore
    from .tools.artifacts import set_artifact_callback, emit_artifact  # type: ignore
except Exception:
    display_artifact = set_artifact_callback = emit_artifact = None  # type: ignore
# Optionally expose other useful components like SafeAgentOptions etc.
# from .agents.specs import SafeAgentOptions # Example if exposing SafeAgentOptions

try:
    from agents.guardrail import input_guardrail, output_guardrail, GuardrailFunctionOutput  # type: ignore
except Exception:
    input_guardrail = None  # type: ignore
    output_guardrail = None  # type: ignore
    GuardrailFunctionOutput = None  # type: ignore

try:
    from agents.tool_guardrails import (  # type: ignore
        ToolInputGuardrail,
        ToolOutputGuardrail,
        tool_input_guardrail,
        tool_output_guardrail,
        ToolGuardrailFunctionOutput,
    )
except Exception:
    ToolInputGuardrail = None  # type: ignore
    ToolOutputGuardrail = None  # type: ignore
    tool_input_guardrail = None  # type: ignore
    tool_output_guardrail = None  # type: ignore
    ToolGuardrailFunctionOutput = None  # type: ignore

# Re-export SDK apply_patch primitives so user code building file-edit
# tools can hand-roll an editor without importing from agents.* directly.
try:
    from agents.editor import (  # type: ignore
        ApplyPatchEditor,
        ApplyPatchOperation,
        ApplyPatchResult,
    )
    from agents.apply_diff import apply_diff  # type: ignore
except Exception:
    ApplyPatchEditor = None  # type: ignore
    ApplyPatchOperation = None  # type: ignore
    ApplyPatchResult = None  # type: ignore
    apply_diff = None  # type: ignore

# Re-export SDK lifecycle hook base classes for user subclassing.
try:
    from agents.lifecycle import (  # type: ignore
        AgentHooks,
        AgentHooksBase,
        RunHooks,
        RunHooksBase,
    )
except Exception:
    AgentHooks = None  # type: ignore
    AgentHooksBase = None  # type: ignore
    RunHooks = None  # type: ignore
    RunHooksBase = None  # type: ignore
