import importlib
import sys

_module = importlib.import_module("omniagents.studio.tracing")

def __getattr__(name):
    return getattr(_module, name)

def __dir__():
    return sorted(set(dir(_module)))

__all__ = getattr(_module, "__all__", [])

for _submodule in ("api", "processor", "storage", "trace", "utils"):
    sys.modules[f"{__name__}.{_submodule}"] = importlib.import_module(f"omniagents.studio.tracing.{_submodule}")
