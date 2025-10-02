"""OmniAgents - A wrapper around OpenAI's agents library."""

try:
    from ._version import __version__, __version_info__
except Exception:
    __version__ = "0.0.0"
    __version_info__ = (0, 0, 0)

try:
    from .core.runtime.runner import Runner  # type: ignore
except Exception:
    Runner = None  # type: ignore

try:
    from .core import function_tool  # type: ignore
except Exception:
    function_tool = None  # type: ignore

try:
    from .core import server_function  # type: ignore
except Exception:
    server_function = None  # type: ignore

try:
    from .core import input_guardrail, output_guardrail, GuardrailFunctionOutput  # type: ignore
except Exception:
    input_guardrail = None  # type: ignore
    output_guardrail = None  # type: ignore
    GuardrailFunctionOutput = None  # type: ignore

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
