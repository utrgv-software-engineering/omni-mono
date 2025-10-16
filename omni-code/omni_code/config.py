import os
from pathlib import Path
from typing import Dict

from dotenv import dotenv_values

GEMFURY_INDEX_URL = "https://REDACTED_FURY_TOKEN:@pypi.fury.io/ericmichael/"


def get_config_dir() -> Path:
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OmniCode"
    override = os.getenv("XDG_CONFIG_HOME")
    if override:
        return Path(override) / "omni-code"
    return Path.home() / ".config" / "omni-code"


def get_config_path() -> Path:
    return get_config_dir() / "config.env"


def load_config() -> Dict[str, str]:
    path = get_config_path()
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def write_config(values: Dict[str, str]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_items = sorted((k, v) for k, v in values.items() if v)
    content = "\n".join(f"{key}={value}" for key, value in sorted_items)
    data = f"{content}\n" if content else ""
    if os.name == "nt":
        path.write_text(data, encoding="utf-8")
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(data)
