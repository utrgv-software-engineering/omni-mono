"""Unified debug configuration for OmniAgents."""

import os


class Debug:
    """Simple unified debug state for OmniAgents."""

    _enabled: bool = False

    @classmethod
    def init(cls, cli_flag: bool = False, yaml_config: bool = False):
        """Initialize debug state once at startup.

        Priority: CLI flag > YAML config > Environment variable

        Args:
            cli_flag: Debug flag from command line arguments
            yaml_config: Debug flag from YAML configuration
        """
        # Priority: CLI > YAML > Environment
        cls._enabled = (
            cli_flag
            or yaml_config
            or os.getenv("OMNIAGENTS_DEBUG", "").lower() in ("1", "true")
        )

        # If enabled via CLI, set env var for child processes
        if cli_flag and cls._enabled:
            os.environ["OMNIAGENTS_DEBUG"] = "1"

    @classmethod
    def enabled(cls) -> bool:
        """Check if debug is enabled."""
        return cls._enabled

    @classmethod
    def set_enabled(cls, enabled: bool):
        """Manually set debug state (useful for testing)."""
        cls._enabled = enabled
        if enabled:
            os.environ["OMNIAGENTS_DEBUG"] = "1"
        else:
            os.environ.pop("OMNIAGENTS_DEBUG", None)
