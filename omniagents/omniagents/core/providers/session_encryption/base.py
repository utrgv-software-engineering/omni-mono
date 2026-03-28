"""Base session encryption provider interface."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Dict


class SessionEncryptionProvider(ABC):
    """Base class for session storage encryption.

    Controls how the SQLite connection for session history is created.
    Enterprise implementations can return an encrypted connection
    (e.g., via SQLCipher).
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    @abstractmethod
    def get_connection(self, db_path: str) -> sqlite3.Connection:
        """Return a ``sqlite3.Connection`` for *db_path*.

        The default implementation returns a standard unencrypted
        connection.  Enterprise providers override this to enable
        encryption at rest.
        """
        ...
