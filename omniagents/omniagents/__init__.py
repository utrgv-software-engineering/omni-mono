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
    "input_guardrail": ".core",
    "output_guardrail": ".core",
    "GuardrailFunctionOutput": ".core",
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
    raise AttributeError(f"module 'omniagents' has no attribute '{name}'")


def _register_agentio_alias() -> None:
    try:
        agentio_module = importlib.import_module("omniagents.agentio")
    except Exception:
        return
    sys.modules["agentio"] = agentio_module
    submodules = getattr(agentio_module, "_SUBMODULE_MAP", {})
    for alias, target in submodules.items():
        try:
            sys.modules[f"agentio.{alias}"] = importlib.import_module(target)
        except Exception:
            continue


_register_agentio_alias()

__all__ = [
    "__version__",
    "__version_info__",
    "Runner",
    "function_tool",
    "server_function",
    "input_guardrail",
    "output_guardrail",
    "GuardrailFunctionOutput",
]


# Defer patch import to prevent early initialization
# This will be applied if/when the agents library is actually used
# try:
#     from .patches import agents_stream_resume as _stream_resume_patch  # noqa: F401
# except Exception:
#     pass
