from pathlib import Path

from omni_code import identity  # noqa: F401  — installs the product identity

# Product-branded knob first, framework knob second. Kept explicit: the
# operator knob is the short OMNI_* brand, not the product env prefix
# (OMNI_CODE_*), and the framework's container-runtime seam takes a
# literal env-var list.
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
    """omni-code's per-user config dir, from the installed product identity."""
    from omniagents.core.config import product_config_dir

    return product_config_dir()
