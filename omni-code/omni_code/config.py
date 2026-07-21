from pathlib import Path

# Project name - must match project.yml for consistency with omniagents MCP loading
PROJECT_NAME = "omni_code"
PROJECT_NAME_WINDOWS = "OmniCode"

# Product-branded knob first, framework knob second.
_CONTAINER_RUNTIME_ENV_VARS = (
    "OMNI_CONTAINER_RUNTIME",
    "OMNIAGENTS_CONTAINER_RUNTIME",
)


def container_runtime() -> str:
    """Return the container runtime binary name (``docker`` or ``podman``).

    Delegates to the framework's selector, honoring omni-code's
    ``OMNI_CONTAINER_RUNTIME`` env var before the framework-wide one.
    """
    from omniagents.core.sandbox.container_runtime import container_runtime as _rt

    return _rt(env_vars=_CONTAINER_RUNTIME_ENV_VARS)


def container_runtime_available() -> bool:
    """Return True when the configured container runtime is on PATH."""
    from omniagents.core.sandbox.container_runtime import (
        container_runtime_available as _available,
    )

    return _available(env_vars=_CONTAINER_RUNTIME_ENV_VARS)


def get_config_dir() -> Path:
    from omniagents.core.config import product_config_dir

    return product_config_dir(PROJECT_NAME, windows_name=PROJECT_NAME_WINDOWS)
