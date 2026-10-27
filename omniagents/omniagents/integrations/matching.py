"""Whether an automation's trigger matches an event.

A trigger names a ``provider`` and an ``event`` and may add a ``filter``.
Filter keys combine with AND, and an absent key matches anything. A key's
value is a list (the event matches when any of its values is listed), a
single value, or ``{not_in: [...]}`` (none of the event's values may be
listed). Values may be glob patterns (``api/**`` matches the paths under
``api/``). An event that carries no value for a filtered key does not match
it, unless the key is ``not_in``. A trigger with a ``schedule`` matches the
firings of that schedule only (by its ``name``).
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Iterable, List, Mapping

from omniagents.core.automations import Automation

from .events import IncomingEvent

__all__ = ["matching_automations", "trigger_matches"]


def trigger_matches(trigger: Mapping[str, Any], event: IncomingEvent) -> bool:
    if trigger.get("provider") != event.provider or trigger.get("event") != event.event:
        return False
    schedule = trigger.get("schedule")
    if isinstance(schedule, Mapping) and schedule.get("name"):
        if str(schedule["name"]) not in event.filters.get("schedules", []):
            return False
    for key, wanted in (trigger.get("filter") or {}).items():
        if not _filter_matches(wanted, event.filters.get(key, [])):
            return False
    return True


def _filter_matches(wanted: Any, values: List[str]) -> bool:
    if isinstance(wanted, Mapping):
        if "not_in" in wanted:
            return not _any_match(_as_list(wanted["not_in"]), values)
        if "in" in wanted:
            return _any_match(_as_list(wanted["in"]), values)
        return False
    return _any_match(_as_list(wanted), values)


def _any_match(patterns: List[str], values: List[str]) -> bool:
    return any(
        fnmatchcase(str(value), pattern) for pattern in patterns for value in values
    )


def _as_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def matching_automations(
    automations: Iterable[Automation], event: IncomingEvent
) -> List[Automation]:
    """The automations with at least one trigger matching ``event``."""
    return [
        a for a in automations if any(trigger_matches(t, event) for t in a.triggers)
    ]
