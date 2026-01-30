"""OmniAgents - A wrapper around OpenAI's agents library."""

import importlib
import sys

try:
    from ._version import __version__, __version_info__
except Exception:
    __version__ = "0.0.0"
    __version_info__ = (0, 0, 0)

# Lazy imports to prevent early initialization of agents library
_LAZY_IMPORTS = {
    "Runner": ".core.runtime.runner",
    "function_tool": ".core",
    "server_function": ".core",
    "runtime_hook": ".core",
    "input_guardrail": ".core",
    "output_guardrail": ".core",
    "GuardrailFunctionOutput": ".core",
    "context_factory": ".core.context",
    "model_config_resolver": ".core.context",
}


def __getattr__(name):
    """Lazy import attributes to avoid early initialization."""
    if name in _LAZY_IMPORTS:
        module_path = _LAZY_IMPORTS[name]
        try:
            module = importlib.import_module(module_path, package="omniagents")
            return getattr(module, name)
        except Exception:
            return None
    # Re-export Agent from the underlying agents library
    if name == "Agent":
        from agents import Agent

        return Agent
    raise AttributeError(f"module 'omniagents' has no attribute '{name}'")


__all__ = [
    "__version__",
    "__version_info__",
    "Agent",
    "Runner",
    "function_tool",
    "server_function",
    "runtime_hook",
    "input_guardrail",
    "output_guardrail",
    "GuardrailFunctionOutput",
    "context_factory",
    "model_config_resolver",
]


try:
    from .patches import disable_token_streaming as _no_token_stream_patch  # noqa: F401
except Exception:
    pass
