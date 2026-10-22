"""Tracing configuration for omniagents.

The ``studio`` provider (the default) records traces locally through
:mod:`omniagents.core.tracing.local`; OmniAgents Studio (the separate
``omniagents-studio`` package) is the viewer for those databases.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def setup_tracing(tracing_config: Optional[Dict[str, Any]]) -> None:
    """Setup tracing based on configuration.

    Defaults to local trace recording (no data sent externally).
    Users must explicitly opt-in to OpenAI tracing via provider: openai.

    Args:
        tracing_config: Tracing configuration from YAML or None for defaults.
            Expected keys include:
            {
                'provider': 'studio' | 'openai' | 'none' | 'default',
                'project': 'project_name',
                'name': 'trace_name',
                'settings': {
                    'resume': 'allow' | 'must' | 'never',
                    ...
                }
            }

            Providers:
            - studio (default): Local SQLite trace storage, viewable in
              OmniAgents Studio
            - default: Same as studio
            - openai: Opt-in to send traces to OpenAI servers
            - none: Disable all tracing
    """
    # Default to studio tracing if no config provided
    if not tracing_config:
        tracing_config = {"provider": "studio"}

    provider = (tracing_config.get("provider") or "studio").lower()

    # Map "default" to "studio"
    if provider == "default":
        provider = "studio"

    if provider == "openai":
        # User explicitly opted in to OpenAI tracing
        logger.info("OpenAI tracing enabled (provider: openai)")
        return
    elif provider == "studio":
        from . import local

        # Convention: the tracing project is the product slug. Project
        # manifests get this filled by the loader; this fallback covers
        # bare (non-project) runs.
        from omniagents.core.product import product_info

        project = tracing_config.get("project", product_info().slug)
        name = tracing_config.get("name")
        settings = tracing_config.get("settings", {})

        try:
            local.init(
                project=project,
                name=name,
                replace_default=True,
                **settings,
            )
            logger.info("Local tracing enabled for project: %s", project)
        except Exception as exc:
            logger.error("Failed to initialize local tracing: %s", exc, exc_info=True)
            print(f"❌ Error initializing local tracing: {exc}")
            print("    Tracing disabled (no data sent to OpenAI)")
            # Disable tracing on error instead of falling back to OpenAI
            try:
                from agents import set_trace_processors

                set_trace_processors([])
            except ImportError:
                pass

    elif provider == "none":
        try:
            from agents import set_trace_processors

            set_trace_processors([])
            logger.info("Tracing disabled")
            print("🔇 Tracing disabled")
        except ImportError as e:
            logger.error(f"Failed to disable tracing: {e}", exc_info=True)
            print(f"❌ Error disabling tracing: {e}")

    else:
        logger.warning(f"Unknown tracing provider: {provider}")
        print(f"⚠️  Unknown tracing provider: {provider}")
        print("    Tracing disabled (no data sent to OpenAI)")
        # Disable tracing for unknown providers instead of falling back to OpenAI
        try:
            from agents import set_trace_processors

            set_trace_processors([])
        except ImportError:
            pass
