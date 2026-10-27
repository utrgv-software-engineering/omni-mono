"""Integrations: events from Slack (and, later, GitHub, Linear, Teams and
Azure DevOps) that start a project's automations in server mode."""

from .events import IncomingEvent
from .intake import EventIntake
from .matching import matching_automations, trigger_matches
from .store import EventStore

__all__ = [
    "EventIntake",
    "EventStore",
    "IncomingEvent",
    "matching_automations",
    "trigger_matches",
]
