"""
Context factory for the voice agent.

This module defines context factories that build dynamic context for
Jinja2 instruction templates.
"""

from __future__ import annotations

from typing import Any, Dict

from omniagents import context_factory
from omniagents import model_config_resolver
from omniagents import realtime_settings_resolver

from omni_code.agents.omni.context import build_coding_context


@context_factory
def build_voice_context(variables: Dict[str, Any] = None) -> Dict[str, Any]:
    """The voice agent shares the coding agent's context (date/time,
    AGENTS.md discovery, available skills) — same factory, no drift."""
    return build_coding_context(variables)


@realtime_settings_resolver
def resolve_voice_realtime_settings() -> Dict[str, Any]:
    from omni_code.models import resolve_voice_settings_for_realtime

    return resolve_voice_settings_for_realtime()


@model_config_resolver
def resolve_default_model() -> Dict[str, Any] | None:
    from omni_code.agents.omni.context import resolve_default_model as resolve

    return resolve()
