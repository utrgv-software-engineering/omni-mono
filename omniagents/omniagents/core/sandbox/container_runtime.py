"""Container-runtime (docker/podman) selection.

Products that shell out to an OCI CLI (``docker`` / ``podman``) need a
single answer to "which binary?" that honors operator configuration.
Podman implements the Docker CLI 1-to-1, so once the binary name is
resolved every call site can stay runtime-agnostic.

The framework env var is ``OMNIAGENTS_CONTAINER_RUNTIME``. Products can
prepend their own env var(s) via ``env_vars`` so their operators keep a
product-branded knob (omni-code uses ``OMNI_CONTAINER_RUNTIME``)::

    container_runtime(env_vars=("OMNI_CONTAINER_RUNTIME", DEFAULT_RUNTIME_ENV_VAR))

Note this is a CLI-binary concept. The docker *sandbox backend*
(``factory.py`` / ``docker_extended.py``) talks to an engine API socket
through the docker SDK (``docker.from_env()``), which honors
``DOCKER_HOST`` — pointing that at Podman's docker-compatible socket is
how the SDK path targets Podman. The two knobs are deliberately
independent.

Lifted from omni-code's ``omni_code/config.py``.
"""

from __future__ import annotations

import os
import shutil
from typing import Sequence

__all__ = [
    "DEFAULT_RUNTIME_ENV_VAR",
    "KNOWN_CONTAINER_RUNTIMES",
    "container_runtime",
    "container_runtime_available",
]

DEFAULT_RUNTIME_ENV_VAR = "OMNIAGENTS_CONTAINER_RUNTIME"

# Runtimes whose CLIs are interchangeable for our purposes.
KNOWN_CONTAINER_RUNTIMES = ("docker", "podman")


def container_runtime(
    *,
    env_vars: Sequence[str] = (DEFAULT_RUNTIME_ENV_VAR,),
    default: str = "docker",
) -> str:
    """Return the container runtime binary name (``docker`` or ``podman``).

    Checks each env var in *env_vars* in order and returns the first
    recognized value (case-insensitive, whitespace-stripped). Unset or
    unrecognized values are skipped; *default* is returned when nothing
    matches.

    Products with their own operator knob pass it first::

        container_runtime(env_vars=("OMNI_CONTAINER_RUNTIME",
                                    DEFAULT_RUNTIME_ENV_VAR))
    """
    for var in env_vars:
        rt = os.environ.get(var, "").strip().lower()
        if rt in KNOWN_CONTAINER_RUNTIMES:
            return rt
    return default


def container_runtime_available(
    *,
    env_vars: Sequence[str] = (DEFAULT_RUNTIME_ENV_VAR,),
    default: str = "docker",
) -> bool:
    """Return True when the configured container runtime is on PATH."""
    return (
        shutil.which(container_runtime(env_vars=env_vars, default=default)) is not None
    )
