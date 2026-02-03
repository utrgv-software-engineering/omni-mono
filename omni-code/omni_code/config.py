import os
from pathlib import Path

# Project name - must match project.yml for consistency with omniagents MCP loading
PROJECT_NAME = "omni_code"
PROJECT_NAME_WINDOWS = "OmniCode"


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
