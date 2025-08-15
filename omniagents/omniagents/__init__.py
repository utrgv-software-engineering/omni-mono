"""OmniAgents - A wrapper around OpenAI's agents library."""

from ._version import __version__, __version_info__
from .core.runtime.runner import Runner
from .core import function_tool

__all__ = ["__version__", "__version_info__", "Runner", "function_tool"]