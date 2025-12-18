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

# Import shared utilities from coding_agent
from omni_agents.coding_agent.context import format_agents_md_block


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
