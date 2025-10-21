"""Tracing configuration for omniagents.

This module handles optional tracing integrations without requiring them as dependencies.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def setup_tracing(tracing_config: Optional[Dict[str, Any]]) -> None:
    """Setup tracing based on configuration.

    Defaults to OpenAI SDK's default behavior (sending traces to OpenAI).

    Args:
        tracing_config: Tracing configuration from YAML or None for defaults.
            Expected keys include:
            {
                'provider': 'default' | 'studio' | 'agentio' | 'none',
                'project': 'project_name',
                'name': 'trace_name',
                'settings': {
                    'resume': 'allow' | 'must' | 'never',
                    ...
                }
            }
    """
    if not tracing_config:
        # No tracing config - keep SDK's default behavior
        return

    provider = (tracing_config.get("provider") or "default").lower()

    if provider == "default":
        # Keep SDK's default behavior (traces go to OpenAI)
        logger.debug("Using default OpenAI tracing")
        return
    elif provider in {"studio", "agentio"}:
        tracing_module = None
        errors = []

        try:
            from omniagents.studio import tracing as studio_tracing

            tracing_module = studio_tracing
        except ImportError as exc:
            errors.append(exc)

        if tracing_module is None and provider == "agentio":
            try:
                import agentio as legacy_tracing

                tracing_module = legacy_tracing
            except ImportError as exc:
                errors.append(exc)

        if tracing_module is None:
            logger.warning("Studio tracing not available, using default OpenAI tracing")
            if errors:
                print(
                    "⚠️  OmniAgents Studio tracing is not installed. Install with: pip install omniagents[studio]"
                )
            return

        project = tracing_config.get("project", "omniagents")
        name = tracing_config.get("name")
        settings = tracing_config.get("settings", {})

        try:
            tracing_module.init(
                project=project,
                name=name,
                replace_default=True,
                **settings,
            )
            logger.info("Studio tracing enabled for project: %s", project)
        except Exception as exc:
            logger.error("Failed to initialize studio tracing: %s", exc)
            print(f"❌ Error initializing studio tracing: {exc}")
            print("    Falling back to default OpenAI tracing")

    elif provider == "none":
        try:
            from agents import set_trace_processors

            set_trace_processors([])
            logger.info("Tracing disabled")
            print("🔇 Tracing disabled")
        except ImportError as e:
            logger.error(f"Failed to disable tracing: {e}")
            print(f"❌ Error disabling tracing: {e}")

    else:
        logger.warning(f"Unknown tracing provider: {provider}")
        print(f"⚠️  Unknown tracing provider: {provider}")
        print("    Using default OpenAI tracing")
