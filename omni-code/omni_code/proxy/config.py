"""Network proxy configuration loading.

Config sources (highest priority first):
1. OMNI_NETWORK_ALLOWLIST env var (comma-separated domains)
2. User config: ~/.config/omni_code/network.json
3. Project config: network: section in project.yml
4. Defaults (disabled)

User config overrides project config for any field that is set.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class NetworkConfig:
    """Configuration for the network isolation proxy."""

    enabled: bool = False
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)
    allow_private_ips: bool = False
    enable_socks5: bool = True
    http_port: int = 0  # 0 = ephemeral
    socks5_port: int = 0


def _get_config_dir() -> Path:
    """Get the user config directory (mirrors omni_code.config.get_config_dir)."""
    from omni_code.config import get_config_dir
    return get_config_dir()


def get_network_config_path() -> Path:
    """Get path to the user's network.json config file."""
    return _get_config_dir() / "network.json"


def load_network_config(project_yml_path: Optional[str] = None) -> NetworkConfig:
    """Load network config, merging user config over project defaults.

    Priority (highest first):
    1. OMNI_NETWORK_ALLOWLIST env var (comma-separated) — overrides everything
    2. User config (~/.config/omni_code/network.json) merged over project.yml
    3. network: section in project.yml
    4. Defaults (disabled)
    """
    # Check env var override first — takes absolute precedence
    env_allowlist = os.environ.get("OMNI_NETWORK_ALLOWLIST", "").strip()
    if env_allowlist:
        domains = [d.strip() for d in env_allowlist.split(",") if d.strip()]
        return NetworkConfig(
            enabled=True,
            allowlist=domains,
        )

    # Load project config as base
    project_data: Dict[str, Any] = {}
    if project_yml_path:
        project_data = _load_project_network_section(Path(project_yml_path))

    # Load user config as override
    user_data = _load_user_config()

    # Merge: user overrides project
    merged = _merge_configs(project_data, user_data)
    if not merged:
        return NetworkConfig()

    return NetworkConfig(
        enabled=bool(merged.get("enabled", False)),
        allowlist=_as_str_list(merged.get("allowlist")),
        denylist=_as_str_list(merged.get("denylist")),
        allow_private_ips=bool(merged.get("allow_private_ips", False)),
        enable_socks5=bool(merged.get("enable_socks5", True)),
        http_port=int(merged.get("http_port", 0)),
        socks5_port=int(merged.get("socks5_port", 0)),
    )


def load_user_network_config() -> Dict[str, Any]:
    """Load the raw user network config (for CLI display/editing)."""
    return _load_user_config()


def save_user_network_config(config: Dict[str, Any]) -> None:
    """Save network config to ~/.config/omni_code/network.json."""
    path = get_network_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(config, indent=2)

    if os.name == "nt":
        path.write_text(content, encoding="utf-8")
        return

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


# ------------------------------------------------------------------
# Internal loaders
# ------------------------------------------------------------------


def _load_user_config() -> Dict[str, Any]:
    """Load ~/.config/omni_code/network.json."""
    path = get_network_config_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _load_project_network_section(path: Path) -> Dict[str, Any]:
    """Parse the network: section from project.yml."""
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    network = data.get("network")
    if isinstance(network, dict):
        return dict(network)
    return {}


def _merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two config dicts. Override values win for scalar fields.

    For list fields (allowlist, denylist), override replaces base entirely
    when present (not appended), matching the models.json pattern.
    """
    if not base and not override:
        return {}
    merged = dict(base)
    for key, value in override.items():
        if value is not None:
            merged[key] = value
    return merged


def _as_str_list(val: object) -> list[str]:
    """Coerce a config value to a list of strings."""
    if val is None:
        return []
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip()]
    if isinstance(val, list):
        return [str(v) for v in val if v]
    return []
