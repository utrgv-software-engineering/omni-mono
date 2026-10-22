"""Tracing configuration for omniagents.

Tracing is off by default. The ``studio`` provider records traces locally
through :mod:`omniagents.core.tracing.local`; OmniAgents Studio (the
separate ``omniagents-studio`` package) is the viewer for those databases.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def setup_tracing(tracing_config: Optional[Dict[str, Any]]) -> None:
    """Setup tracing based on configuration.

    Defaults to no tracing. Local recording is opt-in via provider: studio,
    and sending traces to OpenAI is opt-in via provider: openai.

    Args:
        tracing_config: Tracing configuration from YAML or None for defaults.
            Expected keys include:
            {
                'provider': 'none' | 'studio' | 'openai',
                'project': 'project_name',
                'name': 'trace_name',
                'settings': {
                    'resume': 'allow' | 'must' | 'never',
                    ...
                }
            }

            Providers:
            - none (default): Disable all tracing
            - studio: Local SQLite trace storage, viewable in OmniAgents Studio
            - openai: Opt-in to send traces to OpenAI servers
    """
    tracing_config = tracing_config or {}
    provider = (tracing_config.get("provider") or "none").lower()

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
