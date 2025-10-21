"""Flet GUI backend for OmniAgents."""

from omniagents.core.interfaces.backend import Backend
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.debug import Debug


class FletBackend:
    """Flet GUI backend implementation."""

    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the Flet GUI backend with the given agent specification.

        Args:
            spec: The agent specification
            **kwargs: Additional backend-specific arguments:
                - config_path: Path to YAML config file
                - debug: Enable debug mode
        """
        # Import Flet dependencies only when needed
        try:
            import flet as ft
            from .app_base import AgentApp
        except ImportError as e:
            print(
                f"Error: Flet dependencies not installed. Install with: pip install omniagents[flet]"
            )
            print(f"Missing: {e}")
            import sys

            sys.exit(1)

        # Extract GUI-specific kwargs
        config_path = kwargs.get("config_path")
        debug = kwargs.get("debug", False)
        args = kwargs.get("args")

        # Create AgentApp instance
        if config_path:
            app = AgentApp(config_path=config_path)
        else:
            app = AgentApp(spec=spec)

        # Apply CLI overrides if args provided (strict flags)
        if args is not None:
            approvals = getattr(args, "approvals", None)
            if approvals == "skip":
                app.spec.use_safe_agent = False
            elif approvals == "auto":
                app.spec.use_safe_agent = True
                app.spec.safe_agent_options.skip_approvals = True
            elif approvals == "require":
                app.spec.use_safe_agent = True
                app.spec.safe_agent_options.skip_approvals = False
            on_reject = getattr(args, "on_reject", None)
            if on_reject == "halt":
                app.spec.safe_agent_options.halt_on_rejection = True
            elif on_reject == "continue":
                app.spec.safe_agent_options.halt_on_rejection = False

        # Initialize debug if not already initialized
        Debug.init(cli_flag=debug)

        # Update page title suffix if debug is enabled
        if Debug.enabled() and "(Debug Mode)" not in app.spec.page_title_suffix:
            app.spec.page_title_suffix += " (Debug Mode)"

        # Run the Flet app directly
        print(f"Starting Flet app for agent: {app.spec.name}")
        app.run()


__all__ = ["FletBackend"]
