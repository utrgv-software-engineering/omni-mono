"""Pluggable storage backends for omniagents session history.

The public API lives in ``omniagents.core.session.history_db`` — it picks a
backend at import time based on ``OMNIAGENTS_HISTORY_BACKEND`` and forwards
each module-level function call to the backend instance. This package owns
the abstract interface (``SessionStorageBackend``) and the concrete
implementations (``SqliteSessionStorage``, ``PgSessionStorage``).
"""

from .base import SessionStorageBackend

__all__ = ["SessionStorageBackend"]
