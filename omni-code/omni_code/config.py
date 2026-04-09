import os
import shutil
from pathlib import Path

# Project name - must match project.yml for consistency with omniagents MCP loading
PROJECT_NAME = "omni_code"
PROJECT_NAME_WINDOWS = "OmniCode"


def container_runtime() -> str:
    """Return the container runtime binary name.

    Checks ``OMNI_CONTAINER_RUNTIME`` env var first (``docker`` or ``podman``),
    then falls back to ``docker``.  When set to ``podman``, the caller can use
    the standard OCI CLI which Podman implements 1-to-1 with Docker.
    """
    rt = os.environ.get("OMNI_CONTAINER_RUNTIME", "").strip().lower()
    if rt in ("podman", "docker"):
        return rt
    return "docker"


def container_runtime_available() -> bool:
    """Return True when the configured container runtime is on PATH."""
    return shutil.which(container_runtime()) is not None


def get_config_dir() -> Path:
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / PROJECT_NAME_WINDOWS
    override = os.getenv("XDG_CONFIG_HOME")
    if override:
        return Path(override) / PROJECT_NAME
    return Path.home() / ".config" / PROJECT_NAME
