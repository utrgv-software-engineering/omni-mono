import os
from pathlib import Path
from typing import Dict

from dotenv import dotenv_values


def get_config_dir() -> Path:
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "{config_dir_windows}"
    override = os.getenv("XDG_CONFIG_HOME")
    if override:
        return Path(override) / "{config_dir_unix}"
    return Path.home() / ".config" / "{config_dir_unix}"


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
    items = sorted((k, v) for k, v in values.items() if v)
    content = "\n".join(f"{key}={value}" for key, value in items)
    data = f"{content}\n" if content else ""
    if os.name == "nt":
        path.write_text(data, encoding="utf-8")
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(data)
