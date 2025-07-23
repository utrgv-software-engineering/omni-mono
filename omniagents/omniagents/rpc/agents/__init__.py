"""Reference agent implementations for the Remote Agent Protocol."""

from .remote import RemoteAgent
from .local import LocalAgent

__all__ = ["RemoteAgent", "LocalAgent"]