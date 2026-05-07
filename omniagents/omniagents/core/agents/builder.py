"""Core agent building functionality."""

import asyncio
import json as _json
import os
from typing import Dict, Any, Optional, TYPE_CHECKING, List, Set
from pathlib import Path

from omniagents.core.utils.logging import get_logger
from omniagents.core.agents.mcp import build_mcp_servers

if TYPE_CHECKING:
    from agents.mcp import MCPServer
    from omniagents.core.agents.specs import AgentSpec, AgentLike, HandoffConfig
    from agents.handoffs import Handoff

logger = get_logger(__name__)


def _apply_tool_timeouts(tools: List[Any], timeouts: Dict[str, Any]) -> List[Any]:
    """Wrap tool invoke methods with ``asyncio.wait_for`` timeouts.

    *timeouts* maps tool names to seconds.  The special key ``"default"``
    sets the fallback for tools without an explicit entry.
    """
    from agents.tool import FunctionTool

    default_timeout = timeouts.get("default")
    wrapped: List[Any] = []
    for tool in tools:
        name = getattr(tool, "name", "")
        timeout = timeouts.get(name, default_timeout)
        if timeout is None or not isinstance(tool, FunctionTool):
            wrapped.append(tool)
            continue
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            wrapped.append(tool)
            continue

        original_invoke = tool.on_invoke_tool

        def _make_timed_invoke(orig, tname, tout):
            async def timed_invoke(context, params_json: str):
                try:
                    return await asyncio.wait_for(
                        orig(context, params_json), timeout=tout
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Tool '{tname}' timed out after {tout}s")
                    return _json.dumps(
                        {
                            "error": "TOOL_TIMEOUT",
                            "tool": tname,
                            "timeout_seconds": tout,
                        }
                    )

            return timed_invoke

        timed_tool = FunctionTool(
            name=tool.name,
            description=tool.description,
            params_json_schema=dict(tool.params_json_schema),
            on_invoke_tool=_make_timed_invoke(original_invoke, name, timeout),
            strict_json_schema=tool.strict_json_schema,
        )
        # Preserve custom attributes
        for attr in (
            "_is_omniagents_core_tool",
            "_original_func",
            "_client_status_text",
        ):
            if hasattr(tool, attr):
                setattr(timed_tool, attr, getattr(tool, attr))
        timed_tool._timeout_seconds = timeout
        wrapped.append(timed_tool)
    return wrapped


async def _build_handoffs_from_configs(
    handoff_configs: List["HandoffConfig"],
    settings: Dict[str, Any],
    approval_callback: Optional[Any] = None,
    _loading_stack: Optional[Set[Path]] = None,
    parent_spec: Optional["AgentSpec"] = None,
) -> List["Handoff"]:
    """Build handoff objects from HandoffConfig list."""
    from agents import handoff
    from omniagents.core.config.loader import load_agent_spec_from_yaml

    if _loading_stack is None:
        _loading_stack = set()

    handoffs = []
    for handoff_config in handoff_configs:
        try:
            # Check if this is an external YAML reference or inline agent
            if handoff_config.yaml_path:
                # External YAML reference
                yaml_path = Path(handoff_config.yaml_path).resolve()

                # Add to loading stack to track circular references
                if yaml_path in _loading_stack:
                    logger.warning(
                        f"Circular reference detected: {yaml_path} is already being loaded. Skipping."
                    )
                    continue

                # Load the handoff agent spec with registries from parent spec
                # This ensures handoff agents use the same project-level tool registry
                tool_registry = parent_spec._tool_registry if parent_spec else None
                guardrails_registry = (
                    parent_spec._guardrails_registry if parent_spec else None
                )
                server_functions_registry = (
                    parent_spec._server_functions_registry if parent_spec else None
                )

                handoff_spec = load_agent_spec_from_yaml(
                    str(yaml_path),
                    tool_registry=tool_registry,
                    guardrails_registry=guardrails_registry,
                    server_functions_registry=server_functions_registry,
                )

                # Add this path to building stack for recursive building
                new_loading_stack = _loading_stack.copy()
                new_loading_stack.add(yaml_path)

            elif handoff_config.inline_spec:
                # Inline agent definition
                handoff_spec = handoff_config.inline_spec
                new_loading_stack = _loading_stack.copy()

            else:
                logger.error("HandoffConfig has neither yaml_path nor inline_spec")
                continue

            # Build the handoff agent
            handoff_agent = await _default_build_agent(
                settings=settings,
                mcp_servers=None,
                spec=handoff_spec,
                approval_callback=approval_callback,
                _loading_stack=new_loading_stack,
            )

            # Create the handoff with optional overrides
            handoff_obj = handoff(
                agent=handoff_agent,
                tool_name_override=handoff_config.tool_name_override,
                tool_description_override=handoff_config.tool_description_override,
            )
            handoffs.append(handoff_obj)

        except Exception as e:
            agent_identifier = (
                handoff_config.yaml_path or handoff_config.inline_spec.name
                if handoff_config.inline_spec
                else "unknown"
            )
            logger.error(
                f"Failed to build handoff agent {agent_identifier}: {e}", exc_info=True
            )
            continue

    return handoffs


async def _default_build_agent(
    settings: Dict[str, Any],
    mcp_servers: Optional[Any],
    spec: "AgentSpec",
    approval_callback: Optional[Any] = None,
    _loading_stack: Optional[Set[Path]] = None,
    session: Optional[Any] = None,
) -> "AgentLike":
    """Internal default function to build the agent instance.

    Args:
        settings: Settings dictionary
        mcp_servers: Pre-built MCP servers or None
        spec: Agent specification
        approval_callback: Callback for tool approval
        _loading_stack: Internal stack for circular reference detection
        session: Optional session with model overrides (active_model, reasoning_effort)
    """
    from agents import Agent, set_default_openai_key
    from agents.model_settings import ModelSettings
    from agents.models._openai_shared import get_default_openai_client
    from omniagents.core.utils.jinja_instructions import process_instructions

    logger.debug(f"Building agent '{spec.name}' using internal default builder...")

    # Merge incoming settings with environment and SettingField defaults.
    # This ensures server/TUI paths (which often pass `{}`) still honor .env
    # for all declared SettingField keys. Incoming settings take precedence.
    try:
        effective_settings: Dict[str, Any] = dict(settings or {})
        for field in getattr(spec, "settings_fields", []) or []:
            key = getattr(field, "key", None)
            if not key:
                continue
            if key not in effective_settings or effective_settings[key] in (None, ""):
                env_val = os.getenv(key.upper())
                if env_val not in (None, ""):
                    effective_settings[key] = env_val
                else:
                    # Fall back to declared default (may be empty string)
                    effective_settings[key] = getattr(field, "default", None)
        # Use merged view from here on
        settings = effective_settings
    except Exception:
        # Best-effort only; never fail agent construction on merge errors
        pass

    # Ensure correct OpenAI base URL variable is set for the SDK.
    # We intentionally support ONLY the canonical OPENAI_BASE_URL.
    try:
        base_url = settings.get("openai_base_url") or os.getenv("OPENAI_BASE_URL")
        if base_url and not os.getenv("OPENAI_BASE_URL"):
            os.environ["OPENAI_BASE_URL"] = base_url
            logger.debug("Configured OPENAI_BASE_URL from settings/environment")
    except Exception:
        # Never fail agent construction due to env wiring
        pass

    # Tool-approval policy (formerly SafeAgent) is applied per-session in
    # AgentService via FunctionTool.needs_approval — see core/agents/approval.py.

    # Get core config from spec/settings
    agent_name = spec.name
    model_name = spec.model_name or settings.get("model_name", "gpt-5.2")

    # Session-level model override
    session_model_config = None
    if session is not None:
        session_model_config = getattr(session, "model_config", None)
        if session_model_config and session_model_config.get("model"):
            model_name = session_model_config["model"]
            logger.debug(f"Using session model override: {model_name}")

    instructions = process_instructions(spec.get_agent_instructions())
    tools = spec.available_tools

    # Apply spec-level tool guardrails to every FunctionTool the agent uses.
    # SDK 0.7+ supports per-tool input/output guardrails; we propagate the
    # agent-level lists onto each tool that doesn't already carry them.
    if spec.tool_input_guardrails or spec.tool_output_guardrails:
        for _tool in tools:
            if hasattr(_tool, "tool_input_guardrails") and spec.tool_input_guardrails:
                existing = list(_tool.tool_input_guardrails or [])
                _tool.tool_input_guardrails = existing + list(
                    spec.tool_input_guardrails
                )
            if hasattr(_tool, "tool_output_guardrails") and spec.tool_output_guardrails:
                existing = list(_tool.tool_output_guardrails or [])
                _tool.tool_output_guardrails = existing + list(
                    spec.tool_output_guardrails
                )
    provided_mcp_servers: List[Any] = []
    # Check if mcp_servers was explicitly provided (including empty list)
    # None means "use spec.mcp_servers", empty list means "no servers"
    if mcp_servers is not None:
        if isinstance(mcp_servers, (list, tuple)):
            provided_mcp_servers = list(mcp_servers)
        else:
            provided_mcp_servers = [mcp_servers]
    elif spec.mcp_servers:
        try:
            provided_mcp_servers = build_mcp_servers(spec, settings)
        except Exception as exc:
            logger.error(
                f"Failed to instantiate MCP servers for agent '{spec.name}': {exc}"
            )
            provided_mcp_servers = []
    mcp_servers_list = provided_mcp_servers
    handoff_description = spec.handoff_description
    reset_tool_choice = spec.reset_tool_choice
    tool_use_behavior = spec.tool_use_behavior
    output_type = spec.output_type
    output_type_strict = spec.output_type_strict
    prompt = spec.prompt
    input_guardrails = spec.input_guardrails
    output_guardrails = spec.output_guardrails

    # MCP config - merge spec config with any defaults
    mcp_config = spec.mcp_config if spec.mcp_config else {}

    # Handle output_type with AgentOutputSchema if needed
    if output_type is not None:
        from agents.agent_output import AgentOutputSchema

        output_type = AgentOutputSchema(
            output_type, strict_json_schema=output_type_strict
        )

    # Build handoffs from configs
    handoffs = []
    if spec.handoff_configs:
        handoffs = await _build_handoffs_from_configs(
            handoff_configs=spec.handoff_configs,
            settings=settings,
            approval_callback=approval_callback,
            _loading_stack=_loading_stack,
            parent_spec=spec,
        )

    # Configure API key for the default client if provided.
    # Note: We do this unconditionally so that server/TUI paths (where a default
    # client may already exist) still receive the key from settings/.env.
    # If callers need a custom client, they should set it explicitly via
    # agents.set_default_openai_client() before running.
    # Resolve secrets provider — always goes through the abstraction.
    # Defaults to EnvSecretsProvider (wraps os.getenv) when not configured.
    from omniagents.core.providers.secrets.env import EnvSecretsProvider

    _secrets = EnvSecretsProvider()
    try:
        sec = getattr(spec, "security_config", None) or {}
        provider_cfg = (sec.get("providers") or {}).get("secrets")
        if provider_cfg:
            from omniagents.core.providers import resolve_provider

            _secrets = resolve_provider("secrets", provider_cfg)
    except Exception:
        pass

    def _get_secret(key: str) -> str | None:
        return _secrets.get_secret(key)

    api_key_from_settings = settings.get("openai_api_key")
    if not api_key_from_settings:
        api_key_from_settings = _get_secret("OPENAI_API_KEY")

    # Check for Azure configuration to apply header injection fix
    base_url = settings.get("openai_base_url") or _get_secret("OPENAI_BASE_URL")
    is_azure = base_url and (
        "openai.azure.com" in base_url or "services.ai.azure.com" in base_url
    )

    if api_key_from_settings and is_azure:
        logger.debug("Configuring Azure OpenAI client with header injection fix")
        try:
            from agents import AsyncOpenAI, set_default_openai_client

            # Create client with dummy key in constructor to avoid Bearer token,
            # and real key in headers to satisfy Azure requirements.
            client = AsyncOpenAI(
                api_key="dummy",
                base_url=base_url,
                default_headers={"api-key": api_key_from_settings},
            )
            set_default_openai_client(client, use_for_tracing=False)
        except ImportError:
            logger.warning(
                "Could not import AsyncOpenAI/set_default_openai_client from agents. "
                "Falling back to standard key configuration."
            )
            set_default_openai_key(api_key_from_settings, use_for_tracing=False)
    elif api_key_from_settings:
        logger.debug("Configuring default OpenAI key from settings/environment")
        set_default_openai_key(api_key_from_settings, use_for_tracing=False)
    else:
        if get_default_openai_client() is None:
            logger.debug("No API key found and no custom client configured")
        else:
            logger.debug("Custom client already set; no API key provided")

    # Note: We don't create a client here anymore. The SDK will handle client resolution
    # through its provider chain, which respects set_default_openai_client() if called

    # Build ModelSettings from model config, spec config, or defaults
    # Priority: agent YAML model_settings > model config model_settings > defaults
    # This allows model-level defaults (e.g., store=False + include for reasoning models)
    # to be overridden by agent-specific settings.
    model_settings_kwargs = {}

    # Start with model-level settings from session model_config (if present)
    # This allows models to define default settings (e.g., reasoning model requirements)
    if session_model_config and session_model_config.get("model_settings"):
        model_level_settings = session_model_config["model_settings"]
        if isinstance(model_level_settings, dict):
            model_settings_kwargs.update(model_level_settings)
            logger.debug(
                f"Applied model-level settings: {list(model_level_settings.keys())}"
            )

    # Override with agent-level settings from spec (if present)
    if spec.model_settings:
        # Deep merge for nested dicts like extra_body
        for key, value in spec.model_settings.items():
            if (
                key in model_settings_kwargs
                and isinstance(value, dict)
                and isinstance(model_settings_kwargs[key], dict)
            ):
                # Merge nested dicts (e.g., extra_body)
                model_settings_kwargs[key] = {**model_settings_kwargs[key], **value}
            else:
                model_settings_kwargs[key] = value

    # If tools are available and tool_choice not explicitly set, default to "auto"
    if tools and "tool_choice" not in model_settings_kwargs:
        model_settings_kwargs["tool_choice"] = "auto"

    # For gpt-5.x reasoning models, the SDK 0.16.0 defaults reasoning.effort to "none".
    # We default to "medium" instead unless explicitly configured, to preserve quality.
    if (
        isinstance(model_name, str)
        and model_name.startswith("gpt-5")
        and "reasoning" not in model_settings_kwargs
    ):
        from agents.model_settings import Reasoning

        model_settings_kwargs["reasoning"] = Reasoning(effort="medium")

    # Apply spec-level retry policy (SDK 0.16+) onto ModelSettings.retry.
    # Spec stores a pre-built ``ModelRetrySettings`` (resolved by the YAML
    # loader) so we just plug it in without further coercion.
    if (
        getattr(spec, "retry_settings", None) is not None
        and "retry" not in model_settings_kwargs
    ):
        model_settings_kwargs["retry"] = spec.retry_settings

    # Create ModelSettings from merged kwargs
    if model_settings_kwargs:
        model_settings = ModelSettings(**model_settings_kwargs)
    elif tools:
        # Default settings with tool_choice when tools are available
        model_settings = ModelSettings(tool_choice="auto")
    else:
        # Default settings without tools
        model_settings = ModelSettings()

    # Session-level reasoning effort override
    if session is not None:
        session_reasoning = getattr(session, "reasoning_effort", None)
        if session_reasoning:
            from agents.model_settings import Reasoning

            # Create a new ModelSettings with the reasoning override
            # We need to copy all existing settings and add/replace reasoning
            updated_kwargs = {}
            for field in [
                "temperature",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "tool_choice",
                "parallel_tool_calls",
                "truncation",
                "max_tokens",
                "reasoning",
                "store",
                "include_usage",
                "extra_body",
                "extra_headers",
                "extra_query",
            ]:
                val = getattr(model_settings, field, None)
                if val is not None:
                    updated_kwargs[field] = val
            # Override reasoning with session value
            updated_kwargs["reasoning"] = Reasoning(effort=session_reasoning)
            model_settings = ModelSettings(**updated_kwargs)
            logger.debug(f"Using session reasoning effort: {session_reasoning}")

    # Build the agent
    agent_kwargs: Dict[str, Any] = dict(
        name=agent_name,
        instructions=instructions,
        tools=tools,
        model=model_name,
        model_settings=model_settings,
        mcp_servers=mcp_servers_list,
        mcp_config=mcp_config,
        handoffs=handoffs,
        handoff_description=handoff_description,
        reset_tool_choice=reset_tool_choice,
        tool_use_behavior=tool_use_behavior,
        output_type=output_type,
        prompt=prompt,
        input_guardrails=input_guardrails,
        output_guardrails=output_guardrails,
    )
    if getattr(spec, "agent_hooks", None) is not None:
        agent_kwargs["hooks"] = spec.agent_hooks

    # Sandbox: when ``sandbox.use_sandbox_agent`` is true, build a
    # SandboxAgent (subclass of Agent) and forward the resolved
    # capabilities/manifest/run_as fields. The SDK accepts None for
    # capabilities (it falls back to Capabilities.default()), so we only
    # pass it through when the YAML configured an explicit list.
    use_sandbox_agent = bool(getattr(spec, "sandbox_use_sandbox_agent", False))
    if use_sandbox_agent:
        try:
            from agents.sandbox import SandboxAgent  # type: ignore

            sandbox_kwargs: Dict[str, Any] = {}
            sandbox_run_config = getattr(spec, "sandbox_run_config", None)
            default_manifest = (
                getattr(sandbox_run_config, "manifest", None)
                if sandbox_run_config is not None
                else None
            )
            if default_manifest is not None:
                sandbox_kwargs["default_manifest"] = default_manifest
            capabilities = getattr(spec, "sandbox_capabilities", None)
            if capabilities is not None:
                sandbox_kwargs["capabilities"] = capabilities
            run_as = getattr(spec, "sandbox_run_as", None)
            if run_as:
                sandbox_kwargs["run_as"] = run_as
            base_agent = SandboxAgent(**agent_kwargs, **sandbox_kwargs)
            return base_agent
        except Exception as e:
            logger.warning(
                "Failed to build SandboxAgent (falling back to Agent): %s", e
            )

    base_agent = Agent(**agent_kwargs)

    return base_agent
