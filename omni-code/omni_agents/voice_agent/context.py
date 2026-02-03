"""
Context factory for the voice agent.

This module defines context factories that build dynamic context for
Jinja2 instruction templates.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict

from omniagents import context_factory
from omniagents import realtime_settings_resolver

# Import shared utilities from omni agent
from omni_agents.omni.context import format_agents_md_block


@context_factory
def build_voice_context(variables: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Build the instruction context for the voice agent.

    Args:
        variables: Dict with input variables from agent.yml.

    Returns:
        Dict with current_date, current_time, agents_md_block, etc.
    """
    variables = variables or {}

    now = datetime.now().astimezone()
    context: Dict[str, Any] = {
        "current_date": now.strftime("%Y-%m-%d"),
        "current_time": now.strftime("%H:%M %Z"),
        "agents_md_block": format_agents_md_block(),
    }

    # Merge any input variables into context
    context.update(variables)

    return context


@realtime_settings_resolver
def resolve_voice_realtime_settings() -> Dict[str, Any]:
    from omni_code.models import resolve_voice_settings_for_realtime

    return resolve_voice_settings_for_realtime()
