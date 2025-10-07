import importlib
import sys

_module = importlib.import_module("omniagents.studio.tracing")

def __getattr__(name):
    return getattr(_module, name)

def __dir__():
    return sorted(set(dir(_module)))

__all__ = getattr(_module, "__all__", [])

_SUBMODULE_MAP = {
    "api": "omniagents.studio.server.api",
    "processor": "omniagents.studio.tracing.processor",
    "storage": "omniagents.studio.tracing.storage",
    "trace": "omniagents.studio.tracing.trace",
    "utils": "omniagents.studio.tracing.utils",
}

for _alias, _target in _SUBMODULE_MAP.items():
    sys.modules[f"{__name__}.{_alias}"] = importlib.import_module(_target)
