"""Reference agent implementations for the Remote Agent Protocol."""

from .remote import RemoteAgent
from .local import LocalAgent
from .workers import WorkerManager, WorkerHandle

__all__ = ["RemoteAgent", "LocalAgent", "WorkerManager", "WorkerHandle"]
