"""Flet GUI backend for OmniAgents."""

from omniagents.core.interfaces.backend import Backend
from omniagents.core.agents.specs import AgentSpec


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
            print(f"Error: Flet dependencies not installed. Install with: pip install omniagents[flet]")
            print(f"Missing: {e}")
            import sys
            sys.exit(1)
        
        # Extract GUI-specific kwargs
        config_path = kwargs.get('config_path')
        debug = kwargs.get('debug', False)
        
        # Create AgentApp instance
        if config_path:
            app = AgentApp(config_path=config_path)
        else:
            app = AgentApp(spec=spec)
        
        # Override debug mode if specified
        if debug and debug != spec.debug_flag:
            print("Overriding debug mode: Setting to True based on command line argument.")
            app.spec.debug_flag = True
            if "(Debug Mode)" not in app.spec.page_title_suffix:
                app.spec.page_title_suffix += " (Debug Mode)"
        
        # Run the Flet app directly
        print(f"Starting Flet app for agent: {app.spec.name}")
        app.run()


__all__ = ['FletBackend']